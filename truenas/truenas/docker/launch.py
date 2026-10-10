#!/usr/bin/env python3

import datetime
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time

import tnrpc
import vrnetlab

STARTUP_CONFIG_FILE = "/config/startup-config.cfg"

# truenas_admin's password inside the image (set by the build-time install); the launcher changes it on first boot
IMAGE_PASSWORD = "VR-netlab9"
BOOT_DISK = "/truenas.qcow2"
BOOT_SERIAL = "VRNBOOT01"
DATA_SERIAL = "VRNDATA{:02d}"


def handle_SIGCHLD(signal, frame):
    os.waitpid(-1, os.WNOHANG)


# the running VM, so a container stop can power the guest off cleanly (an abrupt QEMU kill risks the boot pool and the
# TrueNAS configuration database)
RUNNING_VM = None


def handle_SIGTERM(signal, frame):
    vm = RUNNING_VM
    if vm is not None and getattr(vm, "p", None) is not None and vm.p.poll() is None:
        timeout = int(os.getenv("TRUENAS_STOP_TIMEOUT", "90"))
        logging.getLogger().info(f"container stopping: ACPI power-off of the guest (up to {timeout} s)")
        try:
            # vrnetlab holds the (single-client) QEMU monitor connection
            vm._qemu_monitor_cmd("system_powerdown")
        except Exception as e:  # noqa: BLE001
            logging.getLogger().error(f"QEMU monitor: {e}")
        deadline = time.time() + timeout
        while time.time() < deadline and vm.p.poll() is None:
            time.sleep(1)
        logging.getLogger().info("guest powered off" if vm.p.poll() is not None else "guest still running, exiting")
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


