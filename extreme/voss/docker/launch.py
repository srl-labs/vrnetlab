#!/usr/bin/env python3

import datetime
import logging
import re
import signal
import sys
import time
import os

import vrnetlab

STARTUP_CONFIG_FILE = "/config/startup-config.xsf"


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


class VOSS_vm(vrnetlab.VM):
    def __init__(self, username, password, hostname, conn_mode):
        disk_image = None
        for e in sorted(os.listdir("/")):
            if not disk_image and re.search(".qcow2$", e):
                disk_image = "/" + e

        super(VOSS_vm, self).__init__(
            username,
            password,
            disk_image=disk_image,
            ram=2048,
            driveif="ide",
        )

        self.hostname = hostname
        self.conn_mode = conn_mode
        self.num_nics = 33
        self.nic_type = "e1000"

    def wait_write_config(self, cmd, wait="#", retries=30, delay=5, error_patterns=None):
        """Execute command with retry logic for transient VOSS boot states.

        Retries when the response contains any of the strings in error_patterns
        or the generic 'configuration load' rejection message.
        """
        retry_patterns = ["configuration load"]
        if error_patterns:
            retry_patterns.extend(error_patterns)

        for attempt in range(retries):
            self.tn.write(f"{cmd}\r".encode())
            try:
                res = self.tn.read_until(wait.encode(), timeout=30)
                response_str = res.decode(errors="ignore")

                matched = next(
                    (p for p in retry_patterns if p.lower() in response_str.lower()),
                    None,
                )
                if matched:
                    self.logger.info(
                        f"Retryable response ({matched!r}) for '{cmd}', attempt {attempt + 1}/{retries}, waiting {delay}s..."
                    )
                    time.sleep(delay)
                    continue

                return
            except Exception as e:
                self.logger.warning(f"Timeout waiting for prompt after '{cmd}': {e}")
                if attempt < retries - 1:
                    time.sleep(delay)
                continue

        self.logger.error(f"Command '{cmd}' failed after {retries} retries")

    def bootstrap_spin(self):
        """ This function should be called periodically to do work.
        """

        if self.spins > 300:
            # too many spins with no result ->  give up
            self.stop()
            self.start()
            return

        (_, match, res) = self.tn.expect([rb'ogin:'], 1)

        if match:  # got a match!
            time.sleep(1)
            self.wait_write(cmd="", wait=None)
            self.wait_write(cmd="rwa", wait="ogin:")
            self.wait_write(cmd="rwa", wait="assword")
            # First login with the default rwa/rwa forces a password change
            self.wait_write(cmd=self.password, wait="assword")
            self.wait_write(cmd=self.password, wait="assword")
            self.wait_write(cmd="", wait=">")
            self.logger.info("Found login prompt")
            # run main config!
            self.logger.info("Running bootstrap_config()")
            self.bootstrap_config()
            self.startup_config()
            time.sleep(1)
            # close telnet connection
            self.tn.close()
            # startup time?
            startup_time = datetime.datetime.now() - self.start_time
            self.logger.info("Startup complete in: %s" % startup_time)
            # mark as running
            self.running = True
            return

        # no match, if we saw some output from the router it's probably
        # booting, so let's give it some more time
        if res != b"":
            self.logger.trace("OUTPUT: %s" % res.decode())
            # reset spins if we saw some output
            self.spins = 0

        self.spins += 1

        return


    def bootstrap_config(self):
        """ Do the actual bootstrap config
        """
        self.wait_write_config(cmd="enable", wait="#")
        self.wait_write_config(cmd="configure terminal", wait="#")
        self.wait_write_config(cmd=f"sys name {self.hostname}", wait="#")
        self.wait_write_config(cmd="mgmt oob")
        # time.sleep(120)  # Wait for the mgmt interface to come up
        self.wait_write_config(
            cmd="convert ip 10.0.0.15/24 gateway 10.0.0.2",
            wait="(y/n) ?",
            error_patterns=["an ip address is mandatory"],
        )
        self.wait_write_config(cmd="y", wait="#")
        self.wait_write_config(cmd="mgmt convert-commit", wait="#")
        self.wait_write_config(
            cmd=f"username add {self.username} level rwa enable", wait="assword"
        )
        self.wait_write_config(cmd=self.password, wait="assword")
        self.wait_write_config(cmd=self.password, wait="#")
        self.wait_write_config(cmd="exit", wait="#")
        self.wait_write_config(cmd="save config", wait="#")

    def startup_config(self):
        if not os.path.exists(STARTUP_CONFIG_FILE):
            self.logger.trace(f"Startup config file {STARTUP_CONFIG_FILE} not found")
            return
        vrnetlab.run_command(["cp", STARTUP_CONFIG_FILE, "/tftpboot/containerlab.xsf"])
        self.wait_write(cmd="tftp get 10.0.0.2 vr VR-Mgmt containerlab.xsf", wait=None)
        self.wait_write(cmd="load script containerlab.xsf", wait="#")
        self.wait_write(cmd="save config", wait="#")


class VOSS(vrnetlab.VR):
    def __init__(self, hostname, username, password, conn_mode):
        super(VOSS, self).__init__(username, password)
        self.vms = [VOSS_vm(username, password, hostname, conn_mode)]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="")
    parser.add_argument("--trace", action="store_true", help="enable trace level logging")
    parser.add_argument("--hostname", default="vr-voss", help="Router hostname")
    parser.add_argument('--username', default='vrnetlab', help='Username')
    parser.add_argument('--password', default='VR-netlab9', help='Password')
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

    vr = VOSS(args.hostname, args.username, args.password, conn_mode=args.connection_mode)
    vr.start()
