#!/usr/bin/env python3

import datetime
import logging
import os
import re
import signal
import sys
import time

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


class NXOS_vm(vrnetlab.VM):
    def __init__(self, hostname, username, password, conn_mode):
        for e in os.listdir("/"):
            if re.search(".qcow2$", e):
                disk_image = "/" + e
        super(NXOS_vm, self).__init__(
            username,
            password,
            disk_image=disk_image,
            ram=4096,
            smp="2",
            use_scrapli=True,
        )
        self.credentials = [["admin", "admin"]]
        self.hostname = hostname
        self.conn_mode = conn_mode
        self.num_nics = 32

    def bootstrap_spin(self):
        """This function should be called periodically to do work."""

        if self.spins > 300:
            # too many spins with no result ->  give up
            self.stop()
            self.start()
            return

        (ridx, match, res) = self.con_expect([b"login:"])
        if match:  # got a match!
            if ridx == 0:  # login
                self.logger.debug("matched login prompt")
                try:
                    username, password = self.credentials.pop(0)
                except IndexError as exc:
                    self.logger.error("no more credentials to try")
                    return
                self.logger.debug(
                    "trying to log in with %s / %s" % (username, password)
                )
                self.wait_write(username, wait=None)
                self.wait_write(password, wait="Password:")

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
        nxos_scrapli_dev = {
            "host": "127.0.0.1",
            "auth_bypass": True,
            "auth_strict_key": False,
            "timeout_socket": scrapli_timeout,
            "timeout_transport": scrapli_timeout,
            "timeout_ops": scrapli_timeout,
        }

        nxos_config = f"""hostname {self.hostname}
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
no feature ssh
ssh key rsa 2048 force
feature ssh
!
"""

        con = NXOSDriver(**nxos_scrapli_dev)
        con.commandeer(conn=self.scrapli_tn)

        if os.path.exists(STARTUP_CONFIG_FILE):
            self.logger.info("Startup configuration file found")
            with open(STARTUP_CONFIG_FILE, "r") as config:
                nxos_config += config.read()
        else:
            self.logger.warning("User provided startup configuration is not found.")

        res = con.send_configs(nxos_config.splitlines())
        con.send_config("copy running-config startup-config")

        for response in res:
            self.logger.info(f"CONFIG:{response.channel_input}")
            self.logger.info(f"RESULT:{response.result}")

        con.close()


class NXOS(vrnetlab.VR):
    def __init__(self, hostname, username, password, conn_mode):
        super(NXOS, self).__init__(username, password)
        self.vms = [NXOS_vm(hostname, username, password, conn_mode)]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="")
    parser.add_argument("--hostname", default="vr-nxos", help="Router hostname")
    parser.add_argument(
        "--trace", action="store_true", help="enable trace level logging"
    )
    parser.add_argument("--username", default="admin", help="Username")
    parser.add_argument("--password", default="admin", help="Password")
    parser.add_argument(
        "--connection-mode",
        default="tc",
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
    vr = NXOS(
        args.hostname, args.username, args.password, conn_mode=args.connection_mode
    )
    vr.start()
