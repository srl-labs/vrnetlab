#!/usr/bin/env python3
import datetime
import logging
import os
import re
import select
import signal
import sys
import time

import vrnetlab
from scrapli.driver.core import IOSXEDriver

STARTUP_CONFIG_FILE = "/config/startup-config.cfg"


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


class VIOS_vm(vrnetlab.VM):
    def __init__(self, hostname: str, username: str, password: str, conn_mode: str, device_type: str = None):
        # Set up logger early
        self.logger = logging.getLogger()

        disk_image = None
        for e in os.listdir("/"):
            if re.search(".qcow2$", e):
                disk_image = "/" + e
        if not disk_image:
            raise Exception("No disk image found")

        # Determine device type and configure accordingly
        if device_type is None:
            # Auto-detect from image filename
            device_type = "switch" if re.search(r"(viosl2|vios_l2)", disk_image, re.IGNORECASE) else "router"
            self.logger.info(f"Auto-detected device type '{device_type}' from image: {disk_image}")

        # Configure based on device type
        match device_type:
            case "switch":
                ram = 768
                self.logger.info("Configuring switch with 768MB RAM")
            case "router":
                ram = 512
                self.logger.info("Configuring router with 512MB RAM")
            case _:
                raise ValueError(f"Invalid device_type '{device_type}'. Must be 'router' or 'switch'")

        super(VIOS_vm, self).__init__(
            username=username,
            password=password,
            disk_image=disk_image,
            smp="1",
            ram=ram,
            driveif="virtio",
        )

        self.hostname = hostname
        self.conn_mode = conn_mode
        self.device_type = device_type
        # device supports up to 16 interfaces (1 management interface + 15 data interfaces)
        self.num_nics = 15
        self.running = False
        self.spins = 0

    def start(self):
        super().start()
        if self.running:
            return
        self._console_socket()
        self._bootstrap_buffer = b""
        self._last_output = time.monotonic()
        self._last_nudge = self._last_output
        self._nudge_armed = False
        self._nudge_count = 0

    def _console_socket(self):
        transport = self.scrapli_tn.transport
        sock = getattr(getattr(transport, "socket", None), "sock", None)
        if sock is None:
            raise RuntimeError("vIOS bootstrap requires an open scrapli telnet socket")
        return sock

    def _read_console(self):
        # Poll like common._Console, but never fall back to a blocking read.
        transport = self.scrapli_tn.transport
        sock = self._console_socket()
        pending = getattr(transport, "_raw_buf", b"") or getattr(transport, "_cooked_buf", b"")
        if not pending:
            readable, _, _ = select.select([sock], [], [], 1)
            if not readable:
                return b""
        # Readiness can mean only telnet negotiation, without console text.
        # Keep a second recv in scrapli from blocking in that case.
        timeout = sock.gettimeout()
        try:
            sock.setblocking(False)
            return self.scrapli_tn.channel.read()
        except BlockingIOError:
            return b""
        finally:
            sock.settimeout(timeout)

    def bootstrap_spin(self):
        if self.running:
            return

        res = self._read_console()
        now = time.monotonic()
        if res:
            self.write_to_stdout(res)
            self._last_output = now
            self._bootstrap_buffer += res

        # Expect different prompt based on device type.
        device_prompt = b"Switch>" if self.device_type == "switch" else b"Router>"
        patterns = [
            rb"Would you like to enter the initial configuration dialog\? \[yes/no\]:",
            b"Press RETURN to get started!",
            device_prompt,
        ]
        while self._bootstrap_buffer:
            matches = [(match.start(), ridx, match) for ridx, pattern in enumerate(patterns)
                       if (match := re.search(pattern, self._bootstrap_buffer))]
            if not matches:
                break
            _, ridx, match = min(matches, key=lambda item: item[:2])
            self._bootstrap_buffer = self._bootstrap_buffer[match.end():]
            if ridx == 0:
                self.logger.info("Skipping initial configuration dialog")
                self.wait_write("no", wait=None)
                self._nudge_armed = True
            elif ridx == 1:
                self.logger.info("Entering user EXEC mode")
                self._nudge_armed = True
                for _ in range(3):
                    self.wait_write("\r", wait=None)
            elif ridx == 2:
                self._nudge_armed = False
                self.apply_config()

                # startup time
                startup_time = datetime.datetime.now() - self.start_time
                self.logger.info(f"Startup complete in: {startup_time}")
                # mark as running
                self.running = True
                return

        self._bootstrap_buffer = self._bootstrap_buffer[-4096:]
        if now - self._last_output >= 900:
            self.logger.warning("No console output for 900 seconds; restarting")
            self.stop()
            self.start()
            return

        if (self._nudge_armed and self._nudge_count < 6
                and now - max(self._last_output, self._last_nudge) >= 20):
            self._nudge_count += 1
            self.logger.info(f"Nudging console with RETURN ({self._nudge_count}/6)")
            self.wait_write("\r", wait=None)
            self._last_nudge = now

    def apply_config(self):
        scrapli_timeout = vrnetlab.getenv_uint("SCRAPLI_TIMEOUT", vrnetlab.DEFAULT_SCRAPLI_TIMEOUT)
        self.logger.info(
            f"Scrapli timeout is {scrapli_timeout}s (default {vrnetlab.DEFAULT_SCRAPLI_TIMEOUT}s)"
        )

        # init scrapli
        vios_scrapli_dev = {
            "host": "127.0.0.1",
            "auth_bypass": True,
            "auth_strict_key": False,
            "timeout_socket": scrapli_timeout,
            "timeout_transport": scrapli_timeout,
            "timeout_ops": scrapli_timeout,
        }

        con = IOSXEDriver(**vios_scrapli_dev)
        con.commandeer(conn=self.scrapli_tn)

        if not os.path.exists(STARTUP_CONFIG_FILE):
            self.logger.fatal("Failed to find startup configuration file")
            con.close()
            return
        
        with open(STARTUP_CONFIG_FILE, "r") as config:
            res = con.send_configs(config.readlines())
            res += con.send_commands(["write memory"])

        for response in res:
            self.logger.info(f"CONFIG:{response.channel_input}")
            self.logger.info(f"RESULT:{response.result}")

        # close the scrapli connection
        con.close()


