#!/usr/bin/env python3

import datetime
import logging
import os
import re
import signal
import sys
import time

import vrnetlab
from scrapli.driver.core import IOSXRDriver

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


class XRV_vm(vrnetlab.VM):
    def __init__(self, hostname, username, password, conn_mode):
        for e in os.listdir("/"):
            if re.search(".vmdk", e):
                disk_image = "/" + e
        super(XRV_vm, self).__init__(
            username, password, disk_image=disk_image, ram=3072, use_scrapli=True
        )
        self.hostname = hostname
        self.conn_mode = conn_mode
        self.num_nics = 128
        self.credentials = []

        self.xr_ready = False

    def bootstrap_spin(self):
        """"""

        if self.spins > 300:
            # too many spins with no result ->  give up
            self.stop()
            self.start()
            return

        (ridx, match, res) = self.con_expect(
            [
                b"Press RETURN to get started",
                b"SYSTEM CONFIGURATION COMPLETE",
                b"Enter root-system username",
                b"Username:",
                b"^[^ ]+#",
            ],
        )
        if match:  # got a match!
            if ridx == 0:  # press return to get started, so we press return!
                self.logger.debug("got 'press return to get started...'")
                self.wait_write("", wait=None)
            if ridx == 1:  # system configuration complete
                self.logger.info(
                    "IOS XR system configuration is complete, should be able to proceed with bootstrap configuration"
                )
                self.wait_write("", wait=None)
                self.xr_ready = True
            if ridx == 2:  # initial user config
                self.logger.info("Creating initial user")
                time.sleep(15)
                self.wait_write(self.username, wait=None)
                self.wait_write(self.password, wait="Enter secret:")
                self.wait_write(self.password, wait="Enter secret again:")
                self.credentials.insert(0, [self.username, self.password])
            if ridx == 3:  # matched login prompt, so should login
                self.logger.info("matched login prompt")
                try:
                    username, password = self.credentials.pop(0)
                except IndexError:
                    self.logger.error("no more credentials to try")
                    return
                self.logger.info("trying to log in with %s / %s" % (username, password))
                self.wait_write(username, wait=None)
                self.wait_write(password, wait="Password:")
            if self.xr_ready and ridx == 4:
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
        xrv_scrapli_dev = {
            "host": "127.0.0.1",
            "auth_bypass": True,
            "auth_strict_key": False,
            "timeout_socket": scrapli_timeout,
            "timeout_transport": scrapli_timeout,
            "timeout_ops": scrapli_timeout,
        }

        xrv_config = f"""hostname {self.hostname}
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
interface MgmtEth 0/0/CPU0/0
description Containerlab management interface
vrf clab-mgmt
ipv4 address {self.mgmt_address_ipv4}
ipv6 address {self.mgmt_address_ipv6}
no shut
exit
!
ssh server v2
ssh server vrf clab-mgmt
ssh server netconf port 830
ssh server netconf vrf clab-mgmt
netconf agent ssh
netconf-yang agent ssh
!
grpc port 57400
grpc vrf clab-mgmt
grpc no-tls
!
xml agent tty
root
"""

        con = IOSXRDriver(**xrv_scrapli_dev)
        con.commandeer(conn=self.scrapli_tn)

        if os.path.exists(STARTUP_CONFIG_FILE):
            self.logger.info("Startup configuration file found")
            with open(STARTUP_CONFIG_FILE, "r") as config:
                xrv_config += config.read()
        else:
            self.logger.warning("User provided startup configuration is not found.")

        # configure SSH keys
        con.send_interactive(
            [
                (
                    "crypto key generate rsa",
                    "How many bits in the modulus [2048]",
                    False,
                ),
                ("2048", "#", True),
            ]
        )

        res = con.send_configs(xrv_config.splitlines())
        res += con.send_configs(["commit best-effort label CLAB_BOOTSTRAP", "end"])

        for response in res:
            self.logger.info(f"CONFIG:{response.channel_input}")
            self.logger.info(f"RESULT:{response.result}")

        con.close()


class XRV(vrnetlab.VR):
    def __init__(self, hostname, username, password, conn_mode):
        super(XRV, self).__init__(username, password)
        self.vms = [XRV_vm(hostname, username, password, conn_mode)]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="")
    parser.add_argument("--hostname", default="vr-xrv", help="Router hostname")
    parser.add_argument(
        "--trace", action="store_true", help="enable trace level logging"
    )
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

    logger.debug(
        "acting flags: username '{}', password '{}', connection-mode '{}'".format(
            args.username, args.password, args.connection_mode
        )
    )

    vrnetlab.boot_delay()

    vr = XRV(args.hostname, args.username, args.password, args.connection_mode)
    vr.start()
