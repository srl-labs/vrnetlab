#!/usr/bin/env python3

import datetime
import logging
import os
import re
import signal
import subprocess
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
    # Yes, logger takes its '*args' as 'args'.
    if self.isEnabledFor(TRACE_LEVEL_NUM):
        self._log(TRACE_LEVEL_NUM, message, args, **kws)


logging.Logger.trace = trace


class Talos_vm(vrnetlab.VM):
    def __init__(
        self,
        nics,
        conn_mode,
    ):
        for e in os.listdir("/"):
            if re.search(".qcow2$", e):
                disk_image = "/" + e

        super(Talos_vm, self).__init__(
            "","",
            disk_image=disk_image, ram=4192
        )

        self.num_nics = nics
        self.nic_type = "virtio-net-pci"
        self.conn_mode = conn_mode

        if "ADD_DISK" in os.environ:
            disk_size = os.getenv("ADD_DISK")

            self.add_disk(disk_size)


    def bootstrap_spin(self):
        """This function should be called periodically to do work."""
        return

    def gen_mgmt(self):
        """
        Augment the parent class function to change the PCI bus
        """
        # call parent function to generate the mgmt interface
        res = super(Talos_vm, self).gen_mgmt()

        # This is a copy paste from ubuntu
        if "bus=pci.1" not in res[-3]:
            res[-3] = res[-3] + ",bus=pci.1"
        return res

    def add_disk(self, disk_size, driveif="ide"):
        additional_disk = f"disk_{disk_size}.qcow2"

        if not os.path.exists(additional_disk):
            self.logger.debug(f"Creating additional disk image {additional_disk}")
            vrnetlab.run_command(
                ["qemu-img", "create", "-f", "qcow2", additional_disk, disk_size]
            )

        self.qemu_args.extend(
            [
                "-drive",
                f"if={driveif},file={additional_disk}",
            ]
        )


class Talos(vrnetlab.VR):
    def __init__(self, nics, conn_mode):
        super(Talos, self).__init__("", "")
        self.vms = [Talos_vm(nics, conn_mode)]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="")
    parser.add_argument(
        "--trace", action="store_true", help="enable trace level logging"
    )
    parser.add_argument("--nics", type=int, default=4, help="Number of NICS")
    parser.add_argument("--username", default="not supported", help="NOOP")
    parser.add_argument("--password", default="not supported", help="NOOP")
    parser.add_argument("--hostname", default="not supported", help="NOOP")
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

    vr = Talos(
        args.nics,
        args.connection_mode,
    )
    vr.start()
