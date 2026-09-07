#!/usr/bin/env python3
import logging
import os
import re
import select
import signal
import sys
import time
import uuid
from contextlib import contextmanager

import vrnetlab
from common import Credentials, DEFAULT_PASSWORD, DEFAULT_USERNAME, DEF_POLICY_COMPLIANT_PASSWORD, TRACE_LEVEL
from fos_cli_driver import FOSCliDriver
from fos_commander import FOSCommander
from features import (
    CredentialsFeature,
    ConfigureMgmtDns,
    ConfigSaveFeature,
    FormatDisks,
    FeatureFileWatcher,
    DefaultConfig,
    ConfigureTestLicenseFortiGuard,
    SetLicense,
    WaitForLicenseValidation,
    ConfigureMgmtNetwork,
    ReconfigureMgmtNetwork,
    MoveMgmtToVrf1,
    ApplyStartupConfig,
)
from host_forwarded_bridge import HostForwardedBridge
from net_mgmt_strategy import NetMgmtStrategy
from passthrough_redirect import PassthroughRedirect
from terminal import Terminal
from tftp import TFTPServer


def handle_SIGCHLD(_unused_signal, _unused_frame):
    os.waitpid(-1, os.WNOHANG)


def handle_SIGTERM(_unused_signal, _unused_frame):
    sys.exit(0)


signal.signal(signal.SIGINT, handle_SIGTERM)
signal.signal(signal.SIGTERM, handle_SIGTERM)
signal.signal(signal.SIGCHLD, handle_SIGCHLD)

TRACE_LEVEL_NUM = TRACE_LEVEL
logging.addLevelName(TRACE_LEVEL_NUM, "\x1b[1;35m\tTRACE\x1b[0m")
LOG_LEVEL_NAMES = {
    "TRACE": TRACE_LEVEL_NUM,
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARN": logging.WARN,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.FATAL,
}


def trace(self, message, *args, **kws):
    # Yes, logger takes its '*args' as 'args'.
    if self.isEnabledFor(TRACE_LEVEL_NUM):
        self.log(TRACE_LEVEL_NUM, message, *args, **kws)


logging.Logger.trace = trace
MGMT_PASSTHROUGH_DEFAULT = True
TFTP_PORT = 69
TFTP_TID_RANGE = (52400, 52500)
TFTP_DIRECTORY = "/tftpboot"


def parse_log_level(value):
    if value is None or str(value).strip() == "":
        return logging.DEBUG
    value = str(value).strip()
    try:
        return int(value)
    except ValueError:
        pass
    normalized = value.upper()
    if normalized in LOG_LEVEL_NAMES:
        return LOG_LEVEL_NAMES[normalized]
    raise ValueError(
        "Invalid FOS_LOG_LEVEL. Use TRACE, DEBUG, INFO, WARN, ERROR, "
        "another Python logging level name, or a numeric level."
    )


def apply_debug_feature_cutoff(features, stop_feature, logger):
    """Limit bootstrap features to those up to and including ``stop_feature``."""
    if stop_feature is None or str(stop_feature).strip() == "":
        return features

    stop_feature = str(stop_feature).strip()
    for index, feature in enumerate(features):
        if feature.name == stop_feature:
            selected = features[:index + 1]
            skipped = features[index + 1:]
            logger.warning(
                "FOS_DEBUG_FEATURE=%s; stopping after feature %s. Skipping: %s",
                stop_feature,
                stop_feature,
                ", ".join(feature.name for feature in skipped) or "(none)",
            )
            return selected

    available = ", ".join(feature.name for feature in features)
    raise ValueError(f"Unknown FOS_DEBUG_FEATURE '{stop_feature}'. Available features: {available}")


class FortiOSConsole(vrnetlab._Console):
    def __init__(self, driver):
        super().__init__(driver)
        self._output_enabled = True

    def _read(self):
        data = self._driver.channel.read()
        if data and self._output_enabled:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
        return data

    def read_very_eager(self):
        transport_socket = getattr(self._driver.transport, "socket", None)
        socket = getattr(transport_socket, "sock", None)
        if socket is not None:
            readable, _, _ = select.select([socket], [], [], 0)
            if not readable:
                return b""
        return self._read()

    def read_blocking(self):
        return self._read()

    @contextmanager
    def suppress_output(self):
        output_enabled = self._output_enabled
        self._output_enabled = False
        try:
            yield
        finally:
            self._output_enabled = output_enabled


