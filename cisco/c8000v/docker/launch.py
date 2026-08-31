#!/usr/bin/env python3

import datetime
import logging
import os
import re
import signal
import subprocess
import sys

import vrnetlab

STARTUP_CONFIG_FILE = "/config/startup-config.cfg"


def handle_SIGCHLD(signal, frame):
    os.waitpid(-1, os.WNOHANG)


def handle_SIGTERM(signal, frame):
    sys.exit(0)


signal.signal(signal.SIGINT, handle_SIGTERM)
signal.signal(signal.SIGTERM, handle_SIGTERM)
signal.signal(signal.SIGCHLD, handle_SIGCHLD)

TRACE_LEVEL_NUM = 9
logging.addLevelName(TRACE_LEVEL_NUM, "TRACE")


def trace(self, message, *args, **kws):
    # Yes, logger takes its '*args' as 'args'.
    if self.isEnabledFor(TRACE_LEVEL_NUM):
        self._log(TRACE_LEVEL_NUM, message, args, **kws)


logging.Logger.trace = trace


class C8000v_vm(vrnetlab.VM):
    def __init__(self, hostname, username, password, conn_mode, install_mode=False):
        disk_image = None

        for e in sorted(os.listdir("/")):
            if not disk_image and re.search(".qcow2$", e):
                disk_image = "/" + e
            if re.search(r"\.license$", e):
                os.rename("/" + e, "/tftpboot/license.lic")

        self.license = False
        if os.path.isfile("/tftpboot/license.lic"):
            logger.info("License found")
            self.license = True

        super().__init__(username, password, disk_image=disk_image, ram=4096)
        self.install_mode = install_mode
        self.hostname = hostname
        self.conn_mode = conn_mode
        self.num_nics = 9
        self.nic_type = "vmxnet3"
        self.image_name = "config.iso"
        self.mode = os.environ.get("MODE", "autonomous")

        if self.install_mode:
            self.logger.debug("Install mode")
            if self.mode == "controller":
                # Controller mode uses serial-enabled images, no install needed
                self.logger.info(
                    "Controller mode: skipping install, creating empty config ISO"
                )
                self.create_config_image("", install=True)
            else:
                # Autonomous mode needs install to set serial console and license level
                self.logger.info("Autonomous mode: generating install config")
                cfg = self.gen_install_config()
                self.create_config_image(cfg, install=True)
        else:
            if os.path.exists(STARTUP_CONFIG_FILE):
                self.logger.info("Startup configuration file found")
                with open(STARTUP_CONFIG_FILE, "r") as startup_config:
                    startup_cfg = startup_config.read()
                # If startup config is already MIME-wrapped, use it as-is
                if "MIME-Version:" in startup_cfg:
                    self.logger.info("MIME-wrapped config detected, using as-is")
                    cfg = startup_cfg
                else:
                    # Otherwise, append to bootstrap config
                    cfg = self.gen_bootstrap_config() + startup_cfg
            else:
                self.logger.warning("User provided startup configuration is not found.")
                cfg = self.gen_bootstrap_config()
            self.create_config_image(cfg, install=False)

        self.qemu_args.extend(["-cdrom", "/" + self.image_name])

    def gen_install_config(self) -> str:
        """
        Returns the configuration to load in install mode
        """

        config = ""

        if self.license:
            config += """do clock set 13:33:37 1 Jan 2010
interface GigabitEthernet1
ip address 10.0.0.15 255.255.255.0
no shut
exit
license accept end user agreement
yes
do license install tftp://10.0.0.2/license.lic
"""

        config += """
license boot level network-premier addon dna-premier
platform console serial
do clear platform software vnic-if nvtable
do wr
do reload
"""

        return config

    def gen_bootstrap_config(self) -> str:
        """
        Returns the system bootstrap configuration
        """

        v4_mgmt_address = vrnetlab.cidr_to_ddn(self.mgmt_address_ipv4)
        ipv6_address = self.render_optional_mgmt_config(
            "ipv6 address {address}", address=self.mgmt_address_ipv6
        )
        is_autonomous = self.mode != "controller"

        # Controller mode uses VRF 513, autonomous mode uses clab-mgmt
        if self.mode == "controller":
            vrf_name = "513"
            vrf_description = (
                "Initial containerlab management VRF (Replace with 512 on provisioning)"
            )
        else:
            vrf_name = "clab-mgmt"
            vrf_description = "Containerlab management VRF (DO NOT DELETE)"

        ipv6_route = self.render_optional_mgmt_config(
            "ipv6 route vrf {vrf} ::/0 {gateway}",
            vrf=vrf_name,
            gateway=self.mgmt_gw_ipv6,
        )

        # Build autonomous-specific config sections
        crypto_key = "crypto key generate rsa modulus 2048\n!" if is_autonomous else "!"
        login_local = "login local" if is_autonomous else "!"

        config = f"""hostname {self.hostname}
username {self.username} privilege 15 password {self.password}
ip domain name example.com
!
{crypto_key}
line con 0
logging synchronous
!
line vty 0 4
logging synchronous
{login_local}
transport input all
!
ipv6 unicast-routing
!
vrf definition {vrf_name}
description {vrf_description}
address-family ipv4
exit
address-family ipv6
exit
exit
!
ip route vrf {vrf_name} 0.0.0.0 0.0.0.0 {self.mgmt_gw_ipv4}
{ipv6_route}
!
interface GigabitEthernet 1
description Containerlab management interface
vrf forwarding {vrf_name}
ip address {v4_mgmt_address[0]} {v4_mgmt_address[1]}
{ipv6_address}
no shut
exit
!
restconf
netconf-yang
netconf max-sessions 16
netconf detailed-error
!
ip ssh server algorithm mac hmac-sha2-512
ip ssh maxstartups 128
!
"""

        return config

    def create_config_image(self, config, install=False):
        """Creates a iso image with a installation configuration"""

        # Determine config filename based on install mode and MODE
        if install:
            config_filename = "/iosxe_config.txt"
        elif self.mode == "controller":
            config_filename = "/ciscosdwan_cloud_init.cfg"
        else:
            config_filename = "/iosxe_config.txt"

        # For controller mode bootstrap, wrap config in MIME structure if not already present
        if self.mode == "controller" and not install and "MIME-Version:" not in config:
            # Indent the config (add 2 spaces to each line)
            indented_config = "\n".join("  " + line for line in config.split("\n"))

            config = f"""Content-Type: multipart/mixed; boundary="===================================="
MIME-Version: 1.0
--====================================
#cloud-config
vinitparam:
 - uuid : C8K-00000000-0000-0000-0000-000000000000
 - otp : 00000000000000000000000000000000
 - vbond : 0.0.0.0
 - org : null
--====================================
#cloud-boothook
{indented_config}
--====================================--
"""

        with open(config_filename, "w") as cfg:
            cfg.write(config)

        genisoimage_args = [
            "genisoimage",
            "-l",
            "-o",
            "/" + self.image_name,
            config_filename,
        ]

        self.logger.debug("Generating boot ISO")
        subprocess.Popen(genisoimage_args).wait()

    def bootstrap_spin(self):
        """This function should be called periodically to do work."""

        # Controller mode install is a no-op (serial-enabled images)
        if self.install_mode and self.mode == "controller":
            if not self.running:
                self.logger.info(
                    "Controller mode install: marking as complete immediately"
                )
                self.running = True
            return

        if self.spins > 300:
            # too many spins with no result ->  give up
            self.stop()
            self.start()
            return

        # Different expectations for autonomous vs controller mode
        if self.mode == "controller":
            (ridx, match, res) = self.con_expect(
                [
                    b"CVAC-4-CONFIG_DONE",
                    b"IOSXEBOOT-4-FACTORY_RESET",
                    b"All daemons up",
                    b"vip-bootstrap: All daemons up",
                    b"Error extracting config",
                ]
            )
        else:
            (ridx, match, res) = self.con_expect(
                [b"CVAC-4-CONFIG_DONE", b"IOSXEBOOT-4-FACTORY_RESET"]
            )

        if match:  # got a match!
            if ridx == 0 and not self.install_mode:  # configuration applied
                self.logger.info("CVAC Configuration has been applied.")
                # close telnet connection
                self.scrapli_tn.close()
                # startup time?
                startup_time = datetime.datetime.now() - self.start_time
                self.logger.info("Startup complete in: %s", startup_time)
                # mark as running
                self.running = True
                return
            elif ridx == 1:  # IOSXEBOOT-4-FACTORY_RESET
                if self.install_mode:
                    install_time = datetime.datetime.now() - self.start_time
                    self.logger.info("Install complete in: %s", install_time)
                    self.running = True
                    return
                else:
                    self.logger.warning("Unexpected reload while running")
            elif self.mode == "controller" and (
                ridx == 2 or ridx == 3
            ):  # Controller mode daemons up
                self.logger.info(
                    "Controller mode configuration complete - all daemons up."
                )
                # close telnet connection
                self.scrapli_tn.close()
                # startup time?
                startup_time = datetime.datetime.now() - self.start_time
                self.logger.info(
                    "Controller mode startup complete in: %s", startup_time
                )
                # mark as running
                self.running = True
                return
            elif (
                self.mode == "controller" and ridx == 4
            ):  # Controller mode config error
                self.logger.error(
                    "Controller mode configuration failed - error extracting config"
                )
                self.logger.error(
                    "Router may need manual intervention or config correction"
                )
                # Don't mark as running, let it continue trying or timeout

        # no match, if we saw some output from the router it's probably
        # booting, so let's give it some more time
        if res != b"":
            self.write_to_stdout(res)
            # reset spins if we saw some output
            self.spins = 0

        self.spins += 1

        return


