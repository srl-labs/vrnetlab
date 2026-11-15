#!/usr/bin/env python3

import datetime
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time

import vrnetlab
from vc_helpers import (
    CLOUD_INIT_CONSOLE_MARKER,
    CLOUD_INIT_DIR,
    CLOUD_INIT_LOG,
    COMPONENT_DEFAULTS,
    READY_CHECK_ENV,
    ConsoleWatcher,
    cloud_init_check_command,
    detect_component,
    find_disks,
    localize_hostfwd,
    mgmt_net_for,
    parse_cloud_init_check,
    prepare_cloud_init_iso,
    ready_check_for,
    relay_commands,
    ssh_banner_ready,
)


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

# check for SSH every N spins (one spin is ~1s)
SSH_CHECK_EVERY = 10
MAX_SPINS = 6000
# cloud-init readiness: log "still waiting" every N spins; SSH command timeout
WAIT_LOG_EVERY = 60
CLOUD_INIT_CHECK_TIMEOUT = 30
# Guest login for the cloud-init log check. Not --username/--password:
# containerlab always passes its own (clab / clab@123) there.
SSH_USER_ENV = "VC_SSH_USERNAME"
SSH_PASSWORD_ENV = "VC_SSH_PASSWORD"
DEFAULT_SSH_USER = "vcadmin"
DEFAULT_SSH_PASSWORD = "Velocloud123"