class TrueNAS_vm(vrnetlab.VM):
    """TrueNAS (Community Edition) virtual machine.

    The image carries a boot disk installed at build time from the TrueNAS ISO
    (see `TrueNAS_installer`). At run time the VM gets:
      - the boot disk (virtio-blk, serial VRNBOOT01)
      - TRUENAS_DATA_DISKS empty data disks (virtio-blk, serials VRNDATA01..)
        of TRUENAS_DATA_DISK_SIZE each, for storage pools
      - the management NIC on pci.1 slot 1, data NICs eth1.. on slots 2..
        (virtio-net; Linux names them enp<bus>s<slot>, see the README)

    The launcher talks to the TrueNAS middleware API (JSON-RPC 2.0 over the
    websocket ws://<mgmt>/api/current) to bootstrap the system on first boot.
    """

    MGMT_PCI_ADDR = 1
    # a directory mounted here keeps the disks across container re-creations (containerlab redeploys)
    PERSIST_DIR = "/persist"

    def __init__(self, hostname, username, password, conn_mode, nics, install_mode=False):
        self.install_mode = install_mode
        disk_image = BOOT_DISK
        self.disk_dir = "/"
        if not install_mode and os.path.isdir(self.PERSIST_DIR):
            # vrnetlab puts the primary overlay next to the base image, so the base is linked into the persistent dir
            self.disk_dir = self.PERSIST_DIR
            disk_image = os.path.join(self.PERSIST_DIR, os.path.basename(BOOT_DISK))
            if not os.path.exists(disk_image):
                os.symlink(BOOT_DISK, disk_image)
        self.overlay = re.sub(r"(\.[^.]+$)", r"-overlay\1", disk_image)
        # written after the first-boot config ran; lives next to the disks, so a new boot disk means a new bootstrap
        self.marker = os.path.join(self.disk_dir, ".bootstrapped")
        if not os.path.exists(self.overlay) and os.path.exists(self.marker):
            os.remove(self.marker)

        super().__init__(
            username,
            password,
            disk_image=disk_image,
            ram=int(os.getenv("TRUENAS_RAM", "8192")),
            smp=os.getenv("TRUENAS_SMP", "4"),
            driveif="none",
        )
        self.hostname = hostname
        self.conn_mode = conn_mode
        self.num_nics = nics
        # Linux 802.3ad bonding refuses members without a link speed/duplex, which virtio-net only reports when told
        self.nic_type = f"virtio-net-pci,speed={int(os.getenv('TRUENAS_NIC_SPEED', '10000'))},duplex=full"

        # vrnetlab creates the boot disk as "-drive if=none,file=<overlay>": give it an id and a virtio-blk device
        # with a fixed serial, so TrueNAS identifies its disks by serial (it falls back to device names without one)
        for i, arg in enumerate(self.qemu_args):
            if arg.startswith("if=none,file="):
                self.qemu_args[i] = arg + ",id=boot,format=qcow2"
                break
        self.qemu_args.extend(["-device", f"virtio-blk-pci,drive=boot,serial={BOOT_SERIAL},bootindex=1"])
        # the pc machine adds an empty floppy drive, which TrueNAS lists as a 4 KiB disk
        self.qemu_args.extend(["-global", "isa-fdc.fdtypeA=none"])

        self.data_disks = 0 if install_mode else int(os.getenv("TRUENAS_DATA_DISKS", "4"))
        size = os.getenv("TRUENAS_DATA_DISK_SIZE", "50G")
        for n in range(1, self.data_disks + 1):
            path = os.path.join(self.disk_dir, f"data{n}.qcow2")
            if not os.path.exists(path):
                vrnetlab.run_command(["qemu-img", "create", "-f", "qcow2", path, size])
            self.qemu_args.extend(
                [
                    "-drive",
                    f"if=none,id=data{n},file={path},format=qcow2",
                    "-device",
                    f"virtio-blk-pci,drive=data{n},serial={DATA_SERIAL.format(n)}",
                ]
            )

        self.api = None
        self.api_password = None
        self._last_login = 0.0

    def gen_mgmt(self):
        """Pin the management NIC to slot 1 of the pci.1 bridge (data NICs take slots 2..), so its interface name
        is the same at install time and at run time (the install configures DHCP on it)."""
        res = super().gen_mgmt()
        res[1] = res[1] + f",bus=pci.1,addr=0x{self.MGMT_PCI_ADDR:x}"
        return res

    def _release_console(self):
        if getattr(self, "tn", None):
            self.tn.close()
            self.tn = None

    # ------------------------------------------------------------------ API

    def _api_host(self):
        """The middleware is reached through the host-forwarded port 80 (or the guest itself in passthrough mode)"""
        if self.mgmt_passthrough:
            return self.mgmt_address_ipv4.split("/")[0], 80
        return "127.0.0.1", 80

    def _api_connect(self, passwords):
        """Log in to the middleware. TrueNAS allows 10 sessions per user and rate-limits auth.login_ex to 20 calls per
        minute per client, so the launcher keeps ONE connection while it waits and logs in at most every 10 s."""
        if time.time() - self._last_login < 10:
            return None
        self._last_login = time.time()
        host, port = self._api_host()
        for pw in passwords:
            try:
                c = tnrpc.Client(host, port, "/api/current", timeout=10)
            except OSError as e:
                self.logger.trace(f"API not reachable yet: {e}")
                return None
            try:
                r = c.call("auth.login_ex", [{"mechanism": "PASSWORD_PLAIN", "username": "truenas_admin", "password": pw}], timeout=30)
            except (OSError, TimeoutError, tnrpc.RPCError) as e:
                self.logger.trace(f"API login failed: {e}")
                c.close()
                return None
            if r and r.get("response_type") == "SUCCESS":
                self.api_password = pw
                return c
            self.logger.debug(f"API login: {r.get('response_type') if r else r}")
            c.close()
        return None

    def _api_close(self):
        if self.api:
            try:
                self.api.call("auth.logout", timeout=10)
            except (OSError, TimeoutError, tnrpc.RPCError):
                pass
            self.api.close()
            self.api = None

    # ------------------------------------------------------------ bootstrap

    def bootstrap_spin(self):
        """Called periodically: wait for the middleware API, then bootstrap the system once"""
        if self.spins > 900:
            self.logger.error("TrueNAS API not ready after 900 spins, restarting VM")
            self._api_close()
            self.stop()
            self.start()
            return

        # the launcher works through the API only: vrnetlab's console read blocks while the console is idle, and
        # a key press on the TrueNAS console menu would act on it, so the serial connection is released for users
        self._release_console()

        if not self.api:
            # the image's install password only before the first-boot config changed it
            pws = [self.password] if os.path.exists(self.marker) else [self.password, IMAGE_PASSWORD]
            self.api = self._api_connect(pws)
        if not self.api:
            self.spins += 1
            time.sleep(1)
            return
        try:
            ready = self.api.call("system.ready")
        except (OSError, TimeoutError, tnrpc.RPCError) as e:
            self.logger.trace(f"system.ready: {e}")
            self._api_close()
            ready = False
        if not ready:
            self.spins += 1
            time.sleep(2)
            return

        self.logger.info("TrueNAS middleware ready")
        try:
            if not os.path.exists(self.marker):
                self.bootstrap_config()
                self.startup_config()
                with open(self.marker, "w") as f:
                    f.write(datetime.datetime.now().isoformat() + "\n")
            else:
                self.logger.info(f"system already bootstrapped ({self.marker}), skipping the first-boot config")
        except Exception as e:  # noqa: BLE001
            # never let a configuration error end the launcher: the container would exit and kill QEMU mid-write,
            # which can corrupt the TrueNAS configuration database
            self.logger.exception(f"first-boot configuration failed: {e}")
        finally:
            self._api_close()

        startup_time = datetime.datetime.now() - self.start_time
        self.logger.info("Startup complete in: %s", startup_time)
        self.running = True

    def _step(self, what, fn):
        """Run one first-boot step; a failure is logged and the next steps still run"""
        try:
            fn()
        except (tnrpc.RPCError, TimeoutError, OSError, KeyError, TypeError) as e:
            self.logger.error(f"{what} failed: {e}")

    def bootstrap_config(self):
        api = self.api

        def hostname():
            self.logger.info(f"setting hostname {self.hostname}")
            api.call("network.configuration.update", [{"hostname": self.hostname}])

        def admin():
            uid = api.call("user.query", [[["username", "=", "truenas_admin"]], {"get": True}])["id"]
            upd = {"ssh_password_enabled": True}
            if self.password != self.api_password:
                self.logger.info("setting the truenas_admin password")
                upd["password"] = self.password
            api.call("user.update", [uid, upd])

        def ssh():
            self.logger.info("enabling SSH (password login)")
            api.call("ssh.update", [{"passwordauth": True}])
            api.call("service.update", ["ssh", {"enable": True}])
            api.job("service.control", ["START", "ssh"])

        def arc():
            arc_max = os.getenv("TRUENAS_ARC_MAX_MB")
            if arc_max and not api.call("tunable.query", [[["var", "=", "zfs_arc_max"]]]):
                self.logger.info(f"capping the ZFS ARC at {arc_max} MiB")
                api.job("tunable.create", [{"type": "ZFS", "var": "zfs_arc_max", "value": str(int(arc_max) * 1024 * 1024)}])

        pool = os.getenv("TRUENAS_POOL", "tank") if self.data_disks else ""

        def create_pool():
            if api.call("pool.query", [[["name", "=", pool]]]):
                self.logger.info(f"pool {pool} exists")
                return
            disks = sorted(
                d["name"] for d in api.call("disk.details")["unused"] if (d.get("serial") or "").startswith("VRNDATA")
            )
            layout = os.getenv("TRUENAS_POOL_LAYOUT", "RAIDZ1" if len(disks) >= 3 else "STRIPE")
            self.logger.info(f"creating pool {pool} ({layout}) on {', '.join(disks)}")
            api.job("pool.create", [{"name": pool, "topology": {"data": [{"type": layout, "disks": disks}]}}])

        def user():
            # TrueNAS keeps user homes on a pool (SSH password login requires one): without a pool the extra
            # administrator gets web UI / API access only
            if api.call("user.query", [[["username", "=", self.username]]]):
                return
            self.logger.info(f"creating administrator {self.username}")
            gid = api.call("group.query", [[["name", "=", "builtin_administrators"]], {"get": True}])["id"]
            spec = {
                "username": self.username,
                "full_name": self.username,
                "password": self.password,
                "group_create": True,
                "groups": [gid],
                "shell": "/usr/bin/bash",
                "sudo_commands_nopasswd": ["ALL"],
            }
            if pool:
                if not api.call("pool.dataset.query", [[["id", "=", f"{pool}/home"]]]):
                    api.call("pool.dataset.create", [{"name": f"{pool}/home"}])
                spec.update({"home": f"/mnt/{pool}/home", "home_create": True, "ssh_password_enabled": True})
            api.call("user.create", [spec])

        self._step("hostname", hostname)
        self._step("truenas_admin", admin)
        self._step("ssh", ssh)
        self._step("ARC cap", arc)
        if pool:
            self._step("pool", create_pool)
        if self.username not in ("truenas_admin", "root"):
            self._step(f"user {self.username}", user)

    def startup_config(self):
        """Apply the startup config: a JSON list of middleware calls, each {"method": ..., "params": [...]}
        (add "job": true for methods that run as jobs, e.g. pool.create)."""
        if not os.path.exists(STARTUP_CONFIG_FILE):
            self.logger.trace(f"Startup config file {STARTUP_CONFIG_FILE} not found")
            return
        self.logger.info(f"applying startup config {STARTUP_CONFIG_FILE}")
        with open(STARTUP_CONFIG_FILE) as f:
            calls = json.load(f)
        for c in calls:
            fn = self.api.job if c.get("job") else self.api.call
            try:
                fn(c["method"], c.get("params", []))
                self.logger.info(f"startup config: {c['method']} ok")
            except (tnrpc.RPCError, TimeoutError, OSError) as e:
                self.logger.error(f"startup config: {e}")


