#!/usr/bin/env python3
# ==============================================================================
# launch.py – Juniper Apstra vrnetlab launcher
#
# Modelled after the srl-labs/vrnetlab ubuntu/docker/launch.py pattern.
#
# Key design decisions for Apstra
# ────────────────────────────────
# 1. NO data-plane NICs  – Apstra is a management/orchestration appliance.
#    It does not participate in the data plane; there is no need for eth1+
#    tap interfaces.  Setting num_nics=0 prevents vrnetlab from trying to
#    wire up interfaces that will never exist.
#
# 2. HIGH RAM            – Apstra's minimum supported RAM is 16 GB.
#    The default vrnetlab value (4 GB) will cause the VM to crash or boot
#    into a severely degraded state.  We default to 16 384 MB.
#
# 3. NO CLI bootstrap    – Apstra is configured exclusively through its
#    Web UI / REST API.  We must NOT attempt to send CLI commands over the
#    serial console.  bootstrap_spin() simply waits for a login prompt and
#    then marks the VM as running.
#
# 4. LONG boot timeout   – First boot can take 8–10 minutes as Apstra
#    initialises its internal PostgreSQL database and services.
#
# 5. conn_mode           – Passed explicitly from argparse --connection-mode
#    all the way into the VM subclass, exactly as the ubuntu launcher does.
#    The base class vrnetlab.VM does NOT set self.conn_mode itself; the
#    subclass is responsible for setting it after super().__init__().
# ==============================================================================

import datetime
import logging
import os
import re
import signal
import sys

import vrnetlab


# ── signal handlers (mirrors ubuntu/docker/launch.py exactly) ─────────────────

def handle_SIGCHLD(signal, frame):
    os.waitpid(-1, os.WNOHANG)


def handle_SIGTERM(signal, frame):
    sys.exit(0)


signal.signal(signal.SIGINT, handle_SIGTERM)
signal.signal(signal.SIGTERM, handle_SIGTERM)
signal.signal(signal.SIGCHLD, handle_SIGCHLD)


# ── TRACE log level (mirrors ubuntu/docker/launch.py exactly) ─────────────────

TRACE_LEVEL_NUM = 9
logging.addLevelName(TRACE_LEVEL_NUM, "TRACE")


def trace(self, message, *args, **kws):
    if self.isEnabledFor(TRACE_LEVEL_NUM):
        self._log(TRACE_LEVEL_NUM, message, args, **kws)


logging.Logger.trace = trace


# ── tunables ──────────────────────────────────────────────────────────────────

# Default RAM in MB.  Apstra minimum is 16 GB.
DEFAULT_RAM_MB = 16384

# How long (seconds) to wait for the VM to reach a login prompt.
# 600 s (10 min) covers even the slowest first-boot database init.
BOOT_TIMEOUT_S = 600


# ── VM subclass ───────────────────────────────────────────────────────────────