class C8000v(vrnetlab.VR):
    def __init__(self, hostname, username, password, conn_mode):
        super(C8000v, self).__init__(username, password)
        self.vms = [C8000v_vm(hostname, username, password, conn_mode)]


class C8000v_installer(C8000v):
    """C8000v installer

    Will start the C8000v with a mounted iso to make sure that we get
    console output on serial, not vga.
    """

    def __init__(self, hostname, username, password, conn_mode):
        super(C8000v, self).__init__(username, password)
        self.vms = [
            C8000v_vm(hostname, username, password, conn_mode, install_mode=True)
        ]

    def install(self):
        self.logger.info("Installing C8000v")
        cat8kv = self.vms[0]
        while not cat8kv.running:
            cat8kv.work()
        cat8kv.stop()
        self.logger.info("Installation complete")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="")
    parser.add_argument(
        "--trace", action="store_true", help="enable trace level logging"
    )
    parser.add_argument("--username", default="vrnetlab", help="Username")
    parser.add_argument("--password", default="VR-netlab9", help="Password")
    parser.add_argument("--install", action="store_true", help="Install C8000v")
    parser.add_argument("--hostname", default="c8000v", help="Router hostname")
    parser.add_argument(
        "--connection-mode",
        default="vrxcon",
        help="Connection mode to use in the datapath",
    )
    args = parser.parse_args()

    LOG_FORMAT = "%(asctime)s: %(module)-10s %(levelname)-8s %(message)s"
    logging.basicConfig(format=LOG_FORMAT)
    logger = logging.getLogger()

    logger.setLevel(logging.DEBUG)
    if args.trace:
        logger.setLevel(1)

    if args.install:
        vr = C8000v_installer(
            args.hostname, args.username, args.password, args.connection_mode
        )
        vr.install()
    else:
        vr = C8000v(args.hostname, args.username, args.password, args.connection_mode)
        vr.start()
