#!/usr/bin/env python3

import datetime
import logging
import os
import random
import re
import signal
import string
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


class cat9kv_vm(vrnetlab.VM):
    def __init__(self, hostname, username, password, conn_mode, vcpu, ram):
        disk_image = None
        for e in sorted(os.listdir("/")):
            if not disk_image and re.search(".qcow2$", e):
                disk_image = "/" + e

        super().__init__(
            username,
            password,
            disk_image=disk_image,
            smp=f"cores={vcpu},threads=1,sockets=1",
            ram=ram,
            min_dp_nics=8,
            use_scrapli=True,
        )
        self.hostname = hostname
        self.conn_mode = conn_mode
        self.num_nics = 9
        self.nic_type = "virtio-net-pci"

        self.image_name = "config.img"

        self.qemu_args.extend(
            [
                "-overcommit mem-lock=off",
                f"-boot order=cd -cdrom /{self.image_name}",
            ]
        )

        # create .img which is mounted for startup config and contains ASIC emulation in 'conf/vswitch.xml' dir.
        self.create_boot_image()

    def create_boot_image(self):
        """Creates a iso image with a bootstrap configuration"""
        try:
            os.makedirs("/img_dir/conf")
        except:
            self.logger.error(
                "Unable to make '/img_dir'. Does the directory already exist?"
            )

        try:
            # Load vswitch.xml and randomize serial number
            if os.path.exists("/vswitch.xml"):
                with open("/vswitch.xml", "r") as f:
                    vswitch_content = f.read()

                random_serial = ''.join(random.choices(string.ascii_uppercase + string.digits, k=7))

                vswitch_content = re.sub(
                    r'<prod_serial_number>.*?</prod_serial_number>',
                    f'<prod_serial_number>{random_serial}</prod_serial_number>',
                    vswitch_content
                    )

                with open("/img_dir/conf/vswitch.xml", "w") as f:
                    f.write(vswitch_content)

                self.logger.info(f"Generated vswitch.xml with randomized serial number: {random_serial}")
            else:
                self.logger.debug("No vswitch.xml file provided.")
        except Exception as e:
            self.logger.error(f"Error processing vswitch.xml: {e}")

        v4_mgmt_address = vrnetlab.cidr_to_ddn(self.mgmt_address_ipv4)

        cat9kv_config = f"""hostname {self.hostname}
username {self.username} privilege 15 password {self.password}
ip domain name example.com
no ip domain lookup
!
crypto key generate rsa modulus 2048
!
line con 0
logging synchronous
!
line vty 0 4
logging synchronous
login local
transport input all
!
ip route vrf Mgmt-vrf 0.0.0.0 0.0.0.0 {self.mgmt_gw_ipv4}
ipv6 route vrf Mgmt-vrf ::/0 {self.mgmt_gw_ipv6}
!
interface GigabitEthernet0/0
description Containerlab management interface
ip address {v4_mgmt_address[0]} {v4_mgmt_address[1]}
ipv6 address {self.mgmt_address_ipv6}
no shut
exit
!
restconf
netconf-yang
netconf max-sessions 16
netconf detailed-error
!
ip ssh server algorithm mac hmac-sha2-512
!
"""

        if os.path.exists(STARTUP_CONFIG_FILE):
            self.logger.info("Startup configuration file found")
            with open(STARTUP_CONFIG_FILE, "r") as startup_config:
                cat9kv_config += startup_config.read()
        else:
            self.logger.warning(f"User provided startup configuration is not found.")

        with open("/img_dir/iosxe_config.txt", "w") as cfg_file:
            cfg_file.write(cat9kv_config)

        genisoimage_args = [
            "genisoimage",
            "-l",
            "-o",
            "/" + self.image_name,
            "/img_dir",
        ]

        self.logger.debug("Generating boot ISO")
        subprocess.Popen(genisoimage_args).wait()

    def bootstrap_spin(self):
        """This function should be called periodically to do work."""

        if self.spins > 300:
            # too many spins with no result ->  give up
            self.stop()
            self.start()
            return

        (ridx, match, res) = self.con_expect(
            [
                b"CVAC-4-CONFIG_DONE",
                b"IOSXEBOOT-4-FACTORY_RESET",
            ],
        )
        if match:  # got a match!
            if ridx == 0:  # configuration applied
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
                self.logger.warning("Unexpected reload while running")

        # no match, if we saw some output from the router it's probably
        # booting, so let's give it some more time
        if res != b"":
            self.write_to_stdout(res)
            # reset spins if we saw some output
            self.spins = 0

        self.spins += 1

        return


class cat9kv(vrnetlab.VR):
    def __init__(self, hostname, username, password, conn_mode, vcpu, ram):
        super(cat9kv, self).__init__(username, password)
        self.vms = [cat9kv_vm(hostname, username, password, conn_mode, vcpu, ram)]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="")
    parser.add_argument(
        "--trace", action="store_true", help="enable trace level logging"
    )
    parser.add_argument("--username", default="vrnetlab", help="Username")
    parser.add_argument("--password", default="VR-netlab9", help="Password")
    parser.add_argument("--hostname", default="", help="Router hostname")
    parser.add_argument(
        "--connection-mode",
        default="vrxcon",
        help="Connection mode to use in the datapath",
    )
    parser.add_argument("--vcpu", type=int, default=4, help="Allocated vCPUs")
    parser.add_argument("--ram", type=int, default=18432, help="Allocaetd RAM in MB")

    args = parser.parse_args()

    LOG_FORMAT = "%(asctime)s: %(module)-10s %(levelname)-8s %(message)s"
    logging.basicConfig(format=LOG_FORMAT)
    logger = logging.getLogger()

    logger.setLevel(logging.DEBUG)
    if args.trace:
        logger.setLevel(1)

    # Auto-detect hostname from image filename if not provided
    if not args.hostname:
        for e in os.listdir("/"):
            if re.search(r"\.qcow2$", e):
                if re.search(r"c9800", e, re.IGNORECASE):
                    args.hostname = "c9800cl"
                else:
                    args.hostname = "cat9kv"
                break
        if not args.hostname:
            args.hostname = "cat9kv"

    vr = cat9kv(
        args.hostname,
        args.username,
        args.password,
        args.connection_mode,
        args.vcpu,
        args.ram,
    )
    vr.start()
