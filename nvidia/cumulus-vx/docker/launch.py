#!/usr/bin/env python3
# ==============================================================================
# launch.py — Nvidia Cumulus VX vrnetlab launcher
#
#
# Key design decisions for Cumulus VX
# ───────────────────────────────────
# 1. HAS data-plane NICs — Cumulus VX is a network switch OS. Data-plane
#    interfaces (swp1, swp2, …) are provisioned based on CLAB_INTFS.
#
# 2. MODERATE RAM — Default 4096 MB. 4 GB provides comfortable headroom for
#    switchd, NVUE, and routing protocols.
#
# 3. FIRST-BOOT BOOTSTRAP — Cumulus VX enforces password change on first login.
#    We handle this automatically via the serial console, setting the password
#    back to the configured value and disabling expiry.
#
# 4. MEDIUM boot timeout — First boot typically completes within 3–5 minutes.
# ==============================================================================

import datetime
import logging
import os
import re
import signal
import sys
import time

import vrnetlab

# ── signal handlers ────────────────────────────────────────────────────────────


def handle_SIGCHLD(signal, frame):
    os.waitpid(-1, os.WNOHANG)


def handle_SIGTERM(signal, frame):
    sys.exit(0)


signal.signal(signal.SIGINT, handle_SIGTERM)
signal.signal(signal.SIGTERM, handle_SIGTERM)
signal.signal(signal.SIGCHLD, handle_SIGCHLD)


# ── TRACE log level ────────────────────────────────────────────────────────────

TRACE_LEVEL_NUM = 9
logging.addLevelName(TRACE_LEVEL_NUM, "TRACE")


def trace(self, message, *args, **kws):
    if self.isEnabledFor(TRACE_LEVEL_NUM):
        self._log(TRACE_LEVEL_NUM, message, args, **kws)


logging.Logger.trace = trace


# ── tunables ────────────────────────────────────────────────────────────────────

DEFAULT_RAM_MB = 4096


# ── VM subclass ─────────────────────────────────────────────────────────────────


