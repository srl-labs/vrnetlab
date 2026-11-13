"""Pure helpers for the VeloCloud vrnetlab launcher (no vrnetlab import)."""

import fnmatch
import glob
import ipaddress
import logging
import os
import re
import shutil
import socket
import subprocess
import tempfile
from typing import NamedTuple

import yaml

# Defaults from the reference libvirt domains. data_nics excludes the mgmt NIC.
# ready: how the launcher decides the VM is up (see READY_CHECKS).
COMPONENT_DEFAULTS = {
    "vce": {"ram": 4096, "smp": "3", "data_nics": 3, "ready": "ssh"},
    "vcc": {"ram": 8192, "smp": "3", "data_nics": 1, "ready": "ssh"},
    "vco": {"ram": 55296, "smp": "12", "data_nics": 1, "ready": "cloud-init"},
}

# ssh:        the VM's SSH server answers with its banner.
# cloud-init: the console shows cloud-init's final stage finished, and the
#             guest's cloud-init.log records it as a success (checked over SSH).
READY_CHECKS = ("ssh", "cloud-init")
READY_CHECK_ENV = "VC_READY_CHECK"  # overrides the per-component default

_FILENAME_PATTERNS = [
    ("edge-VC_KVM_GUEST-*.qcow2", "vce"),
    ("velocloud-vcc-*.qcow2", "vcc"),
    ("velocloud-orchestrator-kvm-*.tar", "vco"),
]

CLOUD_INIT_DIR = "/config/cloud-init"
CLOUD_INIT_FILES = ["user-data", "meta-data", "network-config"]

VCO_DISKS = ["rootfs.qcow2", "store.qcow2", "store2.qcow2", "store3.qcow2"]

# The VCE's first port (GE1) keeps its factory address.
VCE_MGMT = ipaddress.IPv4Interface("192.168.2.1/24")
# vrnetlab's default user-mode mgmt network, used when no static IP is given.
DEFAULT_MGMT = ipaddress.IPv4Interface("10.0.0.15/24")


class MgmtNet(NamedTuple):
    subnet: str
    host_index: int
    guest_index: int


def detect_component(image_name: str) -> str:
    base = os.path.basename(image_name)
    for pattern, component in _FILENAME_PATTERNS:
        if fnmatch.fnmatchcase(base, pattern):
            return component
    raise ValueError(f"unknown VeloCloud image: {image_name}")


def find_disks(component: str, root: str = "/") -> list[str]:
    if component == "vco":
        disks = [os.path.join(root, d) for d in VCO_DISKS]
        missing = [d for d in disks if not os.path.exists(d)]
        if missing:
            raise FileNotFoundError(f"VCO disk(s) missing: {missing}")
        return disks
    found = sorted(
        p for p in glob.glob(os.path.join(root, "*.qcow2")) if "-overlay" not in os.path.basename(p)
    )
    if len(found) != 1:
        raise FileNotFoundError(f"expected exactly one disk image in {root}, found {found}")
    return found


def read_mgmt_ipv4(network_config_path: str) -> ipaddress.IPv4Interface | None:
    """First static IPv4 address of eth0 in a netplan v2 network-config."""
    try:
        with open(network_config_path) as f:
            cfg = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return None
    if isinstance(cfg.get("network"), dict):  # cloud-init also accepts a `network:` wrapper
        cfg = cfg["network"]
    eth0 = (cfg.get("ethernets") or {}).get("eth0") or {}
    for addr in eth0.get("addresses") or []:
        try:
            iface = ipaddress.ip_interface(str(addr))
        except ValueError:
            continue
        if iface.version == 4:
            return iface
    return None


def pick_host_index(subnet: str, guest_index: int) -> int:
    """Host index for qemu user-net; host and dns (host+1) must not be the guest."""
    if guest_index not in (2, 3):
        return 2
    return ipaddress.ip_network(subnet).num_addresses - 3