class VeloCloud_vm(vrnetlab.VM):
    def __init__(self, hostname, username, password, nics, conn_mode):
        self.component = detect_component(os.environ["VC_IMAGE"])
        defaults = COMPONENT_DEFAULTS[self.component]
        disks = find_disks(self.component)

        super().__init__(
            username,
            password,
            disk_image=disks[0],
            ram=defaults["ram"],
            smp=defaults["smp"],
            driveif="virtio",
        )
        self.hostname = hostname
        self.conn_mode = conn_mode
        self.ready_check = ready_check_for(self.component)
        self.ssh_user = os.environ.get(SSH_USER_ENV, DEFAULT_SSH_USER)
        self.ssh_password = os.environ.get(SSH_PASSWORD_ENV, DEFAULT_SSH_PASSWORD)
        self._reset_ready_checks()
        if self.ready_check == "cloud-init":
            self.logger.info(
                "%s readiness: console '%s', then %s over SSH as %s (%s=ssh to only wait for SSH)",
                self.component.upper(),
                CLOUD_INIT_CONSOLE_MARKER,
                CLOUD_INIT_LOG,
                self.ssh_user,
                READY_CHECK_ENV,
            )
        else:
            self.logger.info("%s readiness: SSH answers", self.component.upper())
        self.nic_type = "virtio-net-pci"
        self.num_nics = nics if nics else defaults["data_nics"]

        # Point vrnetlab's mgmt forwarding (ssh, https, ...) at the VM's real
        # first-interface address instead of the default 10.0.0.15.
        mgmt = mgmt_net_for(self.component, os.path.join(CLOUD_INIT_DIR, "network-config"))
        self.mgmt_subnet = mgmt.subnet
        self.mgmt_host_ip = mgmt.host_index
        self.mgmt_guest_ip = mgmt.guest_index
        self.logger.info(
            "%s mgmt: guest index %d in %s (qemu host index %d)",
            self.component.upper(),
            mgmt.guest_index,
            mgmt.subnet,
            mgmt.host_index,
        )
        if mgmt.guest_index < mgmt.host_index + 2:
            self.logger.info(
                "Guest IP sits below the qemu host IP; the vrnetlab 'Guest IP must be "
                "at least 2 higher' message that follows is expected and harmless"
            )

        # Extra disks (VCO store disks), each behind its own overlay.
        for disk in disks[1:]:
            overlay = disk.replace(".qcow2", "-overlay.qcow2")
            if not os.path.exists(overlay):
                subprocess.run(
                    ["qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b", disk, overlay],
                    check=True,
                )
            else:
                self.logger.info("Reusing overlay %s", overlay)
            self.qemu_args.extend(["-drive", f"if=virtio,file={overlay}"])

        iso = prepare_cloud_init_iso(logger=self.logger)
        if iso:
            self.qemu_args.extend(["-cdrom", iso])

    def nic_provision_delay(self):
        """Always give the VM all of its data NICs.

        Unlinked container interfaces become placeholder NICs in their own
        PCI slot (vrnetlab's dummy-NIC path can reuse a slot when the links
        have gaps, e.g. only eth2/eth3, and qemu then refuses to start).
        """
        super().nic_provision_delay()
        self.highest_provisioned_nic_num = self.start_nic_eth_idx + self.num_nics
        self.insuffucient_nics = False

    def gen_mgmt(self):
        """Host-forwarded mgmt, but reached through a loopback relay.

        See vc_helpers.localize_hostfwd: without it, SSH from outside the
        container arrives with its real source IP and the VM has no route back.
        """
        res = super().gen_mgmt()
        if self.mgmt_passthrough:
            return res
        res, ports = localize_hostfwd(res)
        # The relays point at fixed loopback ports, so they outlive qemu
        # restarts; starting them again would race the old listeners.
        if getattr(self, "_relays", None) is None:
            v4, v6 = self.get_mgmt_address()
            addrs = [a.split("/")[0] for a in (v4, v6) if a]
            self._relays = [subprocess.Popen(cmd) for cmd in relay_commands(ports, addrs)]
            self.logger.info("Relaying TCP %s from %s to the VM via loopback", ports, ", ".join(addrs))
        return res

    def _reset_ready_checks(self):
        self.console_watch = ConsoleWatcher()
        self.last_check_result = None

    def _mirror_console(self):
        """Copy serial console output to docker logs until the VM is up,
        watching it for cloud-init's final stage on the way.

        Console reads block until the VM prints something, and a booted
        VeloCloud VM can stay silent for minutes, so this runs in its own
        thread and bootstrap_spin never waits on the console.
        """
        while not self.running:
            try:
                data = self.tn.read_very_eager()  # _Console mirrors to stdout
            except Exception:
                return
            if not data:
                time.sleep(0.1)
                continue
            watch = self.console_watch
            if self.ready_check == "cloud-init" and not watch.seen and watch.feed(data):
                self.logger.info(
                    "[1/2] Console: '%s' seen after %s",
                    CLOUD_INIT_CONSOLE_MARKER,
                    datetime.datetime.now() - self.start_time,
                )

    def _release_console(self):
        """Free qemu's single-client serial console for users.

        close() alone doesn't wake the mirror thread blocked in recv(), so the
        TCP connection would stay up until the VM next prints something.
        """
        try:
            self.scrapli_tn.transport.socket.sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            self.tn.close()
        except Exception:
            pass

    def check_cloud_init_log(self):
        """Log in to the VM and classify its cloud-init.log (see
        vc_helpers.parse_cloud_init_check); logs each change of result."""
        try:
            res = subprocess.run(
                cloud_init_check_command(self.ssh_user),
                input=self.ssh_password + "\n",  # for the sudo fallback
                env={**os.environ, "SSHPASS": self.ssh_password},
                capture_output=True,
                text=True,
                timeout=CLOUD_INIT_CHECK_TIMEOUT,
            )
            result = parse_cloud_init_check(res.stdout, res.stderr)
            detail = (res.stderr or res.stdout).strip()
        except subprocess.TimeoutExpired:
            result, detail = "error", "timed out"

        changed = result != self.last_check_result
        self.last_check_result = result
        if result == "error":
            self.logger.debug("[2/2] SSH log check failed: %s", detail)
        elif result == "denied":
            if changed:
                self.logger.warning(
                    "[2/2] SSH login as %s refused: set %s/%s to the credentials from "
                    "your cloud-init, or %s=ssh to skip this check",
                    self.ssh_user,
                    SSH_USER_ENV,
                    SSH_PASSWORD_ENV,
                    READY_CHECK_ENV,
                )
        elif result == "failed":
            if changed:
                self.logger.error(
                    "[2/2] %s: cloud-init final stage FAILED; the node stays unhealthy",
                    CLOUD_INIT_LOG,
                )
        elif changed:
            self.logger.info(
                "[2/2] %s: %s",
                CLOUD_INIT_LOG,
                "final stage succeeded" if result == "success" else "no final-stage result yet",
            )
        return result

    def _is_ready(self):
        if self.ready_check == "ssh":
            return self.spins % SSH_CHECK_EVERY == 0 and ssh_banner_ready()
        if not self.console_watch.seen:
            if self.spins and self.spins % WAIT_LOG_EVERY == 0:
                self.logger.info(
                    "[1/2] Waiting for '%s' on the console (%s elapsed)",
                    CLOUD_INIT_CONSOLE_MARKER,
                    datetime.datetime.now() - self.start_time,
                )
            return False
        return (
            self.spins % SSH_CHECK_EVERY == 0
            and ssh_banner_ready()
            and self.check_cloud_init_log() == "success"
        )

    def bootstrap_spin(self):
        """Called periodically until the VM is up (see COMPONENT_DEFAULTS
        "ready": SSH answers, or cloud-init finished)."""
        if self.spins > MAX_SPINS:
            self.spins = 0
            # The VM booted and answers; a reboot fixes neither of these.
            if self.last_check_result in ("denied", "failed"):
                self.logger.warning(
                    "Still not ready (cloud-init check: %s); not restarting the VM",
                    self.last_check_result,
                )
            else:
                self.logger.debug("Too many spins without the VM getting ready -> restarting VM")
                self.stop()
                self._reset_ready_checks()
                self.start()
                return

        thread = getattr(self, "_console_thread", None)
        if thread is None or not thread.is_alive():
            self._console_thread = threading.Thread(target=self._mirror_console, daemon=True)
            self._console_thread.start()

        if self._is_ready():
            self.running = True
            self._release_console()
            startup_time = datetime.datetime.now() - self.start_time
            what = "SSH is up" if self.ready_check == "ssh" else "cloud-init finished"
            self.logger.info("%s. Startup complete in: %s", what, startup_time)
            return

        self.spins += 1
        time.sleep(1)