class CumulusVX_vm(vrnetlab.VM):
    def __init__(self, hostname, username, password, conn_mode):
        # ── locate the Cumulus VX disk image ──────────────────────────────────
        disk_image = None
        for entry in os.listdir("/"):
            if re.search(r"^cumulus-linux.*\.qcow2$", entry):
                disk_image = "/" + entry
                break

        if disk_image is None:
            raise RuntimeError(
                "No Cumulus VX disk image found at /cumulus-linux*.qcow2. "
                "Did you copy the qcow2 into the docker/ build context?"
            )

        self.hostname = hostname
        self.conn_mode = conn_mode
        self._bootstrap_done = False

        # ── KVM-aware CPU selection ──────────────────────────────────────────
        # -cpu host requires KVM; without /dev/kvm QEMU exits immediately and
        # the monitor port never opens.  Use "max" for TCG which enables all
        # features the emulator supports.
        if os.path.exists("/dev/kvm"):
            cpu_model = "host"
        else:
            cpu_model = "max"
            logging.getLogger().warning(
                "/dev/kvm not available — using TCG emulation "
                "(switchd performance will be degraded)"
            )

        # ── initialise the vrnetlab base VM ───────────────────────────────────
        super(CumulusVX_vm, self).__init__(
            username,
            password,
            disk_image=disk_image,
            ram=DEFAULT_RAM_MB,
            cpu=cpu_model,
        )

        self.logger.info(f"Using Cumulus VX disk image: {disk_image}")

        # Cumulus VX is a switch — provision enough PCI slots so that
        # the base class dummy-NIC (socket placeholder) logic fills any
        # gaps, giving correct swpX numbering even with sparse topology
        # interface indices (e.g. eth1, eth4, eth6, eth7).
        self.num_nics = 16
        self.nic_type = "virtio-net-pci"

        # NVUE REST API (HTTPS on 8765) — 8080 is already in the base class
        self.mgmt_tcp_ports.append(8765)

        # ── persistent overlay ────────────────────────────────────────────────
        if os.path.isdir("/config"):
            persistent_overlay = "/config/cumulus_overlay.qcow2"

            if not os.path.exists(persistent_overlay):
                vrnetlab.run_command(
                    [
                        "qemu-img",
                        "create",
                        "-f",
                        "qcow2",
                        "-b",
                        disk_image,
                        "-F",
                        "qcow2",
                        persistent_overlay,
                    ]
                )
                self.logger.info("Created persistent overlay at %s", persistent_overlay)
            else:
                self.logger.info(
                    "Reusing existing persistent overlay at %s",
                    persistent_overlay,
                )

            for i, arg in enumerate(self.qemu_args):
                if "file=" in arg and "-overlay" in arg:
                    self.qemu_args[i] = (
                        arg.split("file=")[0] + "file=" + persistent_overlay
                    )
                    self.logger.info(
                        "Patched qemu_args drive to use persistent overlay"
                    )
                    break
        else:
            self.logger.warning(
                "/config not mounted — overlay is ephemeral and will not "
                "survive clab destroy. Create the bind-mount directory to "
                "enable persistence."
            )

    # ── bootstrap ─────────────────────────────────────────────────────────────

    def bootstrap_spin(self):
        """Called repeatedly by the VR main loop until self.running is True.

        Waits for a Cumulus Linux login prompt on the serial console, optionally
        performs first-boot password setup, then waits for switchd to be active
        before marking the VM as running.
        """

        if self.spins > 6000:
            self.logger.debug("Too many spins -> restarting VM")
            self.stop()
            self.start()
            return

        # If first-boot setup is already done, poll switchd directly via
        # the active serial session (no login prompt needed).
        if self._bootstrap_done:
            if not self._switchd_is_ready():
                self.spins += 1
                return
            self.running = True
            self.tn.close()
            startup_time = datetime.datetime.now() - self.start_time
            self.logger.info("Startup complete in: %s", startup_time)
            return

        (ridx, match, res) = self.tn.expect(
            [b"login: ", b"Login: ", b"cumulus login: "],
            1,
        )

        if match:
            self.logger.debug("Cumulus VX login prompt detected")

            try:
                self._first_boot_setup()
            except Exception as exc:
                self.logger.error(
                    "First-boot setup failed (%s) — password may need "
                    "manual change via serial console (port %d)",
                    exc,
                    5000 + self.num,
                )
                # Don't set _bootstrap_done; retry on next login prompt.
                self.spins += 1
                return

            self._bootstrap_done = True
            self.spins += 1
            return

        if res != b"":
            self.logger.trace("OUTPUT: %s" % res.decode())
            self.spins = 0

        self.spins += 1

    def _switchd_is_ready(self):
        """Check whether switchd is active via the serial console."""
        self.wait_write("\r", None)
        self.wait_write("systemctl is-active switchd 2>/dev/null", None)
        time.sleep(2)
        try:
            (_, match, _) = self.tn.expect([b"active", b"inactive", b"failed"], 3)
            if match:
                return match.group(0) == b"active"
        except Exception:
            pass
        return False

    def _first_boot_setup(self):
        """Handle Cumulus VX first-boot forced password change."""

        VM_USER = "cumulus"
        VM_PASS = "cumulus"
        NEW_PASS = "Clab123!"

        self.logger.info("First-boot setup for '%s' ...", VM_USER)

        # Step 1 — log in
        self.wait_write(VM_USER, None)
        self.wait_write(VM_PASS, "Password:")

        # Step 2 — forced password change (PAM)
        (ridx, match, _) = self.tn.expect(
            [b"Current password:", b"@", b"$ ", b"# "],
            8,
        )
        if match and ridx == 0:
            self.logger.info("First-boot: changing expired password")
            self.wait_write(VM_PASS, None)
            self.wait_write(NEW_PASS, "New password:")
            self.wait_write(NEW_PASS, "Retype new password:")
            time.sleep(1)
        elif match and ridx in (1, 2, 3):
            self.logger.debug("Already authenticated (overlay reuse)")

        # Step 3 — configure system
        self.logger.info("Configuring system ...")
        self.wait_write(
            "echo '%s' | sudo -S bash -c '"
            "chage -M -1 %s && "
            'echo "%s ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/%s && '
            "hostnamectl set-hostname %s'"
            % (NEW_PASS, VM_USER, VM_USER, VM_USER, self.hostname),
            None,
        )
        time.sleep(3)

        # Step 4 — verify shell is still responsive (password was
        # already changed by PAM in step 2)
        self.wait_write("\r", None)
        (_, m2, _) = self.tn.expect([b"$ ", b"# ", b"@"], 8)
        if m2:
            self.logger.info("Password verified: '%s' / '%s'", VM_USER, NEW_PASS)
        else:
            self.logger.warning("Shell prompt not detected after config")

        self.logger.info("First-boot setup complete")


# ── VR subclass ─────────────────────────────────────────────────────────────────


class CumulusVX(vrnetlab.VR):
    def __init__(self, hostname, username, password, conn_mode):
        super(CumulusVX, self).__init__(username, password)
        self.vms = [CumulusVX_vm(hostname, username, password, conn_mode)]


# ── entry point ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Nvidia Cumulus VX vrnetlab launcher")
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Enable trace level logging",
    )
    parser.add_argument(
        "--username",
        default="cumulus",
        help="Cumulus Linux admin username (default: cumulus)",
    )
    parser.add_argument(
        "--password",
        default="Clab123!",
        help="Cumulus Linux admin password",
    )
    parser.add_argument(
        "--hostname",
        default="cumulus",
        help="VM hostname (passed by containerlab generic_vm kind)",
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

    vr = CumulusVX(
        args.hostname,
        args.username,
        args.password,
        args.connection_mode,
    )
    vr.start()