class TrueNAS_installer(TrueNAS_vm):
    """Installs TrueNAS from the ISO into the image's boot disk at build time.

    The ISO is bind-mounted at /install.iso. Its kernel and initrd boot the live installer directly (no GRUB menu,
    console on ttyS0 so the installed system keeps the serial console), whose JSON-RPC service on port 8080 runs the
    install: boot disk, truenas_admin password, DHCP on the management NIC.
    """

    ISO = "/install.iso"
    ISO_MNT = "/mnt/iso"

    def __init__(self, hostname, username, password, conn_mode):
        # one data NIC slot makes vrnetlab add the pci.1 bridge the management NIC sits on (no container eth1 exists, so
        # no data NIC is created)
        super().__init__(hostname, username, password, conn_mode, 1, install_mode=True)
        os.makedirs(self.ISO_MNT, exist_ok=True)
        subprocess.run(["mount", "-o", "loop,ro", self.ISO, self.ISO_MNT], check=True)
        self.qemu_args.extend(
            [
                "-drive",
                f"file={self.ISO},media=cdrom,readonly=on,if=ide,index=2",
                "-kernel",
                f"{self.ISO_MNT}/vmlinuz",
                "-initrd",
                f"{self.ISO_MNT}/initrd.img",
                "-append",
                '"boot=live toram=filesystem.squashfs nomodeset gfxpayload=text console=tty0 console=ttyS0,115200n8"',
            ]
        )
        self.installed = False

    def bootstrap_spin(self):
        if self.spins > 600:
            self.logger.error("installer did not answer")
            sys.exit(1)
        self._release_console()
        try:
            rpc = tnrpc.Client("127.0.0.1", 8080, "/ws", timeout=10, notify=self._progress)
            info = rpc.call("system_info", timeout=30)
        except (OSError, TimeoutError, tnrpc.RPCError) as e:
            self.logger.trace(f"installer not ready: {e}")
            self.spins += 1
            time.sleep(1)
            return
        self.logger.info(f"installer ready: {info}")

        # the install VM has exactly one disk (the boot disk) and one NIC (management); the installer reports
        # neither serials nor MACs, so anything else is a build error
        disks = [d["name"] for d in rpc.call("list_disks")]
        nics = [n["name"] for n in rpc.call("list_network_interfaces")]
        self.logger.info(f"installer sees disks {disks}, network interfaces {nics}")
        if len(disks) != 1 or len(nics) != 1:
            self.logger.error("expected exactly one disk and one network interface")
            sys.exit(1)
        boot, mgmt = disks, nics
        self.logger.info(f"installing to {boot[0]}, DHCP on {mgmt[0]}")
        rpc.call(
            "install",
            [
                {
                    "disks": [boot[0]],
                    "set_pmbr": True,
                    "authentication": {"username": "truenas_admin", "password": IMAGE_PASSWORD},
                    "post_install": {"network_interfaces": [{"name": mgmt[0], "ipv4_dhcp": True}]},
                }
            ],
            timeout=1800,
        )
        self.logger.info("install finished, powering off")
        try:
            rpc.call("shutdown", timeout=10)
        except (OSError, TimeoutError, tnrpc.RPCError):
            pass
        self.installed = True
        self.running = True

    def _progress(self, method, params):
        if method == "installation_progress" and params:
            p = params[0]
            self.logger.info(f"install {int(p.get('progress', 0) * 100)}% {p.get('message')}")

    def install(self):
        self.start()
        while not self.installed:
            self.work()
        # wait for QEMU to exit after the guest powered off
        for _ in range(120):
            if self.p.poll() is not None:
                break
            time.sleep(1)
        else:
            self.logger.warning("QEMU still running 120 s after shutdown, stopping it")
            self.stop()
        subprocess.run(["umount", self.ISO_MNT], check=False)
        # fold the install into the base image and compact it
        self.logger.info("committing the installed overlay into the base image")
        vrnetlab.run_command(["qemu-img", "commit", self.overlay])
        os.remove(self.overlay)
        vrnetlab.run_command(["qemu-img", "convert", "-O", "qcow2", "-c", BOOT_DISK, BOOT_DISK + ".tmp"])
        os.replace(BOOT_DISK + ".tmp", BOOT_DISK)
        self.logger.info("install complete")