class VeloCloud(vrnetlab.VR):
    def __init__(self, hostname, username, password, nics, conn_mode):
        super().__init__(username, password)
        # /health survives `docker restart`; don't report a stale "running"
        # while the VM is being set up again.
        self.update_health(1, "starting")
        self.vms = [VeloCloud_vm(hostname, username, password, nics, conn_mode)]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="VeloCloud VCE/VCC/VCO")
    parser.add_argument("--trace", action="store_true", help="enable trace level logging")
    # Credentials are set by the user's cloud-init; these are only for vrnetlab.
    parser.add_argument("--username", default="vcadmin", help="Username")
    parser.add_argument("--password", default="Velocloud123", help="Password")
    parser.add_argument("--hostname", default=None, help="VM hostname (set by cloud-init)")
    parser.add_argument(
        "--nics", type=int, default=0, help="Number of data NICs (default: per component)"
    )
    parser.add_argument(
        "--connection-mode",
        default=os.environ.get("CONNECTION_MODE", "tc"),
        help="Connection mode to use in the datapath",
    )
    args = parser.parse_args()

    LOG_FORMAT = "%(asctime)s: %(module)-10s %(levelname)-8s %(message)s"
    logging.basicConfig(format=LOG_FORMAT)
    logger = logging.getLogger()

    logger.setLevel(logging.DEBUG)
    if args.trace:
        logger.setLevel(TRACE_LEVEL_NUM)

    vr = VeloCloud(args.hostname, args.username, args.password, args.nics, args.connection_mode)
    vr.start()
