#!/usr/bin/env python3

import datetime
import logging
import os
import re
import signal
import sys

import vrnetlab
from scrapli import Scrapli

STARTUP_CONFIG_FILE = "/config/startup-config.cfg"

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

        scrapli_timeout = vrnetlab.getenv_uint("SCRAPLI_TIMEOUT", vrnetlab.DEFAULT_SCRAPLI_TIMEOUT)

        def _open(conn):
            """Set the internal privilege level to 'exec' so scrapli knows what to do"""
            conn._current_priv_level = conn.privilege_levels["exec"]
            self.logger.debug("Set initial privilege level to 'exec' to boostrap configuration")

        asa_scrapli_dev = {
            "platform": "cisco_asa",
            "host": "127.0.0.1",
            "auth_bypass": True,
            "auth_strict_key": False,
            "auth_secondary": self.password,
            "timeout_socket": scrapli_timeout,
            "timeout_transport": scrapli_timeout,
            "timeout_ops": scrapli_timeout,
            "on_open": _open,
        }

        con = Scrapli(**asa_scrapli_dev)
        con.commandeer(conn=self.scrapli_tn)

        # On fresh ASA, typing 'enable' prompts to set up password
        self.logger.debug("Setting up initial enable password")
        result = con.send_interactive(
            interact_events=[
                ("enable", r"Password:", False),
                (self.password, r"Password:", True),
                # Send an empty character to force the prompt along
                (self.password, r"", False),
                ("", r"ciscoasa#", False),
            ],
            privilege_level="exec"
        )

        self.logger.debug("Entering configuration mode to handle reporting prompt")
        result = con.send_interactive(
            [
                ("configure terminal", r"Would you like to enable anonymous error reporting", False),
                ("N", r"(config)#", False),
            ]
        )

        v4_mgmt_address = vrnetlab.cidr_to_ddn(self.mgmt_address_ipv4)

        config_commands = f"""hostname {self.hostname}
aaa authentication ssh console LOCAL
aaa authentication enable console LOCAL
username {self.username} password {self.password} privilege 15
interface Management0/0
nameif management
security-level 100
ip address {v4_mgmt_address[0]} {v4_mgmt_address[1]}
ipv6 address {self.mgmt_address_ipv6}
no shutdown
exit
route management 0.0.0.0 0.0.0.0 {self.mgmt_gw_ipv4} 1
route management ::/0 {self.mgmt_gw_ipv6} 1
access-list MGMT_IN extended permit tcp any any eq ssh
access-group MGMT_IN in interface management
crypto key generate ecdsa elliptic-curve 256
ssh key-exchange group dh-group14-sha256
ssh 0.0.0.0 0.0.0.0 management
ssh ::/0 management
no ssh stricthostkeycheck
ssh timeout 60"""

        self.logger.debug("Sending configuration commands")
        con.send_configs(config_commands.splitlines())
        
        # Apply user-provided startup configuration if present
        if os.path.exists(STARTUP_CONFIG_FILE):
            self.logger.info("Startup configuration file found")
            with open(STARTUP_CONFIG_FILE, "r") as config:
                startup_config = config.read()
                self.logger.debug("Applying startup configuration")
                con.send_configs(startup_config.splitlines())
        else:
            self.logger.info("User provided startup configuration is not found.")
        
        self.logger.debug("Saving configuration")
        # Exit to privilege exec mode then save
        con.acquire_priv("privilege_exec")
        con.send_command("write memory")
        self.logger.debug("Closing connection")
        con.close()


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