class TrueNAS(vrnetlab.VR):
    def __init__(self, hostname, username, password, conn_mode, nics):
        super().__init__(username, password)
        self.vms = [TrueNAS_vm(hostname, username, password, conn_mode, nics)]
        global RUNNING_VM
        RUNNING_VM = self.vms[0]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="")
    parser.add_argument("--trace", action="store_true", help="enable trace level logging")
    parser.add_argument("--hostname", default="truenas", help="Hostname")
    parser.add_argument("--username", default="admin", help="Administrator to create (besides truenas_admin)")
    parser.add_argument("--password", default="admin@123", help="Password of truenas_admin and the administrator")
    parser.add_argument("--connection-mode", default="tc", help="Connection mode to use in the datapath")
    parser.add_argument("--nics", type=int, default=8, help="Number of data NICs")
    parser.add_argument("--install", action="store_true", help="Install TrueNAS from /install.iso (image build)")
    args = parser.parse_args()

    LOG_FORMAT = "%(asctime)s: %(module)-10s %(levelname)-8s %(message)s"
    logging.basicConfig(format=LOG_FORMAT)
    logger = logging.getLogger()

    logger.setLevel(logging.DEBUG)
    if args.trace:
        logger.setLevel(1)

    if args.install:
        TrueNAS_installer(args.hostname, args.username, args.password, args.connection_mode).install()
    else:
        vr = TrueNAS(args.hostname, args.username, args.password, args.connection_mode, args.nics)
        vr.start()