def _mgmt_net(iface: ipaddress.IPv4Interface) -> MgmtNet:
    net = iface.network
    guest = int(iface.ip) - int(net.network_address)
    return MgmtNet(str(net), pick_host_index(str(net), guest), guest)


def mgmt_net_for(component: str, network_config_path: str) -> MgmtNet:
    if component == "vce":
        return _mgmt_net(VCE_MGMT)
    return _mgmt_net(read_mgmt_ipv4(network_config_path) or DEFAULT_MGMT)


def prepare_cloud_init_iso(
    src_dir: str = CLOUD_INIT_DIR, out_iso: str = "/cloud-init.iso", logger=None
) -> str | None:
    """Return the ISO to attach as CD-ROM, building it from the user's files if present.

    Precedence: user-data/meta-data/network-config (packed verbatim into a
    `cidata` NoCloud ISO) > a ready-made cdrom.iso > nothing.
    """
    logger = logger or logging.getLogger()
    present = [n for n in CLOUD_INIT_FILES if os.path.isfile(os.path.join(src_dir, n))]
    if "user-data" in present or "meta-data" in present:
        with tempfile.TemporaryDirectory() as stage:
            for name in present:
                shutil.copyfile(os.path.join(src_dir, name), os.path.join(stage, name))
            for name in ("user-data", "meta-data"):
                if name not in present:
                    logger.warning("cloud-init: %s missing in %s, using an empty one", name, src_dir)
                    open(os.path.join(stage, name), "w").close()
            names = sorted(os.listdir(stage))
            subprocess.run(
                ["genisoimage", "-quiet", "-output", out_iso, "-volid", "cidata", "-joliet", "-rock", *names],
                cwd=stage,
                check=True,
            )
        logger.info("cloud-init: built %s from %s (%s)", out_iso, src_dir, ", ".join(names))
        return out_iso
    cdrom = os.path.join(src_dir, "cdrom.iso")
    if os.path.isfile(cdrom):
        logger.info("cloud-init: using %s as-is", cdrom)
        return cdrom
    logger.warning("cloud-init: nothing found in %s, booting without a cloud-init ISO", src_dir)
    return None


