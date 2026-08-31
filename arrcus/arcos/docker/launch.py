#!/usr/bin/env python3

import datetime
import logging
import os
import re
import signal
import sys
import time
from pathlib import Path

import vrnetlab

# containerlab's arrcus_arcos kind mounts the startup config to
# /config/startup.cfg and points the STARTUP_CFG env var at it
STARTUP_CONFIG_FILE = os.getenv("STARTUP_CFG", "/config/startup-config.cfg")

# the ArcOS VM ships with a single built-in user that is used to bootstrap
# the system via the serial console
DEFAULT_USERNAME = "root"
DEFAULT_PASSWORD = "YouReallyNeedToChangeThis"


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


class ArcOS_vm(vrnetlab.VM):
    def __init__(self, hostname, username, password, conn_mode):
        disk_image = None
        for e in sorted(os.listdir("/")):
            if not disk_image and re.search(".qcow2$", e):
                disk_image = "/" + e
        if disk_image is None:
            logging.getLogger().info("Disk image was not found")
            exit(1)

        super(ArcOS_vm, self).__init__(
            username,
            password,
            disk_image=disk_image,
            ram=16384,
            smp="4",
            driveif="virtio",
            # containerlab's arrcus_arcos kind provisions the data plane
            # interfaces in the container with the same swpX names they have
            # in the ArcOS VM
            data_intf_prefix="swp",
        )
        self.hostname = hostname
        self.conn_mode = conn_mode
        self.num_nics = 16
        self.nic_type = "virtio-net-pci"
        self.ztp_started = False

    def nic_provision_delay(self) -> None:
        """Wait for the provisioned data plane interfaces to appear

        Same as the base class implementation, except that the management
        interface is not included in the expected interface count: it (eth0)
        does not share the swp prefix of the data plane interfaces, so it is
        not picked up by the interface glob below.
        """
        self.logger.debug(
            f"number of provisioned data plane interfaces is {self.num_provisioned_nics}"
        )

        # no nics provisioned and/or not running from containerlab so we can bail
        if self.num_provisioned_nics == 0:
            return

        self.logger.debug("waiting for provisioned interfaces to appear...")

        start_eth = self.start_nic_eth_idx
        end_eth = self.start_nic_eth_idx + self.num_nics

        inf_path = Path("/sys/class/net/")
        while True:
            provisioned_nics = list(inf_path.glob(f"{self.data_intf_prefix}*"))
            # if we see all provisioned nics we are ready to roll!
            if len(provisioned_nics) >= self.num_provisioned_nics:
                nics = [
                    int(re.search(pattern=r"\d+", string=nic.name).group())
                    for nic in provisioned_nics
                ]

                # Ensure the max eth is in range of allocated eth index of VM LC
                nics = [nic for nic in nics if nic in range(start_eth, end_eth)]

                if nics:
                    self.highest_provisioned_nic_num = max(nics)

                self.logger.debug(
                    f"highest allocated interface id determined to be: {self.highest_provisioned_nic_num}..."
                )
                self.logger.debug("interfaces provisioned, continuing...")
                break
            time.sleep(5)

    def bootstrap_spin(self):
        """This function should be called periodically to do work."""

        if self.spins > 300:
            # too many spins with no result ->  give up
            self.logger.info("Too many spins with no result, restarting")
            self.stop()
            self.start()
            return

        (ridx, match, res) = self.tn.expect([b"login:"], 1)

        # ZTP starts a few minutes into the first boot and the system
        # configuration must not be changed while it is running -- hold off
        # logging in until it has started so it can be stopped reliably
        if not self.ztp_started and b"Starting Zero Touch Provisioning" in res:
            self.logger.info("ZTP started")
            self.ztp_started = True
            # elicit a fresh login prompt
            self.wait_write("", wait=None)
            self.spins = 0
            return

        if match and ridx == 0 and not self.ztp_started:
            self.logger.debug("login prompt seen, waiting for ZTP to start")
            self.spins = 0
            return

        if match and ridx == 0:  # got a match!
            self.logger.debug("matched login prompt")
            self.logger.debug(f"trying to log in with '{DEFAULT_USERNAME}'")
            self.wait_write(DEFAULT_USERNAME, wait=None)
            self.wait_write(DEFAULT_PASSWORD, wait="Password:")
            # logging in as root places us in the Linux bash shell,
            # wait for the bash prompt (do not wait for a bare '#' as the
            # kernel version in the motd contains one)
            self.wait_write("", wait=":~#")
            if not self.enter_cli():
                self.logger.error("Could not enter the ArcOS CLI, restarting")
                self.stop()
                self.start()
                return
            # run bootstrap config!
            self.logger.info("Running bootstrap_config()")
            self.bootstrap_config()
            self.startup_config()
            # close telnet connection
            self.tn.close()
            # startup time?
            startup_time = datetime.datetime.now() - self.start_time
            self.logger.info("Startup complete in: %s" % startup_time)
            # mark as running
            self.running = True
            return

        time.sleep(5)

        # no match, if we saw some output from the router it's probably
        # booting, so let's give it some more time
        if res != b"":
            self.logger.trace("OUTPUT: %s" % res.decode())
            # reset spins if we saw some output
            self.spins = 0

        self.spins += 1

        return

    def enter_cli(self):
        """Enter the ArcOS CLI from the Linux bash shell

        The login prompt appears on the serial console before all ArcOS
        services are up, in which case the "cli" command fails with
        "Failed to connect to server", so retry until the CLI is available.
        """
        for _ in range(60):
            self.wait_write("cli", wait=None)
            (ridx, match, res) = self.tn.expect(
                [b"Welcome to the ArcOS CLI", b"Failed to connect to server"], 10
            )
            if match and ridx == 0:
                return True
            self.logger.info("ArcOS CLI not available yet, retrying in 5 seconds...")
            time.sleep(5)
        return False

    def bootstrap_mgmt_interface(self):
        """Configure the management interface (ma1)

        By default ArcOS runs a DHCP client on ma1, which takes care of the
        management addressing in both the host-forwarded mode (qemu user mode
        network) and the pass-through mode with DHCP. A static configuration
        is only needed in pass-through mode without DHCP.
        """
        if not self.mgmt_passthrough or self.mgmt_dhcp:
            return

        if self.mgmt_address_ipv4 and "/" in self.mgmt_address_ipv4:
            ipv4_addr, ipv4_prefix_len = self.mgmt_address_ipv4.split("/")
            self.wait_write(f"interface {self.mgmt_intf}", wait="(config)#")
            self.wait_write("subinterface 0", wait="#")
            self.wait_write(
                f"ipv4 address {ipv4_addr} prefix-length {ipv4_prefix_len}", wait="#"
            )
            self.wait_write("top", wait="#")
        if self.mgmt_gw_ipv4 and "." in self.mgmt_gw_ipv4:
            self.wait_write(
                "network-instance management protocol STATIC default", wait="#"
            )
            self.wait_write("static-route 0.0.0.0/0 next-hop-index 1", wait="#")
            self.wait_write(f"next-hop {self.mgmt_gw_ipv4}", wait="#")
            self.wait_write(f"interface {self.mgmt_intf}", wait="#")
            self.wait_write("top", wait="#")

    def stop_ztp(self):
        """Stop and disable Zero Touch Provisioning

        ZTP keeps retrying DHCP based provisioning in the background and the
        system configuration should not be changed while it is running.
        Stopping it also disables it for subsequent reboots.
        """
        self.wait_write("request system ztp stop", wait="#")
        (ridx, match, res) = self.tn.expect([rb"\[no,yes\]"], 30)
        if match:
            self.wait_write("yes", wait=None)
            # ZTP does some internal cleanup before stopping which might take
            # up to 10 minutes, a message confirms once it is stopped
            self.logger.info("waiting for ZTP to stop...")
            self.wait_write(
                "", wait="Zero Touch Provisioning (ZTP) stopped", timeout=600
            )
        else:
            self.logger.info(
                "no ZTP stop confirmation prompt seen, assuming ZTP is not running"
            )
            self.wait_write("", wait=None)

    def bootstrap_config(self):
        """Do the actual bootstrap config"""
        self.logger.info("applying bootstrap configuration")

        self.stop_ztp()

        self.wait_write("config", wait="#")

        self.bootstrap_mgmt_interface()

        self.wait_write(f"system hostname {self.hostname}", wait="#")

        # changing the initial root password is required before the front
        # panel (swp) ports can be enabled
        self.wait_write(
            f"system aaa authentication admin-user admin-password {self.password}",
            wait="#",
        )

        if self.username != DEFAULT_USERNAME:
            self.wait_write(
                f"system aaa authentication user {self.username}", wait="#"
            )
            self.wait_write(f"password {self.password}", wait="#")
            self.wait_write("role SYSTEM_ROLE_ADMIN", wait="#")
            self.wait_write("top", wait="#")

        # ssh is disabled on the management interface by default
        self.wait_write("system ssh-server enable true", wait="#")
        self.wait_write("system ssh-server permit-root-login true", wait="#")

        self.wait_write("commit", wait="#")
        self.wait_write("end", wait="#")

    def startup_config(self):
        """Load additional config provided by user."""

        if not os.path.exists(STARTUP_CONFIG_FILE):
            self.logger.trace(f"Startup config file {STARTUP_CONFIG_FILE} is not found")
            return

        self.logger.trace(f"Startup config file {STARTUP_CONFIG_FILE} exists")
        with open(STARTUP_CONFIG_FILE) as file:
            config_lines = file.readlines()
            config_lines = [line.rstrip() for line in config_lines]
            self.logger.trace(f"Parsed startup config file {STARTUP_CONFIG_FILE}")

        self.logger.info(f"Writing lines from {STARTUP_CONFIG_FILE}")

        self.wait_write("config", wait="#")
        # Apply lines from file
        for line in config_lines:
            self.wait_write(line, wait="#")
        # Commit and end
        self.wait_write("commit", wait="#")
        # the commit is atomic -- a single invalid line rejects the complete
        # startup configuration, make sure this does not go unnoticed
        (ridx, match, res) = self.tn.expect(
            [b"Commit complete", b"Aborted:", b"Error:"], 60
        )
        if match and ridx > 0:
            self.logger.error(
                f"startup config commit failed: {res.decode(errors='ignore')}"
            )
        self.wait_write("end", wait="#")


class ArcOS(vrnetlab.VR):
    def __init__(self, hostname, username, password, conn_mode):
        super(ArcOS, self).__init__(username, password)
        self.vms = [ArcOS_vm(hostname, username, password, conn_mode)]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="")
    parser.add_argument(
        "--trace", action="store_true", help="enable trace level logging"
    )
    # containerlab's arrcus_arcos kind does not pass any arguments to the
    # container, fall back to the environment variables where available
    parser.add_argument(
        "--hostname",
        default=os.getenv("HOSTNAME", "arrcus_aros"),
        help="Router hostname",
    )
    parser.add_argument(
        "--username", default=os.getenv("USERNAME", "root"), help="Username"
    )
    parser.add_argument(
        "--password", default=os.getenv("PASSWORD", "YouReallyNeedToChangeThis"),
        help="Password"
    )
    parser.add_argument(
        "--connection-mode",
        default=os.getenv("CONNECTION_MODE", "tc"),
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

    vr = ArcOS(args.hostname, args.username, args.password, args.connection_mode)
    vr.start()