class FortiOS_vm(vrnetlab.VM):
    def __init__(self, hostname: str, username, password, conn_mode, mgmt_net: NetMgmtStrategy):
        disk_image = None
        for e in os.listdir("."):
            if re.search(".qcow2$", e):
                disk_image = "./" + e
        if disk_image is None:
            raise RuntimeError("Could not find image to boot")
        super(FortiOS_vm, self).__init__(
            username,
            password,
            disk_image=disk_image,
            ram=2048,
            driveif="virtio",
            # fortios fails to respond to network requests if the pci bus is setup :D
            provision_pci_bus=False,
            mgmt_passthrough=mgmt_net.mgmt_passthrough
        )
        self.tn = FortiOSConsole(self.scrapli_tn)

        self.logger.info(f"Launching. commandline: {' '.join(sys.argv)}")
        self.conn_mode = conn_mode
        self.hostname = hostname
        self.num_nics = 12
        self.nic_type = "virtio-net-pci"
        self.highest_port = 0
        self.qemu_args.extend(["-uuid", os.getenv("FOS_UUID") or str(uuid.uuid4())])
        self.spins = 0
        self.stopped = False
        self.waiting_for = False
        self._mgmt_net = mgmt_net
        self._mgmt_net.configure_vm_mgmt(self)
        self.mgmt_dns_primary = os.getenv("FOS_MGMT_DNS_PRIMARY", "1.1.1.1")
        self.mgmt_dns_secondary = os.getenv(
            "FOS_MGMT_DNS_SECONDARY", os.getenv("FOS_MGMG_DNS_SECONDARY", "8.8.8.8")
        )
        self.terminal = Terminal(self.tn, self.logger, default_wait=self.wait_pattern)
        desired_password = DEFAULT_PASSWORD if password is None else password
        self._bootstrap_username = DEFAULT_USERNAME
        self._bootstrap_password = DEF_POLICY_COMPLIANT_PASSWORD
        self.credentials = Credentials(self._bootstrap_username, self._bootstrap_password)
        self.desired_credentials = Credentials(username or DEFAULT_USERNAME, desired_password)
        self.commander = FOSCommander(
            terminal=self.terminal,
            logger=logger,
        )
        self.driver = FOSCliDriver(
            self.terminal,
            self.commander,
            self.credentials,
            logger,
            self._bootstrap_password,
            self.activate_blank_credentials,
            self.activate_bootstrap_credentials,
        )
        configure_dns = ConfigureMgmtDns(self, self.commander)
        self._features = [
            FormatDisks(self, self.commander),
            CredentialsFeature(self, self.commander),
            ConfigureMgmtNetwork(self, self.commander),
            configure_dns,
            ConfigureTestLicenseFortiGuard(self, self.commander),
            SetLicense(self, self.commander),
            DefaultConfig(self, self.commander),
            ReconfigureMgmtNetwork(self, self.commander),
            WaitForLicenseValidation(self, self.commander),
            MoveMgmtToVrf1(self, self.commander),
            configure_dns.undo(),
            ConfigSaveFeature(self, self.commander),
            ApplyStartupConfig(self, self.commander),
        ]
        self._features = apply_debug_feature_cutoff(
            self._features,
            os.getenv("FOS_DEBUG_FEATURE"),
            self.logger,
        )
        self._file_watcher = FeatureFileWatcher(self._features, self.logger)
        self.commander.start(self._features)
        # set up the extra empty disk image
        # for fortigate logs
        vrnetlab.run_command(
            ["qemu-img", "create", "-f", "qcow2", "empty.qcow2", "30G"]
        )

        # Comma-separated list of disk sizes to install in the machine. as accepted by qemu-img create.
        disk_specs = os.getenv("FOS_DISK_SPECS", "").split(",")
        if disk_specs[0] == '':
            self.logger.warn(
                "No additional disks configured. Use FOS_DISK_SPECS to specify a comma-separated list of disk sizes")
            return
        index = 0
        for spec in disk_specs:
            index += 1
            # set up the extra empty disk image
            # for fortigate logs
            vrnetlab.run_command(
                ["qemu-img", "create", "-f", "qcow2", f"empty{index}.qcow2", spec]
            )

            self.qemu_args.extend(
                [
                    "-drive",
                    f"if=virtio,format=qcow2,file=empty{index}.qcow2,index={index}",
                ]
            )

            index += 1

    def bootstrap_spin(self):
        """This function should be called periodically to do work.

        returns False when it has failed and given up, otherwise True
        """
        try:
            self.driver.process_state()
        except Exception:
            self.stop()
            raise
        if self.driver.ready:
            self.running = True

    def work(self):
        super().work()
        if self.running:
            self._file_watcher.poll()
            # Runtime features such as get-config share the event-driven CLI
            # scheduler with bootstrap and must continue receiving serial work.
            if not self.driver.ready:
                self.bootstrap_spin()

    def connect_serial_console(self):
        try:
            self.terminal.close()
        except Exception:
            pass

        for attempt in range(1, vrnetlab.MAX_RETRIES + 1):
            try:
                self.tn.open()
                break
            except Exception:
                if attempt == vrnetlab.MAX_RETRIES:
                    self.logger.exception("Failed to connect to VM serial port.")
                    self.stop()
                    raise
                self.logger.error(
                    f"Unable to connect to VM serial port {5000 + self.num}, "
                    f"retrying in a second (attempt {attempt})"
                )
                time.sleep(1)

        self.stopped = False
        self.terminal.write(b"\r")

    def activate_desired_credentials(self):
        self.credentials.username = self.desired_credentials.username
        self.credentials.password = self.desired_credentials.password

    def activate_bootstrap_credentials(self):
        self.credentials.username = self._bootstrap_username
        self.credentials.password = self._bootstrap_password

    def activate_blank_credentials(self):
        self.credentials.password = ""

    def gen_mgmt(self):
        return self._mgmt_net.gen_mgmt(self)

    @staticmethod
    def mgmt_gateway_destination(gateway, mgmt_address):
        _address, separator, prefix = str(mgmt_address).partition("/")
        if separator and prefix:
            return f"{gateway}/{prefix}"
        return f"{gateway}/{'128' if ':' in str(gateway) else '32'}"

    def stop(self):
        self.stopped = True
        super().stop()