def ssh_banner_ready(host: str = "127.0.0.1", port: int = 22, timeout: float = 3.0) -> bool:
    """True once an SSH server answers with its banner (qemu hostfwd accepts
    TCP even when the guest is not listening, so a bare connect is not enough)."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            data = b""
            while len(data) < 4:
                chunk = s.recv(4 - len(data))
                if not chunk:
                    break
                data += chunk
            return data == b"SSH-"
    except OSError:
        return False



def ready_check_for(component: str, env: dict | None = None) -> str:
    """The readiness check for a component; VC_READY_CHECK overrides it."""
    env = os.environ if env is None else env
    override = env.get(READY_CHECK_ENV, "").strip().lower()
    if override:
        if override not in READY_CHECKS:
            raise ValueError(
                f"{READY_CHECK_ENV}={override!r}: expected one of {', '.join(READY_CHECKS)}"
            )
        return override
    return COMPONENT_DEFAULTS[component]["ready"]


# systemd's status line when cloud-final.service finishes. systemd wraps the
# unit description in highlight escapes
# ("Finished \x1b[0;1;39mCloud-init: Final Stage\x1b[0m."), so match on
# ANSI-stripped text.
CLOUD_INIT_CONSOLE_MARKER = "Finished Cloud-init: Final Stage"
_ANSI_ESCAPE = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")


def strip_ansi(data: bytes) -> bytes:
    return _ANSI_ESCAPE.sub(b"", data)


class ConsoleWatcher:
    """Finds a marker in a console stream fed in arbitrary chunks."""

    TAIL = 256  # bytes kept between reads so a marker split across them matches

    def __init__(self, marker: str = CLOUD_INIT_CONSOLE_MARKER):
        self.marker = marker.encode()
        self.tail = b""
        self.seen = False

    def feed(self, data: bytes) -> bool:
        if self.seen or not data:
            return self.seen
        # Strip after joining so an escape split across reads is still removed.
        buf = strip_ansi(self.tail + data)
        if self.marker in buf:
            self.seen = True
        self.tail = buf[-self.TAIL:]
        return self.seen


CLOUD_INIT_LOG = "/var/log/cloud-init.log"
CLOUD_INIT_LOG_SUCCESS = "SUCCESS: running modules for final"
CLOUD_INIT_LOG_FAIL = "FAIL: running modules for final"
_CHECK_DONE = "VC_CHECK_DONE"


def cloud_init_check_command(user: str) -> list[str]:
    """argv run inside the container: log in to the VM and print the last
    'running modules for final' line of its cloud-init.log.

    The password comes from the SSHPASS environment variable (sshpass -e), so
    it never shows in the process list. vcadmin is in the adm group and reads
    the log directly; sudo, with the password on stdin, is only a fallback.
    The trailing marker proves the remote command ran: exit codes can't be
    trusted because launch.py's SIGCHLD handler reaps children before
    subprocess sees their status.
    """
    remote = (
        f"f={CLOUD_INIT_LOG}; "
        "{ cat \"$f\" 2>/dev/null || sudo -S -p '' cat \"$f\"; } "
        "| grep -F 'running modules for final' | tail -n 1; "
        f"echo {_CHECK_DONE}"
    )
    return [
        "sshpass", "-e",
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        "-o", "ConnectTimeout=10",
        "-o", "PubkeyAuthentication=no",
        "-o", "PreferredAuthentications=password,keyboard-interactive",
        "-p", "22",
        f"{user}@127.0.0.1",
        remote,
    ]


def parse_cloud_init_check(stdout: str, stderr: str = "") -> str:
    """Classify a cloud_init_check_command() run.

    success: the final stage succeeded     failed: it failed (won't change)
    pending: no final-stage result yet     denied: SSH login refused
    error:   anything else (SSH not up yet, timeout, ...)
    """
    if _CHECK_DONE not in stdout:
        return "denied" if "Permission denied" in stderr else "error"
    if CLOUD_INIT_LOG_SUCCESS in stdout:
        return "success"
    if CLOUD_INIT_LOG_FAIL in stdout:
        return "failed"
    return "pending"


_TCP_HOSTFWD = re.compile(r"hostfwd=tcp:0\.0\.0\.0:(\d+)-")


def localize_hostfwd(qemu_args: list[str]) -> tuple[list[str], list[int]]:
    """Bind qemu user-net TCP forwards to 127.0.0.1 and return their ports.

    libslirp rewrites the source address to its own host address only for
    connections from loopback; other clients keep their real source, which a
    guest without a route back (VCE GE1, a gateway-less VCO eth0) cannot
    answer. Clients reach these forwards through relay_commands() instead.
    """
    out, ports = [], []
    for arg in qemu_args:
        if arg.startswith("user,") and "hostfwd=tcp:0.0.0.0:" in arg:
            ports.extend(int(p) for p in _TCP_HOSTFWD.findall(arg))
            arg = _TCP_HOSTFWD.sub(r"hostfwd=tcp:127.0.0.1:\1-", arg)
        out.append(arg)
    return out, ports


def relay_commands(ports: list[int], addresses: list[str]) -> list[list[str]]:
    """socat relays from the container's own addresses to the loopback forwards."""
    cmds = []
    for port in ports:
        for addr in addresses:
            if ":" in addr:
                listen = f"TCP6-LISTEN:{port},bind=[{addr}],fork,reuseaddr"
            else:
                listen = f"TCP4-LISTEN:{port},bind={addr},fork,reuseaddr"
            cmds.append(["socat", listen, f"TCP4:127.0.0.1:{port}"])
    return cmds