class Apstra_vm(vrnetlab.VM):
    def __init__(self, username, password, conn_mode):
        # ── locate the Apstra disk image ──────────────────────────────────────
        # Mirrors ubuntu launch.py: scan / for a matching qcow2 filename.
        disk_image = None
        for entry in os.listdir("/"):
            if re.search(r"^aos_server.*\.qcow2$", entry):
                disk_image = "/" + entry
                break

        if disk_image is None:
            raise RuntimeError(
                "No Apstra disk image found at /aos_server*.qcow2. "
                "Did you copy the qcow2 into the docker/ build context?"
            )

        # ── initialise the vrnetlab base VM ───────────────────────────────────
        # super().__init__() sets up self.logger, self.qemu_args, and all
        # other base attributes.  Nothing on self must be accessed before this.
        super(Apstra_vm, self).__init__(
            username,
            password,
            disk_image=disk_image,
            ram=DEFAULT_RAM_MB,
        )

        # ── Apstra-specific settings (all after super().__init__()) ───────────
        self.logger.info(f"Using Apstra disk image: {disk_image}")

        # conn_mode MUST be set by the subclass – the base class does not set
        # it.  We receive it from argparse via Apstra() exactly as the ubuntu
        # launcher does.
        self.conn_mode = conn_mode

        # Apstra is a management appliance – no data-plane NICs needed.
        self.num_nics = 0

        self.nic_type = "virtio-net-pci"

        # Add Apstra agent binary protocol ports (Sysdb) to the hostfwd rules
        for port in range(29730, 29740):          
            self.mgmt_tcp_ports.append(port)

        # ── persistent overlay ────────────────────────────────────────────────
        if os.path.isdir("/state"):
            persistent_overlay = "/state/apstra_overlay.qcow2"

            if not os.path.exists(persistent_overlay):
                vrnetlab.run_command([
                    "qemu-img", "create",
                    "-f", "qcow2",
                    "-b", disk_image,
                    "-F", "qcow2",
                    persistent_overlay,
                ])
                self.logger.info("Created persistent overlay at %s", persistent_overlay)
            else:
                self.logger.info(
                    "Reusing existing persistent overlay at %s", persistent_overlay
                )

            # Patch the -drive argument that super().__init__() already wrote
            # into self.qemu_args — replace its file= path with the persistent
            # overlay so QEMU actually writes state to /state/
            for i, arg in enumerate(self.qemu_args):
                if "file=" in arg and "-overlay" in arg:
                    self.qemu_args[i] = arg.split("file=")[0] + \
                                        "file=" + persistent_overlay
                    self.logger.info(
                        "Patched qemu_args drive to use persistent overlay"
                    )
                    break
        else:
            self.logger.warning(
                "/state not mounted — overlay is ephemeral and will not "
                "survive clab destroy. Create the bind-mount directory to "
                "enable persistence."
            )

    # ── bootstrap ─────────────────────────────────────────────────────────────

    def bootstrap_spin(self):
        """
        Called repeatedly by the VR main loop until self.running is True.

        We wait for a login prompt on the serial console and then mark the
        VM as running.  We intentionally do NOT log in or send any commands:
        Apstra is managed through its Web UI / REST API, not the serial console.
        """

        if self.spins > 6000:
            # Too many spins with no result – restart and try again.
            # Mirrors the ubuntu launcher's own spin-limit guard.
            self.logger.debug("Too many spins -> restarting VM")
            self.stop()
            self.start()
            return

        (ridx, match, res) = self.tn.expect(
            [
                b"login: ",           # standard Ubuntu getty
                b"Login: ",           # some Apstra versions capitalise
                b"apstra login: ",    # branded hostname at getty
                b"aos-server login: ", # default Apstra hostname
            ],
            1,  # 1-second timeout per spin, like ubuntu launcher
        )

        if match:
            self.logger.debug("Apstra login prompt detected")
            self.running = True
            self.tn.close()
            startup_time = datetime.datetime.now() - self.start_time
            self.logger.info("Startup complete in: %s", startup_time)
            return

        # No match yet – log any console output at TRACE level and keep spinning.
        if res != b"":
            self.logger.trace("OUTPUT: %s" % res.decode())
            self.spins = 0  # reset spin counter when we see output

        self.spins += 1


# ── VR subclass ───────────────────────────────────────────────────────────────

class Apstra(vrnetlab.VR):
    def __init__(self, username, password, conn_mode):
        super(Apstra, self).__init__(username, password)
        self.vms = [Apstra_vm(username, password, conn_mode)]


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Juniper Apstra vrnetlab launcher")
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Enable trace level logging",
    )
    parser.add_argument(
        "--username",
        default="admin",
        help="Apstra admin username (default: admin)",
    )
    parser.add_argument(
        "--password",
        default="admin",
        help="Apstra admin password (default: admin)",
    )
    parser.add_argument(
        "--hostname",
        default="apstra",
        help="VM hostname (passed by containerlab generic_vm kind, not used by Apstra)",
    )
    parser.add_argument(
        "--connection-mode",
        default="tc",
        help="Connection mode to use in the datapath (default: tc)",
    )
    args = parser.parse_args()

    LOG_FORMAT = "%(asctime)s %(name)-10s %(levelname)-8s %(message)s"
    logging.basicConfig(format=LOG_FORMAT)
    logger = logging.getLogger()

    logger.setLevel(logging.DEBUG)
    if args.trace:
        logger.setLevel(1)

    vr = Apstra(
        args.username,
        args.password,
        args.connection_mode,
    )
    vr.start()
