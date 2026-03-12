#!/usr/bin/env python3

import datetime
import logging
import os
import re
import signal
import sys

import vrnetlab
from scrapli.driver.core import IOSXRDriver

STARTUP_CONFIG_FILE = "/config/startup-config.cfg"

# OVMF for UEFI boot major version 25+
ovmf_code = "/OVMF.fd"

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


class XRv9k_vm(vrnetlab.VM):
    def __init__(
        self, hostname, username, password, nics, conn_mode, vcpu, ram, install=False
    ):
        disk_image = None
        for e in sorted(os.listdir("/")):
            if not disk_image and re.search(".qcow2", e):
                disk_image = "/" + e
        super(XRv9k_vm, self).__init__(
            username,
            password,
            disk_image=disk_image,
            ram=ram,
            smp=f"cores={vcpu},threads=1,sockets=1",
            use_scrapli=True,
            cpu="host,+ssse3,+sse4.1,+sse4.2,+x2apic",
        )
        
        # extract version num
        version = ""

        try:
            version = self.version
        except: #noqa: E722
            version = re.search(r"\d+(?:\.\d+)+", self.image).group(0)
        
        version_parts = version.split(".")
        self.version_major = int(version_parts[0])
        self.version_minor = int(version_parts[1]) if len(version_parts) > 1 else 0

        self.hostname = hostname
        self.conn_mode = conn_mode
        self.num_nics = nics
        self.install_mode = install
        self.qemu_args.extend(
            [
                "-machine",
                "smm=off",
                "-boot",
                "order=c",
                "-serial",
                "telnet:0.0.0.0:50%02d,server,nowait" % (self.num + 1),
                "-serial",
                "telnet:0.0.0.0:50%02d,server,nowait" % (self.num + 2),
                "-serial",
                "telnet:0.0.0.0:50%02d,server,nowait" % (self.num + 3),
            ]
        )

        # For XRv9k 25.x, or 24.4+, we need to replace the default IDE disk with virtio-blk-pci
        # and add OVMF UEFI firmware
        use_ovmf = False
        if self.version_major > 25:
            use_ovmf = True
        elif self.version_major == 25:
            use_ovmf = True
        elif self.version_major == 24 and self.version_minor >= 4:
            use_ovmf = True
        # else: do not use OVMF/UEFI
        if use_ovmf:
            # Remove the IDE disk that parent class added (both -drive flag and its value),
            # and extract the original qcow2 image path
            new_args = []
            skip_next = False
            disk_file = None
            for i, arg in enumerate(self.qemu_args):
                if skip_next:
                    skip_next = False
                    continue
                if arg == "-drive":
                    if i + 1 < len(self.qemu_args):
                        drive_arg = self.qemu_args[i+1]
                        if "if=ide" in drive_arg or ".qcow2" in drive_arg:
                            # Extract file=... from the drive_arg
                            match = re.search(r"file=([^,]+)", drive_arg)
                            if match:
                                disk_file = match.group(1)
                            skip_next = True
                            continue
                new_args.append(arg)
            self.qemu_args = new_args

            # Add virtio-blk-pci disk configuration using the overlay created by vrnetlab core
            self.qemu_args.extend([
                "-drive",
                f"file={disk_file},if=none,id=drive-virtio-disk0,format=qcow2",
                "-device",
                "virtio-blk-pci,drive=drive-virtio-disk0,id=virtio-disk0",
            ])
            
            # Attach OVMF
            self.qemu_args.extend([
                "-drive",
                f"if=pflash,format=raw,unit=0,readonly=on,file={ovmf_code}",
            ])

    def gen_mgmt(self):
        """Generate qemu args for the mgmt interface(s)"""

        res = super().gen_mgmt()

        # dummy interface for xrv9k ctrl interface
        res.extend(
            [
                "-device",
                "virtio-net-pci,netdev=ctrl-dummy,id=ctrl-dummy,mac=%s"
                % vrnetlab.gen_mac(0),
                "-netdev",
                "tap,ifname=ctrl-dummy,id=ctrl-dummy,script=no,downscript=no",
            ]
        )
        # dummy interface for xrv9k dev interface
        res.extend(
            [
                "-device",
                "virtio-net-pci,netdev=dev-dummy,id=dev-dummy,mac=%s"
                % vrnetlab.gen_mac(0),
                "-netdev",
                "tap,ifname=dev-dummy,id=dev-dummy,script=no,downscript=no",
            ]
        )

        return res

    def bootstrap_spin(self):
        """"""

        if self.spins > 600:
            # too many spins with no result ->  give up
            self.logger.debug(
                "node is failing to boot or we can't catch the right prompt. Restarting..."
            )
            self.stop()
            self.start()
            return

        (ridx, match, res) = self.con_expect(
            [
                b"Press RETURN to get started",
                b"Enter root-system [U|u]sername",
                b"XR partition preparation completed successfully",
            ],
        )

        if match:  # got a match!
            if ridx == 0:  # press return to get started, so we press return!
                self.logger.info("got 'press return to get started...'")
                self.wait_write("", wait=None)
            if ridx == 1 and not self.install_mode:  # initial user config
                self.logger.info("Caught user creation prompt. Creating initial user")
                self.wait_write(self.username, wait=None)
                self.wait_write(self.password, wait="Enter secret:")
                self.wait_write(self.password, wait="Enter secret again:")
                self.write_to_stdout(self.scrapli_tn.channel.read())

                self.apply_config()

                # startup time?
                startup_time = datetime.datetime.now() - self.start_time
                self.logger.info("Startup complete in: %s" % startup_time)
                # mark as running
                self.running = True
                return
            if ridx == 2 and self.install_mode:
                # SDR/XR image bake is complete, install finished
                install_time = datetime.datetime.now() - self.start_time
                self.logger.info("Install complete in: %s", install_time)
                self.running = True
                return

        # no match, if we saw some output from the router it's probably
        # booting, so let's give it some more time
        if res != b"":
            self.write_to_stdout(res)
            # reset spins if we saw some output
            self.spins = 0

        self.spins += 1

        return

    def apply_config(self):
        scrapli_timeout = vrnetlab.getenv_uint("SCRAPLI_TIMEOUT", vrnetlab.DEFAULT_SCRAPLI_TIMEOUT)
        self.logger.info(
            f"Scrapli timeout is {scrapli_timeout}s (default {vrnetlab.DEFAULT_SCRAPLI_TIMEOUT}s)"
        )

        # init scrapli
        xrv9k_scrapli_dev = {
            "host": "127.0.0.1",
            "port": 5000 + self.num,
            "auth_username": self.username,
            "auth_password": self.password,
            "auth_strict_key": False,
            "transport": "telnet",
            "timeout_socket": scrapli_timeout,
            "timeout_transport": scrapli_timeout,
            "timeout_ops": scrapli_timeout,
        }

        xrv9k_config = f"""hostname {self.hostname}
vrf clab-mgmt
description Containerlab management VRF (DO NOT DELETE)
address-family ipv4 unicast
exit
address-family ipv6 unicast
root
!
router static
vrf clab-mgmt
address-family ipv4 unicast
0.0.0.0/0 {self.mgmt_gw_ipv4}
address-family ipv6 unicast
::/0 {self.mgmt_gw_ipv6}
root
!
interface MgmtEth 0/RP0/CPU0/0
description Containerlab management interface
vrf clab-mgmt
ipv4 address {self.mgmt_address_ipv4}
ipv6 address {self.mgmt_address_ipv6}
no shutdown
exit
!
ssh server v2
ssh server vrf clab-mgmt
ssh server netconf
!
grpc port 57400
grpc vrf clab-mgmt
grpc no-tls
!
xml agent tty
root
"""

        if os.path.exists(STARTUP_CONFIG_FILE):
            self.logger.info("Startup configuration file found")
            with open(STARTUP_CONFIG_FILE, "r") as config:
                xrv9k_config += config.read()
        else:
            self.logger.warning("User provided startup configuration is not found.")

        self.scrapli_tn.close()

        with IOSXRDriver(**xrv9k_scrapli_dev) as con:
            if self.version_major < 7:
                # Configure SSH keys
                con.send_interactive(
                    [
                        (
                            "crypto key generate rsa",
                            "How many bits in the modulus [2048]",
                            False,
                        ),
                        ("4096", "#", True),
                    ]
                )
                con.send_command("crypto key generate ecdsa nistp521")

            res = con.send_configs(xrv9k_config.splitlines())
            res += con.send_configs(["commit best-effort label CLAB_BOOTSTRAP", "end"])

            for response in res:
                self.logger.info(f"CONFIG:{response.channel_input}")
                self.logger.info(f"RESULT:{response.result}")


