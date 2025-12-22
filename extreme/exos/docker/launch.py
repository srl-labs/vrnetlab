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


class EXOS_vm(vrnetlab.VM):
    def __init__(self, username, password, hostname, conn_mode):
        disk_image = None
        for e in sorted(os.listdir("/")):
            if not disk_image and re.search(".qcow2$", e):
                disk_image = "/" + e

        super(EXOS_vm, self).__init__(
            username,
            password,
            disk_image=disk_image,
            ram=512,
            cpu="core2duo",
            driveif="ide",
        )

        self.hostname = hostname
        self.conn_mode = conn_mode
        self.num_nics = 13
        self.nic_type = "rtl8139"

    def wait_write_config(self, cmd, wait="#", retries=30, delay=5):
        """Execute command with retry logic for 'configuration load' state.
        
        EXOS can enter a 'configuration load' state during boot where config
        commands are rejected. This wrapper retries the command until success.
        """
        for attempt in range(retries):
            self.tn.write(f"{cmd}\r".encode())
            try:
                res = self.tn.read_until(wait.encode(), timeout=30)
                response_str = res.decode(errors="ignore")
                
                # Check if we got the config load error
                if "configuration load" in response_str.lower():
                    if attempt < retries - 1:  # Don't log on last attempt
                        self.logger.info(
                            f"'configuration load' detected, retrying '{cmd}' in {delay}s..."
                        )
                    time.sleep(delay)
                    continue
                    
                # Success - command completed
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

        (ridx, match, res) = self.tn.expect(
            [rb'node is now available for login.', rb'\[[yY]\/[nN]\/q\]'], 1
        )

        if match:  # got a match!
            if ridx == 0:
                time.sleep(1)
                self.wait_write(cmd="", wait=None)
                self.wait_write(cmd="admin", wait="login:")
                self.wait_write(cmd="", wait="password:")
            else:
                self.wait_write(cmd="q", wait=None)
                self.wait_write(cmd="", wait="#")
                self.logger.info("Found config prompt")
                # run main config!
                self.logger.info("Running bootstrap_config()")
                self.bootstrap_config()
                self.startup_config()
                (ridx, match, res) = self.tn.expect(
                    [rb'node is now available for login.'], 1
                )
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
        self.wait_write_config(cmd=f"configure snmp sysName {self.hostname}")
        self.wait_write_config(cmd="unconfigure vlan Mgmt ipaddress")
        self.wait_write_config(cmd="configure vlan Mgmt ipaddress 10.0.0.15/24")
        self.wait_write_config(cmd="configure iproute add default 10.0.0.2 vr VR-Mgmt")
        if self.username == "admin":
            self.wait_write(cmd="configure account admin password", wait="#")
            self.wait_write(cmd="", wait="Current user's password:")
            self.wait_write(cmd=self.password, wait="New password:")
            self.wait_write(cmd=self.password, wait="Reenter password:")
        else:
            self.wait_write(
                cmd=f"create account admin {self.username} {self.password}", wait="#"
            )
        self.wait_write_config(cmd="disable cli prompting")
        self.wait_write_config(cmd="configure ssh2 key")
        self.wait_write_config(cmd="enable ssh2")
        self.wait_write_config(cmd="save")

    def startup_config(self):
        if not os.path.exists(STARTUP_CONFIG_FILE):
            self.logger.trace(f"Startup config file {STARTUP_CONFIG_FILE} not found")
            self.wait_write(cmd="enable cli prompting", wait="#")
            return
        vrnetlab.run_command(["cp", STARTUP_CONFIG_FILE, "/tftpboot/containerlab.xsf"])
        self.wait_write(cmd="tftp get 10.0.0.2 vr VR-Mgmt containerlab.xsf", wait=None)
        self.wait_write(cmd="load script containerlab.xsf", wait="#")
        self.wait_write(cmd="save", wait="#")
        self.wait_write(cmd="enable cli prompting", wait="#")


class EXOS(vrnetlab.VR):
    def __init__(self, hostname, username, password, conn_mode):
        super(EXOS, self).__init__(username, password)
        self.vms = [EXOS_vm(username, password, hostname, conn_mode)]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="")
    parser.add_argument("--trace", action="store_true", help="enable trace level logging")
    parser.add_argument("--hostname", default="vr-exos", help="Router hostname")
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

    vr = EXOS(args.hostname, args.username, args.password, conn_mode=args.connection_mode)
    vr.start()
