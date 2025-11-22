#!/usr/bin/env python3
import datetime
import logging
import os
import re
import signal
import sys

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
            use_scrapli=True,
        )

        self.hostname = hostname
        self.conn_mode = conn_mode
        self.device_type = device_type
        # device supports up to 16 interfaces (1 management interface + 15 data interfaces)
        self.num_nics = 15
        self.running = False
        self.spins = 0

    def bootstrap_spin(self):
        if self.spins > 300:
            # too many spins with no result -> give up
            self.stop()
            self.start()
            return

        # Expect different prompt based on device type
        device_prompt = b"Switch>" if self.device_type == "switch" else b"Router>"

        (ridx, match, res) = self.con_expect(
            [
                rb"Would you like to enter the initial configuration dialog\? \[yes/no\]:",
                b"Press RETURN to get started!",
                device_prompt,
            ],
        )

        if match:
            if ridx == 0:
                self.logger.info("Skipping initial configuration dialog")
                self.wait_write("no", wait=None)
            elif ridx == 1:
                self.logger.info("Entering user EXEC mode")
                for _ in range(3):
                    self.wait_write("\r", wait=None)
            elif ridx == 2:
                self.apply_config()

                # startup time
                startup_time = datetime.datetime.now() - self.start_time
                self.logger.info(f"Startup complete in: {startup_time}")
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
        scrapli_timeout = os.getenv("SCRAPLI_TIMEOUT", vrnetlab.DEFAULT_SCRAPLI_TIMEOUT)
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