class XRv9k(vrnetlab.VR):
    def __init__(self, hostname, username, password, nics, conn_mode, vcpu, ram):
        super(XRv9k, self).__init__(username, password)
        self.vms = [XRv9k_vm(hostname, username, password, nics, conn_mode, vcpu, ram)]


class XRv9k_Installer(XRv9k):
    """XRv9k installer
    Will start the XRv9k and then shut it down. Booting the XRv9k for the
    first time requires the XRv9k itself to install internal packages
    then it will restart. Subsequent boots will not require this restart.
    By running this "install" when building the docker image we can
    decrease the normal startup time of the XRv9k.
    """

    def __init__(self, hostname, username, password, nics, conn_mode, vcpu, ram):
        super(XRv9k, self).__init__(username, password)
        self.vms = [
            XRv9k_vm(
                hostname, username, password, nics, conn_mode, vcpu, ram, install=True
            )
        ]

    def install(self):
        self.logger.info("Installing XRv9k")
        xrv = self.vms[0]
        while not xrv.running:
            xrv.work()
        xrv.stop()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="")
    parser.add_argument(
        "--trace", action="store_true", help="enable trace level logging"
    )
    parser.add_argument("--hostname", default="vr-xrv9k", help="Router hostname")
    parser.add_argument("--username", default="vrnetlab", help="Username")
    parser.add_argument("--password", default="VR-netlab9", help="Password")
    parser.add_argument("--nics", type=int, default=128, help="Number of NICS")
    parser.add_argument("--install", action="store_true", help="Pre-install image")
    parser.add_argument(
        "--vcpu", type=int, default=4, help="Number of cpu cores to use"
    )
    parser.add_argument(
        "--ram", type=int, default=24576, help="Number RAM to use in MB"
    )
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

    vrnetlab.boot_delay()

    if args.install:
        vr = XRv9k_Installer(
            args.hostname,
            args.username,
            args.password,
            args.nics,
            args.connection_mode,
            args.vcpu,
            args.ram,
        )
        vr.install()
    else:
        vr = XRv9k(
            args.hostname,
            args.username,
            args.password,
            args.nics,
            args.connection_mode,
            args.vcpu,
            args.ram,
        )
        vr.start()
