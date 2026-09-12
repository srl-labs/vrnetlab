#!/usr/bin/env python3

import datetime
import logging
import os
import re
import signal
import sys
import ipaddress

import vrnetlab

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


class FTOS_vm(vrnetlab.VM):
    def __init__(self, hostname, username, password, conn_mode):
        disk_image = ""
        for e in os.listdir("/"):
            if re.search(".qcow2$", e):
                disk_image = "/" + e
        if disk_image == "":
            logging.getLogger().info("Disk image was not found")
            exit(1)
        # OS10 (10.6.x, kernel 6.1) hangs right after kernel load with -cpu host on AMD EPYC (Zen) hosts;
        # the generic qemu64 model boots reliably. Override with the QEMU_CPU env var if needed.
        super(FTOS_vm, self).__init__(
            username, password, disk_image=disk_image, ram=4096, smp="4", cpu="qemu64",
            # OS10 platform services (NAS/PPM/IFM) are unreliable unless the VM sees the full
            # S5248F port set; vrnetlab fills the gaps with dummy tap NICs.
            min_dp_nics=56,
        )
        self.credentials = [["admin", "admin"]]
        self.hostname = hostname
        self.conn_mode = conn_mode
        # mgmt + 56 (S5248 platform) that show up in the vm, may as well populate them all in vrnetlab right away
        # max interface numbers depend on the qemu disk image of platform used.
        # available OS10 virtualization platform options: S4000,S4128,S5212,S5224,S5248,S6000,S6010
        self.num_nics = 56
        self.nic_type = "e1000"

        overlay_disk_image = re.sub(r"(\.[^.]+$)", r"-overlay\1", disk_image)
        # boot harddrive first
        self.qemu_args.extend(["-boot", "c"])
        replace_index = self.qemu_args.index(
            "if=ide,file={}".format(overlay_disk_image)
        )
        self.qemu_args[
            replace_index
        ] = "file={},if=none,id=drive-sata-disk0,format=qcow2".format(
            overlay_disk_image
        )
        self.qemu_args.extend(["-device", "ahci,id=ahci0,bus=pci.0"])
        self.qemu_args.extend(
            [
                "-device",
                "ide-hd,drive=drive-sata-disk0,bus=ahci0.0,id=drive-sata-disk0,bootindex=1",
            ]
        )

    def gen_mgmt(self):
        """
        Augment the parent class function to add gRPC port forwarding

        TCP ports forwarded:
        443 - OS10 REST API
        830 - Netconf
        """
        self.mgmt_subnet = "169.254.127.0/24"
        self.mgmt_tcp_ports = [443,830]
        # call parent function to generate the mgmt interface
        res = super(FTOS_vm, self).gen_mgmt()

        # append gRPC forwarding if it was not added by common lib. confirm gNMI agent port number, default port is different than 50051?
        # gRPC Network Management Interface agent requires the switch in non default SmartFabric switch-operating-mode
        network = ipaddress.ip_network(self.mgmt_subnet)
        guest = str(network[self.mgmt_guest_ip])
        if f"hostfwd=tcp::50051-{guest}:50051" not in res[-1]:
            res[-1] = res[-1] + f",hostfwd=tcp::17051-{guest}:50051"
            vrnetlab.run_command(
                ["socat", "TCP-LISTEN:50051,fork", "TCP:127.0.0.1:17051"],
                background=True,
            )
        return res

    def bootstrap_spin(self):
        """This function should be called periodically to do work."""
        if self.spins > 300:
            # too many spins with no result ->  give up
            self.stop()
            self.start()
            return

        (ridx, match, res) = self.tn.expect([b"login:"], 1)
        if match:  # got a match!
            if ridx == 0:  # login
                self.logger.debug("matched login prompt")
                try:
                    username, password = self.credentials.pop(0)
                except IndexError as exc:
                    self.logger.error("no more credentials to try")
                    return
                self.logger.debug("trying to log in with %s / %s", username, password)
                self.wait_write(username, wait=None)
                self.wait_write(password, wait="Password:")

                # run main config!
                self.bootstrap_config()
                self.startup_config()
                # close telnet connection
                self.tn.close()
                # startup time?
                startup_time = datetime.datetime.now() - self.start_time
                self.logger.info("Startup complete in: %s", startup_time)
                # mark as running
                self.running = True
                return

        # no match, if we saw some output from the router it's probably
        # booting, so let's give it some more time
        if res != b"":
            self.logger.trace("OUTPUT: %s", res.decode())
            # reset spins if we saw some output
            self.spins = 0

        self.spins += 1

        return

    def bootstrap_config(self):
        """Do the actual bootstrap config"""
        self.logger.info("applying bootstrap configuration once system is ready")
        self.wait_write("", None)
        self.wait_write("configure", wait="OS10#", hold="loading-OS10#")
        self.wait_write(f"hostname {self.hostname}")
        self.wait_write("service simple-password")
        self.wait_write(
            f"username {self.username} password {self.password} role sysadmin priv-lv 15"
        )

        # No need to reconfigure mgmt IP; causes issues with parallel Netlab provisioning
        self.wait_write("copy running-configuration startup-configuration")
        self.wait_write("")

    def startup_config(self):
        """Load additional config provided by user."""

        if not os.path.exists(STARTUP_CONFIG_FILE):
            self.logger.trace("Startup config file %s is not found", STARTUP_CONFIG_FILE)
            return

        self.logger.trace("Startup config file %s exists", STARTUP_CONFIG_FILE)
        with open(STARTUP_CONFIG_FILE) as file:
            config_lines = file.readlines()
            config_lines = [line.rstrip() for line in config_lines]
            self.logger.trace("Parsed startup config file %s", STARTUP_CONFIG_FILE)

        self.logger.info("Writing lines from %s", STARTUP_CONFIG_FILE)

        self.wait_write("configure terminal")
        # Apply lines from file
        for line in config_lines:
            self.wait_write(line)
        # End and Save
        self.wait_write("end")
        self.wait_write("copy running-config startup-config")
        self.wait_write("")


class FTOS(vrnetlab.VR):
    def __init__(self, hostname, username, password, conn_mode):
        super(FTOS, self).__init__(username, password)
        self.vms = [FTOS_vm(hostname, username, password, conn_mode)]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="")
    parser.add_argument(
        "--trace", action="store_true", help="enable trace level logging"
    )
    parser.add_argument("--hostname", default="vr-ftosv", help="Router hostname")
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

    logger.debug(f"Environment variables: {os.environ}")
    vrnetlab.boot_delay()

    vr = FTOS(args.hostname, args.username, args.password, args.connection_mode)
    vr.start()
