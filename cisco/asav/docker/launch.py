#!/usr/bin/env python3

import datetime
import logging
import os
import re
import signal
import sys
import time

import vrnetlab

# ASA has some password complexity requirements
ENABLE_PASSWORD = "CiscoAsa1!"


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


class ASAv_vm(vrnetlab.VM):
    def __init__(self, username, password, conn_mode, hostname, install_mode=False):
        for e in os.listdir("/"):
            if re.search(".qcow2$", e):
                disk_image = "/" + e

        super(ASAv_vm, self).__init__(
            username,
            password,
            disk_image=disk_image,
            ram=2048,
            cpu="Nehalem",
            use_scrapli=True,
        )
        self.hostname = hostname
        self.nic_type = "e1000"
        self.conn_mode = conn_mode
        self.install_mode = install_mode
        self.num_nics = 8

    def bootstrap_spin(self):
        """This function should be called periodically to do work."""

        if self.spins > 300:
            # too many spins with no result ->  give up
            self.stop()
            self.start()
            return

        (ridx, match, res) = self.con_expect([b"ciscoasa>"], 1)
        if match:  # got a match!
            if ridx == 0:  # login
                if self.install_mode:
                    self.logger.debug("matched, ciscoasa>")
                    self.wait_write("", wait=None)
                    self.wait_write("", None)
                    self.wait_write("", wait="ciscoasa>")
                    self.running = True
                    return

                self.logger.debug("matched, ciscoasa>")
                self.wait_write("", wait=None)

                # run main config!
                self.apply_config()

                # startup time?
                startup_time = datetime.datetime.now() - self.start_time
                self.logger.debug("Startup complete in: %s" % startup_time)
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
        """Apply the full configuration"""
        self.logger.debug("Applying bootstrap configuration")
        self.wait_write("enable", wait="ciscoasa>")
        self.wait_write(ENABLE_PASSWORD, wait="Password:")
        self.wait_write(ENABLE_PASSWORD, wait="Password:")
        self.wait_write("", wait="ciscoasa#")
        self.wait_write("configure terminal", wait="ciscoasa#")

        # Handle the initial user prompt that appears after configure terminal
        # The ASA will show a call-home prompt that we need to respond to
        self.logger.debug("Handling initial user prompt")
        # Give the prompt time to appear
        time.sleep(2)
        # Send 'N' followed by extra carriage return to get back to prompt
        self.scrapli_tn.channel.write("N\r")
        # Wait for the response message to complete
        time.sleep(2)
        self.scrapli_tn.channel.write("\r")
        # Wait for prompt to appear
        time.sleep(1)
        # Read and discard any buffered output to clear the channel
        _ = self.scrapli_tn.channel.read()

        # configure the asa hostname
        self.wait_write(f"hostname {self.hostname}", wait=None)

        # Now we should be at config prompt, send first command without waiting
        self.logger.debug("Setting device access")
        self.wait_write("aaa authentication ssh console LOCAL", wait=None)
        self.wait_write("aaa authentication enable console LOCAL")
        self.wait_write(
            f"username {self.username} password {self.password} privilege 15"
        )

        v4_mgmt_address = vrnetlab.cidr_to_ddn(self.mgmt_address_ipv4)

        self.logger.debug("Configuring management interface")
        self.wait_write("interface Management0/0")
        self.wait_write("nameif management")
        self.wait_write("security-level 100")
        self.wait_write(f"ip address {v4_mgmt_address[0]} {v4_mgmt_address[1]}")
        self.wait_write(f"ipv6 address {self.mgmt_address_ipv6}")
        self.wait_write("no shutdown")
        self.wait_write("exit")

        self.logger.debug("Adding default route")
        self.wait_write(f"route management 0.0.0.0 0.0.0.0 {self.mgmt_gw_ipv4} 1")
        self.wait_write(f"route management ::/0 {self.mgmt_gw_ipv6} 1")

        self.logger.debug("Configuring management access")
        self.wait_write("access-list MGMT_IN extended permit tcp any any eq ssh")
        self.wait_write("access-group MGMT_IN in interface management")

        self.logger.debug("Configuring SSH")
        self.wait_write("crypto key generate ecdsa elliptic-curve 256")
        self.wait_write("ssh key-exchange group dh-group14-sha256")
        self.wait_write("ssh 0.0.0.0 0.0.0.0 management")
        self.wait_write("ssh ::/0 management")
        self.wait_write("no ssh stricthostkeycheck")
        self.wait_write("ssh timeout 60")

        self.logger.debug("Saving configuration")
        self.wait_write("write memory")
        self.wait_write("end")
        self.wait_write("\r", None)

        self.logger.debug("Closing telnet connection")
        self.scrapli_tn.close()


class ASAv(vrnetlab.VR):
    def __init__(self, username, password, conn_mode, hostname):
        super(ASAv, self).__init__(username, password)
        self.vms = [ASAv_vm(username, password, conn_mode, hostname)]


class ASAv_installer(ASAv):
    """ASAv installer"""

    def __init__(self, username, password, conn_mode, hostname):
        super(ASAv_installer, self).__init__(username, password, conn_mode, hostname)
        self.vms = [ASAv_vm(username, password, conn_mode, hostname, install_mode=True)]

    def install(self):
        self.logger.info("Installing ASAv")
        asav = self.vms[0]
        while not asav.running:
            asav.work()
        asav.stop()
        self.logger.info("Installation complete")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="")
    parser.add_argument(
        "--trace", action="store_true", help="enable trace level logging"
    )
    parser.add_argument("--hostname", default="asa", help="Hostname of the ASA VM")
    parser.add_argument("--username", default="admin", help="Username")
    parser.add_argument("--password", default="CiscoAsa1!", help="Password")
    parser.add_argument("--install", action="store_true", help="Install ASAv")
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
        vr = ASAv_installer(
            args.username, args.password, args.connection_mode, args.hostname
        )
        vr.install()
    else:
        vr = ASAv(args.username, args.password, args.connection_mode, args.hostname)
        vr.start()
