#!/usr/bin/env python3

import datetime
import logging
import os
import re
import signal
import sys

import vrnetlab


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
    if self.isEnabledFor(TRACE_LEVEL_NUM):
        self._log(TRACE_LEVEL_NUM, message, args, **kws)


logging.Logger.trace = trace


class OPNsense_vm(vrnetlab.VM):
    """OPNsense firewall VM.

    Built from the pre-installed OPNsense *nano* serial image (FreeBSD based).
    The base qcow2 has been pre-configured so the first NIC (vtnet0) is the
    OPNsense LAN/management interface running DHCP -- it picks up vrnetlab's
    management address (10.0.0.15) automatically -- and sshd is enabled with
    root login + password auth. Default credentials are root / opnsense.
    """

    def __init__(self, hostname, username, password, nics, conn_mode):
        disk_image = ""
        for e in sorted(os.listdir("/")):
            if re.search(r"\.qcow2$", e):
                disk_image = "/" + e
                break

        super(OPNsense_vm, self).__init__(
            username, password, disk_image=disk_image, ram=2048
        )

        self.num_nics = nics
        self.hostname = hostname
        self.conn_mode = conn_mode
        self.nic_type = "virtio-net-pci"

    def gen_mgmt(self):
        """Augment the parent to keep the mgmt interface on the first bus.

        Like other FreeBSD-based guests, OPNsense enumerates virtio NICs in PCI
        bus order. The parent places the mgmt NIC on a separate bus, which would
        make the OS assign it the *last* index instead of vtnet0. Force it onto
        pci.1 so it becomes vtnet0 (the baked-in LAN/management interface).
        """
        res = super(OPNsense_vm, self).gen_mgmt()
        if "bus=pci.1" not in res[-3]:
            res[-3] = res[-3] + ",bus=pci.1"
        return res

    def bootstrap_spin(self):
        """Called periodically; mark the node running once it reaches login."""
        if self.spins > 600:
            # too many spins with no result -> give up, restart the VM
            self.stop()
            self.start()
            return

        (ridx, match, res) = self.tn.expect([b"login:"], 1)
        if match:
            if ridx == 0:
                self.logger.info("OPNsense reached login prompt")
                self.running = True
                self.tn.close()
                startup_time = datetime.datetime.now() - self.start_time
                self.logger.info("Startup complete in: %s", startup_time)
                return

        if res != b"":
            self.logger.trace("OUTPUT: %s" % res.decode())
            self.spins = 0

        self.spins += 1
        return


class OPNsense(vrnetlab.VR):
    def __init__(self, hostname, username, password, nics, conn_mode):
        super(OPNsense, self).__init__(username, password)
        self.vms = [OPNsense_vm(hostname, username, password, nics, conn_mode)]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="")
    parser.add_argument(
        "--trace", action="store_true", help="enable trace level logging"
    )
    parser.add_argument("--username", default="root", help="Username")
    parser.add_argument("--password", default="opnsense", help="Password")
    parser.add_argument("--hostname", default="opnsense", help="VM Hostname")
    parser.add_argument("--nics", type=int, default=8, help="Number of NICs")
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

    vr = OPNsense(
        args.hostname,
        args.username,
        args.password,
        args.nics,
        args.connection_mode,
    )
    vr.start()
