#!/usr/bin/env python3

import datetime
import logging
import os
import re
import signal
import sys
import time

import vrnetlab
from scrapli import Scrapli
from scrapli.exceptions import ScrapliException, ScrapliTimeout

STARTUP_CONFIG_FILE = "/config/startup-config.cfg"

# ASA has some password complexity requirements
ENABLE_PASSWORD = "CiscoAsa1!"

# Seconds to wait on the console for a prompt that the running release may not
# even ask for. Nothing here is worth the default scrapli timeout: a release that
# does not ask is the expected case, not a failure.
CONSOLE_DIALOG_TIMEOUT = 60


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

        scrapli_timeout = vrnetlab.getenv_uint(
            "SCRAPLI_TIMEOUT", vrnetlab.DEFAULT_SCRAPLI_TIMEOUT
        )

        def _open(conn):
            """Set the internal privilege level to 'exec' so scrapli knows what to do"""
            conn._current_priv_level = conn.privilege_levels["exec"]
            self.logger.debug(
                "Set initial privilege level to 'exec' to boostrap configuration"
            )

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

        # On a fresh ASA, typing 'enable' prompts to set the password up -- but
        # only since 9.12 or so. Older releases just authenticate against the
        # empty password they ship with and go straight to the enable prompt,
        # where 'aaa authentication enable console LOCAL' takes over below.
        self.logger.debug("Setting up initial enable password")
        self._console_dialog(
            con,
            "enable",
            re.compile(r"ciscoasa#"),
            [
                (re.compile(r"Enter\s+Password:"), self.password),
                (re.compile(r"Repeat\s+Password:"), self.password),
                (re.compile(r"Password:"), ""),
            ],
        )

        # Releases that do not report anonymously never ask the question
        self.logger.debug("Entering configuration mode to handle reporting prompt")
        self._console_dialog(
            con,
            "configure terminal",
            re.compile(r"\(config\)#"),
            [(re.compile(r"Would you like to enable anonymous error reporting"), "N")],
        )

        v4_mgmt_address = vrnetlab.cidr_to_ddn(self.mgmt_address_ipv4)
        ipv6_address = self.render_optional_mgmt_config(
            "ipv6 address {address}", address=self.mgmt_address_ipv6
        )
        ipv6_route = self.render_optional_mgmt_config(
            "route management ::/0 {gateway} 1", gateway=self.mgmt_gw_ipv6
        )

        config_commands = f"""hostname {self.hostname}
aaa authentication ssh console LOCAL
aaa authentication enable console LOCAL
username {self.username} password {self.password} privilege 15
interface Management0/0
nameif management
security-level 100
ip address {v4_mgmt_address[0]} {v4_mgmt_address[1]}
{ipv6_address}
no shutdown
exit
route management 0.0.0.0 0.0.0.0 {self.mgmt_gw_ipv4} 1
{ipv6_route}
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

        # After the save: every 'username' line flags its user again, including
        # the ones a startup configuration brings, and the console authentication
        # this needs is only ever meant to live in the running configuration.
        self.complete_first_login(con)

        self.logger.debug("Closing connection")
        con.close()

    def complete_first_login(self, con):
        """Answer the password change ASA 9.18+ forces at the first login.

        Those releases mark a local user whose password was set by an administrator
        as "New-User" ('show aaa local user') and make the first authenticated login
        change it. Nothing clears that flag from the configuration, and the console
        is not authenticated, so the dialog normally waits for the first SSH login --
        which this container cannot make itself: with management passthrough the
        management address sits on its own eth0 and the VM only ever sees what tc
        redirects to tap0. The console is put behind local authentication for the
        length of one login instead, and the dialog answered there.

        The password is set to the one the user already has: the point is only to
        clear the flag so that the first real login lands on a prompt instead of the
        dialog. Releases that do not force the change simply log in.

        A failure here only leaves the dialog to whoever logs in first, so it is
        logged and the bootstrap carries on.
        """
        exec_prompt = re.compile(rf"{re.escape(self.hostname)}>")
        priv_prompt = re.compile(rf"{re.escape(self.hostname)}#")
        answers = [
            (re.compile(r"[Uu]sername:"), self.username),
            (re.compile(r"[Ee]nter old password"), self.password),
            (re.compile(r"[Ee]nter new password"), self.password),
            (re.compile(r"[Cc]onfirm new password"), self.password),
            (re.compile(r"[Pp]assword:"), self.password),
        ]

        self.logger.debug("Handling the forced password change at first login")
        try:
            con.send_configs(["aaa authentication serial console LOCAL"])
            # send_configs leaves the session in configuration mode, where 'exit'
            # only drops back to the enable prompt instead of logging out
            con.acquire_priv("privilege_exec")
            self._console_dialog(con, "exit", exec_prompt, answers)
            self._console_dialog(con, "enable", priv_prompt, answers)
            con._current_priv_level = con.privilege_levels["privilege_exec"]
            con.send_configs(["no aaa authentication serial console LOCAL"])
        except (ScrapliException, OSError) as exc:
            self.logger.warning(
                f"Could not complete the first login ({exc}), the password change "
                "is left to whoever logs in to this device first"
            )
            # best effort, so that a console is not left behind a login prompt
            try:
                con._current_priv_level = con.privilege_levels["privilege_exec"]
                con.send_configs(["no aaa authentication serial console LOCAL"])
            except (ScrapliException, OSError):
                self.logger.warning(
                    f"The console of this device is left behind a login prompt, "
                    f"use {self.username} to get past it"
                )

    def _console_dialog(self, con, command, prompt, answers):
        """Send a command on the console and answer prompts until 'prompt' shows up

        The prompts an ASA offers vary from release to release, so they are
        answered as they come rather than in a fixed order, and a release that
        skips one of them simply lands on 'prompt' without ever being asked.
        """
        timeout = vrnetlab.getenv_uint("CONSOLE_DIALOG_TIMEOUT", CONSOLE_DIALOG_TIMEOUT)
        # A read that times out takes the transport down with it, so it has to be
        # kept short enough to not stall the boot for the whole transport timeout.
        # 'con' commandeered the transport, which kept the timeouts of the
        # connection it came from: setting them on 'con' would have no effect.
        transport_args = con.transport._base_transport_args
        original_timeout = transport_args.timeout_transport
        transport_args.timeout_transport = timeout

        con.channel.write(command)
        con.channel.send_return()

        buf = ""
        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                buf += con.channel.read().decode(errors="replace")
                if prompt.search(buf):
                    return
                # the earliest match wins: 'Enter Password:' ends in 'Password:'
                # too, and the generic prompt would otherwise swallow it
                matched = [
                    (match.start(), match.end(), reply)
                    for pattern, reply in answers
                    for match in [pattern.search(buf)]
                    if match
                ]
                if not matched:
                    continue
                _, end, reply = min(matched)
                buf = buf[end:]
                con.channel.write(reply, redacted=True)
                con.channel.send_return()

            raise ScrapliTimeout(f"no '{prompt.pattern}' prompt after '{command}'")
        finally:
            transport_args.timeout_transport = original_timeout


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
