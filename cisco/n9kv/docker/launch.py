#!/usr/bin/env python3

import datetime
import logging
import os
import re
import signal
import sys

import vrnetlab
from scrapli.driver.core import NXOSDriver

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


class N9KV_vm(vrnetlab.VM):
    def __init__(self, hostname, username, password, conn_mode):
        disk_image = ""
        for e in os.listdir("/"):
            if re.search(".qcow2$", e):
                disk_image = "/" + e
        if disk_image == "":
            logging.getLogger().info("Disk image was not found")
            exit(1)
        super(N9KV_vm, self).__init__(
            username,
            password,
            disk_image=disk_image,
            ram=10240,
            smp=4,
            cpu="host",
            use_scrapli=True,
        )
        self.hostname = hostname
        self.conn_mode = conn_mode
        # mgmt + 128 that show up in the vm, may as well populate them all in vrnetlab right away
        self.num_nics = 129
        self.nic_type = "e1000"

        # bios for n9kv
        self.qemu_args.extend(["-bios", "/OVMF.fd"])

        overlay_disk_image = re.sub(r"(\.[^.]+$)", r"-overlay\1", disk_image)
        # boot harddrive first
        self.qemu_args.extend(["-boot", "c"])
        replace_index = self.qemu_args.index(
            "if=ide,file={}".format(overlay_disk_image)
        )
        self.qemu_args[replace_index] = (
            "file={},if=none,id=drive-sata-disk0,format=qcow2".format(
                overlay_disk_image
            )
        )
        self.qemu_args.extend(["-device", "ahci,id=ahci0,bus=pci.0"])
        self.qemu_args.extend(
            [
                "-device",
                "ide-hd,drive=drive-sata-disk0,bus=ahci0.0,id=drive-sata-disk0,bootindex=1",
            ]
        )

    def bootstrap_spin(self):
        """This function should be called periodically to do work."""
        if self.spins > 300:
            # too many spins with no result ->  give up
            self.stop()
            self.start()
            return

        (ridx, match, res) = self.con_expect(
            [
                b"\(yes\/skip\/no\)\[no\]:",
                b"\(yes\/no\)\[n\]:",
                b"\(yes\/no\)\[no\]:",
                b"login:",
            ]
        )
        if match:  # got a match!
            if ridx in (0, 1, 2):
                self.logger.debug("matched poap prompt")
                self.wait_write("yes", wait=None)
                self.wait_write(
                    "no", wait="Do you want to enforce secure password standard"
                )
                self.wait_write(self.password, wait='Enter the password for "admin"')
                self.wait_write(self.password, wait='Confirm the password for "admin"')
                self.wait_write(
                    "no", wait="Would you like to enter the basic configuration dialog"
                )
            elif ridx == 3:  # login
                self.logger.debug("matched login prompt")
                self.logger.debug(f'trying to log in with "admin" / {self.password}')
                self.wait_write("admin", wait=None)
                self.wait_write(self.password, wait="Password:")

                # run main config!
                self.apply_config()

                # startup time?
                startup_time = datetime.datetime.now() - self.start_time
                self.logger.info("Startup complete in: %s" % startup_time)
                # mark as running
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
        n9kv_scrapli_dev = {
            "host": "127.0.0.1",
            "auth_bypass": True,
            "auth_strict_key": False,
            "timeout_socket": scrapli_timeout,
            "timeout_transport": scrapli_timeout,
            "timeout_ops": scrapli_timeout,
        }

        n9kv_config = f"""hostname {self.hostname}
username {self.username} password 0 {self.password} role network-admin
!
vrf context management
ip route 0.0.0.0/0 {self.mgmt_gw_ipv4}
ipv6 route ::/0 {self.mgmt_gw_ipv6}
exit
!
interface mgmt0
ip address {self.mgmt_address_ipv4}
ipv6 address {self.mgmt_address_ipv6}
exit
!
ssh key rsa 2048 force
feature ssh
!
feature scp-server
feature nxapi
feature telnet
feature netconf
feature grpc
!
"""

        con = NXOSDriver(**n9kv_scrapli_dev)
        con.commandeer(conn=self.scrapli_tn)

        if os.path.exists(STARTUP_CONFIG_FILE):
            self.logger.info("Startup configuration file found")
            with open(STARTUP_CONFIG_FILE, "r") as config:
                n9kv_config += config.read()
        else:
            self.logger.warning("User provided startup configuration is not found.")

        # set `terminal dont-ask` to avoid confirmation prompts
        # during startup config deployment
        con.send_config("terminal dont-ask")
        res = con.send_configs(n9kv_config.splitlines())
        con.send_config("no terminal dont-ask")
        con.send_config("copy running-config startup-config")

        for response in res:
            self.logger.info(f"CONFIG:{response.channel_input}")
            self.logger.info(f"RESULT:{response.result}")

        con.close()


class N9KV(vrnetlab.VR):
    def __init__(self, hostname, username, password, conn_mode):
        super(N9KV, self).__init__(username, password)
        self.vms = [N9KV_vm(hostname, username, password, conn_mode)]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="")
    parser.add_argument(
        "--trace", action="store_true", help="enable trace level logging"
    )
    parser.add_argument("--hostname", default="vr-n9kv", help="Router hostname")
    parser.add_argument("--username", default="vrnetlab", help="Username")
    parser.add_argument("--password", default="VR-netlab9", help="Password")
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

    vr = N9KV(args.hostname, args.username, args.password, args.connection_mode)
    vr.start()
