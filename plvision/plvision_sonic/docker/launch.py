#!/usr/bin/env python3

import datetime
import logging
import os
import re
import signal
import subprocess
import sys

import vrnetlab

CONFIG_FILE = "/config/config_db.json"
DEFAULT_USER = "admin"
DEFAULT_PASSWORD = "YourPaSsWoRd"


def handle_SIGCHLD(_signal, _frame):
    os.waitpid(-1, os.WNOHANG)


def handle_SIGTERM(_signal, _frame):
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


class PLVision_SONiC_VM(vrnetlab.VM):
    def __init__(self, hostname, username, password, conn_mode):
        disk_image = "/"
        for e in os.listdir("/"):
            if re.search(".qcow2$", e):
                disk_image = "/" + e
                break
        super(PLVision_SONiC_VM, self).__init__(
            username, password, disk_image=disk_image, ram=4096, smp="2"
        )
        self.nic_type = "virtio-net-pci"
        self.conn_mode = conn_mode
        self.num_nics = 96
        self.hostname = hostname

    def bootstrap_spin(self):
        """This function should be called periodically to do work."""

        if self.spins > 300:
            # too many spins with no result ->  give up
            self.stop()
            self.start()
            return

        ridx, match, res = self.tn.expect([b"login:"], 1)
        if match and ridx == 0:  # login
            self.logger.info("VM started")

            # Login
            self.wait_write("\r", None)
            self.wait_write(DEFAULT_USER, wait="login:")
            self.wait_write(DEFAULT_PASSWORD, wait="Password:")
            self.wait_write("", wait="%s@" % (self.username))
            self.logger.info("Login completed")

            # run main config!
            self.bootstrap_config()
            self.startup_config()
            # close telnet connection
            self.tn.close()
            # startup time?
            startup_time = datetime.datetime.now() - self.start_time
            self.logger.info(f"Startup complete in: {startup_time}")
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
        """Do the actual bootstrap config"""
        self.logger.info("applying bootstrap configuration")
        self.wait_write("sudo -i", "$")
        # Set IPv4 Management Address if present:
        if self.mgmt_address_ipv4 and "." in self.mgmt_address_ipv4:
            self.wait_write(
                f"/usr/sbin/ip address add {self.mgmt_address_ipv4} dev eth0", "#"
            )
        # Note: IPv6 has not been fully tested - it has reported problems under Enterprise SONiC
        # Set IPv6 Management Address if present:
        if self.mgmt_address_ipv6 and ":" in self.mgmt_address_ipv6:
            self.wait_write(
                f"/usr/sbin/ip -6 address add {self.mgmt_address_ipv6} dev eth0", "#"
            )
        # Set IPv4 Management Gateway if present:
        if self.mgmt_gw_ipv4 and "." in self.mgmt_gw_ipv4:
            self.wait_write(
                f'while ! /usr/sbin/ip link show eth0 | grep -q "state UP"; do sleep 1; done; /usr/sbin/ip route add default via {self.mgmt_gw_ipv4} dev eth0',
                "#",
            )
        # Set IPv6 Management Gateway if present:
        if self.mgmt_gw_ipv6 and ":" in self.mgmt_gw_ipv6:
            self.wait_write(
                f'while ! /usr/sbin/ip link show eth0 | grep -q "state UP"; do sleep 1; done;/usr/sbin/ip -6 route add default via {self.mgmt_gw_ipv6} dev eth0',
                "#",
            )
        self.wait_write("passwd -q %s" % (self.username))
        self.wait_write(self.password, "New password:")
        self.wait_write(self.password, "password:")
        self.wait_write("sleep 1", "#")
        self.wait_write("hostnamectl set-hostname %s" % (self.hostname))
        self.wait_write("sleep 1", "#")
        self.wait_write("printf '127.0.0.1\\t%s\\n' >> /etc/hosts" % (self.hostname))
        self.wait_write("sleep 1", "#")
        self.logger.info("completed bootstrap configuration")

    def startup_config(self):
        """Load additional config provided by user."""

        if not os.path.exists(CONFIG_FILE):
            self.logger.trace(f"Backup file {CONFIG_FILE} not found")
            return

        self.logger.trace(f"Backup file {CONFIG_FILE} exists")

        subprocess.run(
            f"/backup.sh -u {self.username} -p {self.password} restore",
            check=True,
            shell=True,
        )


class PLVision_SONiC(vrnetlab.VR):
    def __init__(self, hostname, username, password, conn_mode):
        super().__init__(username, password)
        self.vms = [PLVision_SONiC_VM(hostname, username, password, conn_mode)]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="")
    parser.add_argument(
        "--trace", action="store_true", help="enable trace level logging"
    )
    parser.add_argument("--hostname", default="sonic", help="SONiC hostname")
    parser.add_argument("--username", default="admin", help="Username")
    parser.add_argument("--password", default="admin", help="Password")
    parser.add_argument(
        "--connection-mode", default="tc", help="Connection mode to use in the datapath"
    )
    args = parser.parse_args()

    LOG_FORMAT = "%(asctime)s: %(module)-10s %(levelname)-8s %(message)s"
    logging.basicConfig(format=LOG_FORMAT)
    logger = logging.getLogger()

    logger.setLevel(logging.DEBUG)
    if args.trace:
        logger.setLevel(1)

    vr = PLVision_SONiC(
        args.hostname,
        args.username,
        args.password,
        conn_mode=args.connection_mode,
    )
    vr.start()
