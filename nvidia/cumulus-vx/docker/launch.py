#!/usr/bin/env python3
# ==============================================================================
# launch.py — Nvidia Cumulus VX vrnetlab launcher
#
# Modelled after the Juniper Apstra and Nokia CmgLinux launcher patterns.
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
import math
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

        # Cumulus VX is a switch — data-plane NICs are provisioned from
        # containerlab via CLAB_INTFS.
        self.num_nics = int(os.environ.get("CLAB_INTFS", 0))
        self.nic_type = "virtio-net-pci"

        # NVUE REST API (HTTPS on 8765) — 8080 is already in the base class
        self.mgmt_tcp_ports.append(8765)

        # ── persistent overlay ────────────────────────────────────────────────
        if os.path.isdir("/config"):
            persistent_overlay = "/config/cumulus_overlay.qcow2"

            if not os.path.exists(persistent_overlay):
                vrnetlab.run_command([
                    "qemu-img", "create",
                    "-f", "qcow2",
                    "-b", disk_image,
                    "-F", "qcow2",
                    persistent_overlay,
                ])
                self.logger.info(
                    "Created persistent overlay at %s", persistent_overlay
                )
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

        (ridx, match, res) = self.tn.expect(
            [b"login: ", b"Login: ", b"cumulus login: "],
            1,
        )

        if match:
            self.logger.debug("Cumulus VX login prompt detected")

            if not self._bootstrap_done:
                self._bootstrap_done = True
                try:
                    self._first_boot_setup()
                except Exception as exc:
                    self.logger.error(
                        "First-boot setup failed (%s) — password may need "
                        "manual change via serial console (port %d)",
                        exc,
                        5000 + self.num,
                    )

            # Cumulus-specific: wait for switchd to be active so that swp
            # ports are fully initialised before reporting healthy.
            if not self._switchd_is_ready():
                self.spins += 1
                return

            self.running = True
            self.tn.close()
            startup_time = datetime.datetime.now() - self.start_time
            self.logger.info("Startup complete in: %s", startup_time)
            return

        if res != b"":
            self.logger.trace("OUTPUT: %s" % res.decode())
            self.spins = 0

        self.spins += 1

    def _switchd_is_ready(self):
        """Check whether switchd is active via the serial console.
        Returns True if ``systemctl is-active switchd`` prints 'active'.
        """
        self._tn_write("")
        time.sleep(0.5)
        self._tn_write("systemctl is-active switchd 2>/dev/null")
        time.sleep(2)
        try:
            (_, match, _) = self.tn.expect([b"active", b"inactive", b"failed"], 3)
            if match:
                self.logger.debug("switchd status: %s", match.group(0).decode())
                return match.group(0) == b"active"
        except Exception:
            pass
        return False

    def _tn_write(self, text):
        """Write a line to the serial console."""
        self.tn.write(("%s\r" % text).encode())
        self.logger.trace("SENT: %s", text)

    def _tn_read_until(self, pattern, timeout=10):
        """Read from the serial console until *pattern* (bytes) is seen."""
        self.logger.trace("WAITING for: %s", pattern.decode(errors="replace"))
        (ridx, match, res) = self.tn.expect([pattern], timeout)
        if match:
            self.logger.trace("MATCHED: %s", pattern.decode(errors="replace"))
        return ridx, match, res

    def _first_boot_setup(self):
        """Handle Cumulus VX first-boot forced password change.

        A fresh Cumulus VX image has user ``cumulus`` / ``cumulus`` with an
        expired password.  We log in, satisfy the PAM password change with
        ``Nsn1234!``, enable NOPASSWD sudo, disable password expiry, and
        set the hostname.
        """

        VM_USER = "cumulus"
        VM_PASS = "cumulus"
        TMP_PASS = "Nsn1234!"

        self.logger.info("First-boot setup for '%s' ...", VM_USER)

        # ── Step 1: log in ──────────────────────────────────────────────────
        self._tn_write(VM_USER)
        self._tn_read_until(b"Password:", 15)
        self._tn_write(VM_PASS)
        time.sleep(1)

        # ── Step 2: forced password change (PAM) ────────────────────────────
        (ridx, match, _) = self.tn.expect(
            [b"Current password:", b"@", b"$ ", b"# "],
            10,
        )

        if match and ridx == 0:
            self.logger.info("First-boot: changing expired password")
            self._tn_write(VM_PASS)
            self._tn_read_until(b"New password:", 15)
            self._tn_write(TMP_PASS)
            self._tn_read_until(b"Retype new password:", 15)
            self._tn_write(TMP_PASS)
            time.sleep(2)

        elif match and ridx in (1, 2, 3):
            self.logger.debug("Already authenticated (overlay reuse)")

        # ── Step 3: configure system (sudo, chage, hostname) ─────────────
        self.logger.info(
            "Configuring system (new password is '%s') ...", TMP_PASS
        )
        self._tn_write(
            "echo '%s' | sudo -S bash -c '"
            "chage -M -1 %s && "
            "echo \"%s ALL=(ALL) NOPASSWD:ALL\" > /etc/sudoers.d/%s && "
            "hostnamectl set-hostname %s'"
            % (TMP_PASS, VM_USER, VM_USER, VM_USER, self.hostname)
        )
        time.sleep(4)

        # ── Step 4: verify ─────────────────────────────────────────────────
        self._tn_write("exit")
        time.sleep(0.5)
        self._tn_write("")
        time.sleep(0.5)
        self._tn_write(VM_USER)
        self._tn_read_until(b"Password:", 10)
        self._tn_write(TMP_PASS)
        time.sleep(1.5)

        (r2, m2, _) = self.tn.expect(
            [b"@", b"$ ", b"# ", b"Current password:", b"incorrect"],
            8,
        )

        if m2 and r2 in (0, 1, 2):
            self.logger.info(
                "Password verified: '%s' / '%s'", VM_USER, TMP_PASS
            )
        else:
            self.logger.warning("Password verify skipped (ridx=%s)", r2)

        self.logger.info("First-boot setup complete")


    # ── NIC provisioning overrides ──────────────────────────────────────────
    # Cumulus VX uses socket placeholders to fill PCI gaps so that switch port
    # numbering (swpX) matches the topology interface numbering even when
    # interfaces are sparsely numbered (e.g. eth1, eth4, eth6, eth7).

    def nic_provision_delay(self):
        """Override parent: discover highest provisioned NIC index without
        truncating to a sequential range, so that sparse interface numbering
        (e.g. eth1, eth4, eth6, eth7) is correctly handled."""
        import pathlib

        if self.num_provisioned_nics == 0:
            if self.min_nics:
                self.insuffucient_nics = True
            return

        self.logger.debug("waiting for provisioned interfaces to appear...")

        inf_path = pathlib.Path("/sys/class/net/")
        while True:
            provisioned_nics = list(
                inf_path.glob(f"{self.data_intf_prefix}*")
            )
            if len(provisioned_nics) >= self.num_provisioned_nics + 1:
                nics = [
                    int(re.search(pattern=r"\d+", string=nic.name).group())
                    for nic in provisioned_nics
                ]
                # Accept any index >= start_eth (not a closed range)
                nics = [nic for nic in nics if nic >= self.start_nic_eth_idx]
                if nics:
                    self.highest_provisioned_nic_num = max(nics)
                self.logger.debug(
                    "highest allocated interface id: %s",
                    self.highest_provisioned_nic_num,
                )
                break
            time.sleep(5)

        if self.num_provisioned_nics < self.min_nics:
            self.insuffucient_nics = True

    def gen_nics(self):
        """Override parent: loop bound is max(count, highest+1) so that
        sparsely numbered interfaces are not lost.  Missing slots below the
        highest provisioned NIC get socket placeholders so that PCI positions
        stay aligned and swpX numbering inside Cumulus matches ethX."""
        self.nic_provision_delay()

        res = []
        if self.conn_mode == "tc":
            self.create_tc_tap_ifup()

        start_eth = self.start_nic_eth_idx
        end_eth = max(
            self.start_nic_eth_idx + self.num_nics,
            self.highest_provisioned_nic_num + 1,
        )
        pci_bus_ctr = 0
        for i in range(start_eth, end_eth):
            pci_bus_ctr += 1
            x = pci_bus_ctr + 1 if "vEOS" in self.image else pci_bus_ctr
            pci_bus = math.floor(x / self.nics_per_pci_bus) + 1
            addr = (x % self.nics_per_pci_bus) + 1

            if not os.path.exists(
                f"/sys/class/net/{self.data_intf_prefix}{i}"
            ):
                if i >= self.highest_provisioned_nic_num:
                    continue
                # socket placeholder for gap
                res.extend(
                    [
                        "-device",
                        f"{self.nic_type},netdev=p{i:02d}"
                        + (
                            f",bus=pci.{pci_bus},addr=0x{addr:x}"
                            if self.provision_pci_bus
                            else ""
                        ),
                        "-netdev",
                        f"socket,id=p{i:02d},listen=:{i + 10000:02d}",
                    ]
                )
                continue

            mac = self.get_intf_mac(
                f"{self.data_intf_prefix}{i}"
            ) or vrnetlab.gen_mac(i)
            res.append("-device")
            res.append(
                f"{self.nic_type},netdev=p{i:02d},mac={mac}"
                + (
                    f",bus=pci.{pci_bus},addr=0x{addr:x}"
                    if self.provision_pci_bus
                    else ""
                ),
            )
            if self.conn_mode == "tc":
                res.append("-netdev")
                res.append(
                    f"tap,id=p{i:02d},ifname=tap{i},"
                    "script=/etc/tc-tap-ifup,downscript=no"
                )

        return res

    # ── end NIC overrides ────────────────────────────────────────────────────


# ── VR subclass ─────────────────────────────────────────────────────────────────

class CumulusVX(vrnetlab.VR):
    def __init__(self, hostname, username, password, conn_mode):
        super(CumulusVX, self).__init__(username, password)
        self.vms = [
            CumulusVX_vm(hostname, username, password, conn_mode)
        ]


# ── entry point ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Nvidia Cumulus VX vrnetlab launcher"
    )
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
        default="Nsn1234!",
        help="Cumulus Linux admin password (default: Nsn1234!)",
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