class VIOS(vrnetlab.VR):
    def __init__(self, hostname: str, username: str, password: str, conn_mode: str, device_type: str = None):
        super(VIOS, self).__init__(username, password)
        self.vms = [VIOS_vm(hostname, username, password, conn_mode, device_type)]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="")
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Enable trace level logging",
        default=os.getenv("TRACE", "false").lower() == "true",
    )
    parser.add_argument(
        "--username", help="Username", default=os.getenv("USERNAME", "admin")
    )
    parser.add_argument(
        "--password", help="Password", default=os.getenv("PASSWORD", "admin")
    )
    parser.add_argument(
        "--hostname", help="Device hostname", default=os.getenv("HOSTNAME", "vios")
    )
    parser.add_argument(
        "--connection-mode",
        help="Connection mode to use in the datapath",
        default=os.getenv("CONNECTION_MODE", "tc"),
    )
    parser.add_argument(
        "--type",
        help="Device type (router or switch). If not specified, auto-detected from image filename.",
        default=os.getenv("DEVICE_TYPE", None),
        choices=["router", "switch"],
    )
    args = parser.parse_args()

    LOG_FORMAT = "%(asctime)s: %(module)-10s %(levelname)-8s %(message)s"
    logging.basicConfig(format=LOG_FORMAT)
    logger = logging.getLogger()

    logger.setLevel(logging.DEBUG)
    if args.trace:
        logger.setLevel(1)

    vr = VIOS(
        hostname=args.hostname,
        username=args.username,
        password=args.password,
        conn_mode=args.connection_mode,
        device_type=args.type,
    )
    vr.start()