class FortiOS(vrnetlab.VR):
    def __init__(self, hostname, username, password, conn_mode, mgmt_net: NetMgmtStrategy):
        super(FortiOS, self).__init__(username, password, mgmt_passthrough=mgmt_net.mgmt_passthrough)
        self.logger.debug("Hostname")
        self.logger.debug(hostname)
        self.vms = [FortiOS_vm(hostname, username, password, conn_mode, mgmt_net=mgmt_net)]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="")
    parser.add_argument(
        "--trace", action="store_true", help="enable trace level logging"
    )
    parser.add_argument("--hostname", default="vr-fortinet", help="Fortinet hostname")
    parser.add_argument("--username", default=None, help="Username")
    parser.add_argument("--password", default=None, help="Password", nargs="?")
    parser.add_argument(
        "--connection-mode",
        default="tc",
        help="Connection mode to use in the datapath",
    )
    args = parser.parse_args()

    LOG_FORMAT = "%(asctime)s: %(module)-10s %(levelname)-8s %(message)s"
    logging.basicConfig(format=LOG_FORMAT)
    logger = logging.getLogger()

    logger.setLevel(parse_log_level(os.getenv("FOS_LOG_LEVEL")))

    mgmt_passthrough = (
        os.environ.get("CLAB_MGMT_PASSTHROUGH", "").lower() == "true"
        if os.environ.get("CLAB_MGMT_PASSTHROUGH")
        else MGMT_PASSTHROUGH_DEFAULT
    )

    tftp_server = None
    mgmt_net = None
    if mgmt_passthrough:
        mgmt_net = PassthroughRedirect([f"udp:{TFTP_PORT}", f"udp:{'-'.join(map(str, TFTP_TID_RANGE))}"])
        mgmt_net.prep()
        tftp_server = TFTPServer(mgmt_net=mgmt_net,
                                 srv_port=TFTP_PORT,
                                 tid_range=TFTP_TID_RANGE,
                                 directory=TFTP_DIRECTORY)
    else:
        mgmt_net = HostForwardedBridge()
        mgmt_net.prep()
        tftp_server = TFTPServer(srv_port=TFTP_PORT,
                                 mgmt_net=mgmt_net,
                                 directory=TFTP_DIRECTORY)

    vr = FortiOS(
        args.hostname, args.username, args.password, conn_mode=args.connection_mode, mgmt_net=mgmt_net
    )
    tftp_server.launch()
    vrnetlab.boot_delay()
    vr.start()
