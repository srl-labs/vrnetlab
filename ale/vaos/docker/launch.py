#!/usr/bin/env python3
#
# vrnetlab launcher for Alcatel-Lucent Enterprise OmniSwitch vAOS 8.x.
#
# Boots the VM the way the GNS3 appliance (vAOS8x.gns3a) does:
#   qemu -kernel vAOS8x_bzImage -initrd vAOS8x_ramdisk.gz \
#        -append "console=ttyS0 GNS3=1 inifile=vAOS8x.ini connfile=vAOS8x_fab.con loglevel=0" \
#        -hda vAOS8x.qcow2 -hdb cmm_chassis_info_vAOS8x.tar  (both IDE, e1000 NICs)

import ipaddress
import logging
import os
import re
import signal
import socket
import struct
import subprocess
import sys
import time

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
    if self.isEnabledFor(TRACE_LEVEL_NUM):
        self._log(TRACE_LEVEL_NUM, message, args, **kws)


logging.Logger.trace = trace

# Kernel command line copied verbatim from vAOS8x.gns3a.
# (The inifile/connfile configs already live inside the VM disks.)
KERNEL_APPEND = (
    "console=ttyS0 GNS3=1 inifile=vAOS8x.ini connfile=vAOS8x_fab.con loglevel=0"
)

# NIC index (adapter_number) -> OmniSwitch port name, from the vAOS8x.gns3a
# custom_adapters block. Index is the guest NIC enumeration order; index 2 is
# always the EMP (management) port.
#
# We place vrnetlab's management NIC on guest index 2 (= EMP), so EMP is the
# managed interface and every other slot is a usable data port. Result:
#   container ethN -> vAOS port at the matching guest index:
#     eth1 -> 1/1/1   eth2 -> 1/1/2   eth3 -> 1/1/3 ... eth10 -> 1/1/10
#     eth11 -> 1/1/29A   eth12 -> 1/1/30A      (EMP has no container interface)
PORT_MAP = {
    0: "1/1/1",
    1: "1/1/2",
    2: "EMP",      # <- vrnetlab management NIC is mapped here
    3: "1/1/3",
    4: "1/1/4",
    5: "1/1/5",
    6: "1/1/6",
    7: "1/1/7",
    8: "1/1/8",
    9: "1/1/9",
    10: "1/1/10",
    11: "1/1/29A",
    12: "1/1/30A",
}

# guest NIC index reserved for the management NIC (= EMP)
MGMT_NIC_INDEX = 2

# ---- Console bootstrap (EMP auto-configuration) -------------------------
# After boot, launch.py logs in on the serial console and configures EMP.
# All settings are optional environment variables (set them with "env:" in
# the containerlab topology):
#   VAOS_BOOTSTRAP   "false" to skip the bootstrap entirely   (default: true)
#   VAOS_USERNAME    console login     (default: --username, admin)
#   VAOS_PASSWORD    console password  (default: --password, Switch@123)
#   EMP_ROUTES       static routes, "prefix=gateway" separated by commas,
#                    e.g. "192.168.200.0/24=192.168.121.254"
#                    A default route (0.0.0.0/0) is not allowed on EMP and is
#                    skipped; list the specific management prefixes instead.
#   VAOS_STARTUP_CONFIG  path (inside the container) of a startup config to
#                    push after EMP is set. Default: the first non-empty file
#                    of STARTUP_CONFIG_PATHS (ale_vaos kind "startup-config:",
#                    or a generic_vm bind mount at /startup-config.cfg).
#                    Format: AOS CLI, one command per line, as in vcboot.cfg
#                    or "show configuration snapshot". Empty lines and lines
#                    starting with "!" are skipped. Keep the prompt ending
#                    in "->" if the config changes it.
#   VAOS_EXTRA_CMDS  extra CLI commands, separated by ";"
#   VAOS_SAVE        "false" to skip "write memory flash-synchro" (default: true)
# Note: containerlab's own USERNAME/PASSWORD env vars (clab/clab@123) are
# deliberately NOT used: the vAOS image does not know that account.
# Startup config locations: /config/startup-config.cfg is where the containerlab
# ale_vaos kind puts the node startup-config; /startup-config.cfg is for a bind
# mount with the generic_vm kind.
STARTUP_CONFIG_PATHS = ["/config/startup-config.cfg", "/startup-config.cfg"]
# "containerlab save" (ale_vaos kind) drops SAVE_REQUEST in the mounted config
# dir. launch.py then runs "write memory flash-synchro", stores
# "show configuration snapshot" in SAVED_CONFIG and writes "ok" (or the error)
# in SAVE_DONE, which containerlab waits for.
SAVE_REQUEST = "/config/.vaos-save-request"
SAVE_DONE = "/config/.vaos-save-done"
SAVED_CONFIG = "/config/startup-config.cfg"
# async console messages, e.g. "Sat Oct 31 14:02:19 : vcmNi thread INFO message:"
ASYNC_HEADER_RE = re.compile(r"^\w{3} \w{3} +\d+ \d\d:\d\d:\d\d : ")
LINE_PROMPT_RE = re.compile(r"^[\w.\-]*-> ?$")
LOGIN_RETRIES = 10
LOGIN_RETRY_DELAY = 15
# CLI prompt: a line that starts with "->" or "<name>->". Not anchored at the
# end of the buffer, because async console messages ("+++ Link 1/1/4
# operationally up") can be printed right after the prompt.
PROMPT_RE = rb"(?:^|\n)[\w.\-]*-> ?"
# vAOS shows the login prompt before it accepts configuration. Until it is
# ready, config commands answer with one of these errors: wait and retry.
NOT_READY_RE = (
    rb"has not reached the ready state|not permitted during initialization"
)
READY_RETRY_DELAY = 10
READY_TIMEOUT = 900  # seconds
# Boot is complete when this line shows on the console (same check as the
# GNS3 deploy script). Only then do we log in and configure.
LINK_UP_RE = rb"\+\+\+ Link 1/1/1 operationally up"
BOOT_TIMEOUT = 600  # seconds


class VAOS_vm(vrnetlab.VM):
    def __init__(self, hostname, username, password, nics, conn_mode):
        disk_image = None
        kernel = None
        initrd = None
        hdb = None
        for e in sorted(os.listdir("/")):
            if re.search(r"\.qcow2$", e) and "-overlay" not in e:
                disk_image = "/" + e
            elif re.search(r"bzImage$", e):
                kernel = "/" + e
            elif re.search(r"ramdisk.*\.gz$", e):
                initrd = "/" + e
            elif re.search(r"chassis_info.*\.tar$", e):
                hdb = "/" + e

        if not disk_image:
            raise RuntimeError("No .qcow2 disk image found in /")

        # ram=2048, smp=2 per the gns3a; driveif/ide + e1000 are vrnetlab defaults
        super(VAOS_vm, self).__init__(
            username,
            password,
            disk_image=disk_image,
            ram=2048,
            smp="2",
            driveif="ide",
        )

        self.hostname = hostname
        self.conn_mode = conn_mode
        self.port_map = PORT_MAP
        # expose all defined ports (max); vrnetlab only instantiates NICs for
        # interfaces actually wired in the topology.
        self.num_nics = max(nics, len(PORT_MAP) - 1)
        self.nic_type = "e1000"
        self._log_port_map()

        # Boot via the custom kernel + initrd + append, and attach the second
        # (chassis-info) disk as IDE -> becomes hdb.
        # vrnetlab runs qemu via "shell=True", so the multi-word -append value
        # must be quoted or the shell word-splits it (qemu then treats tokens
        # like "GNS3=1" as bare disk-image args).
        self.qemu_args.extend(
            ["-kernel", kernel, "-initrd", initrd, "-append", '"' + KERNEL_APPEND + '"']
        )
        if hdb:
            self.qemu_args.extend(["-drive", "if=ide,file=" + hdb + ",format=raw"])

        self.logger.info(
            "vAOS boot files: kernel=%s initrd=%s hda=%s hdb=%s",
            kernel, initrd, disk_image, hdb,
        )

    # ---- NIC placement --------------------------------------------------
    # vrnetlab enumerates NICs by PCI slot. To get a clean
    # container-ethN -> vAOS-1/1/N mapping with EMP as the management port, we
    # force every NIC onto bridge pci.1 with explicit, ordered slots and
    # reserve the slot at MGMT_NIC_INDEX for the management NIC (= EMP).

    @staticmethod
    def _eth_to_nic_index(eth_i):
        """container eth index -> guest NIC index (skipping the EMP/mgmt slot)."""
        return eth_i - 1 if eth_i - 1 < MGMT_NIC_INDEX else eth_i

    @staticmethod
    def _set_slot(dev_str, nic_index):
        """Force a -device string onto pci.1 at slot (nic_index + 1)."""
        dev_str = re.sub(r",bus=pci\.\d+", "", dev_str)
        dev_str = re.sub(r",addr=0x[0-9a-fA-F]+", "", dev_str)
        return dev_str + ",bus=pci.1,addr=0x%x" % (nic_index + 1)

    def gen_mgmt(self):
        # Put the management NIC on the EMP slot (guest index 2).
        res = super().gen_mgmt()
        for i, tok in enumerate(res):
            if tok == "-device":
                res[i + 1] = self._set_slot(res[i + 1], MGMT_NIC_INDEX)
        return res

    def gen_nics(self):
        # Re-slot each data NIC so container ethN lands on its target guest index.
        res = super().gen_nics()
        # vrnetlab skips NICs above the highest wired ethN, and the guest numbers
        # NICs in enumeration order. If eth1/eth2 are not wired, the management
        # NIC shifts down and is no longer EMP. Always add unconnected dummies.
        present = set(int(n) for n in re.findall(r"netdev=p(\d+)", " ".join(res)))
        for eth_i in range(1, MGMT_NIC_INDEX + 1):
            if eth_i not in present:
                res.extend([
                    "-device", "%s,netdev=p%02d" % (self.nic_type, eth_i),
                    "-netdev", "socket,id=p%02d,listen=:%d" % (eth_i, 10000 + eth_i),
                ])
        for i, tok in enumerate(res):
            if tok == "-device":
                m = re.search(r"netdev=p(\d+)", res[i + 1])
                if m:
                    eth_i = int(m.group(1))
                    res[i + 1] = self._set_slot(res[i + 1], self._eth_to_nic_index(eth_i))
        return res

    def _log_port_map(self):
        """Log the container-interface <-> vAOS-port mapping for the operator."""
        self.logger.info("vAOS NIC mapping (EMP = vrnetlab management):")
        for nic in sorted(self.port_map):
            port = self.port_map[nic]
            if nic == MGMT_NIC_INDEX:
                src = "vrnetlab management (NAT, or container eth0 in mgmt-passthrough)"
            else:
                eth = nic + 1 if nic < MGMT_NIC_INDEX else nic
                src = "container eth%d" % eth
            self.logger.info("  guest NIC%-2d  vAOS %-8s  <-  %s", nic, port, src)
        self.logger.info("  => eth1=1/1/1, eth2=1/1/2, eth3=1/1/3 ... EMP = management")

    def bootstrap_spin(self):
        """Poll the serial console until the OmniSwitch login prompt appears."""
        if self.spins > 6000:
            self.logger.warning("Too many spins, restarting VM")
            self.stop()
            self.start()
            return

        (ridx, match, res) = self.tn.expect([b"login:"], 1)
        # keep the boot output, in case the link-up line shows before login:
        self._boot_out = getattr(self, "_boot_out", b"") + res
        if match and ridx == 0:
            self.logger.info("vAOS reached login prompt")
            if os.environ.get("VAOS_BOOTSTRAP", "true").lower() != "false":
                try:
                    self.bootstrap_config()
                except Exception as e:
                    self.logger.error("vAOS bootstrap failed: %s", e)
            self.running = True
            self.tn.close()
            return

        if res != b"":
            self.logger.trace("OUTPUT: %s" % res.decode(errors="ignore"))
            self.spins = 0

        self.spins += 1
        return

    # ---- containerlab save ----------------------------------------------

    def work(self):
        super().work()
        if self.running and os.path.exists(SAVE_REQUEST):
            self.save_config()

    def save_config(self):
        """Save the configuration and store a snapshot for containerlab save."""
        self.logger.info("containerlab save requested")
        try:
            os.remove(SAVE_REQUEST)
        except OSError:
            pass
        result = "ok"
        try:
            self.tn.open()
            self.tn.write(b"\r")
            ridx, _, _ = self.tn.expect([PROMPT_RE, b"login:"], 15)
            if ridx == 1:
                username = os.environ.get("VAOS_USERNAME") or self.username
                password = os.environ.get("VAOS_PASSWORD") or self.password
                if not self._login(username, password):
                    raise RuntimeError("could not log in on the console")
            elif ridx != 0:
                raise RuntimeError("no prompt on the console (is a console session open?)")
            self._send("write memory flash-synchro", timeout=180)
            buf = self._send("show configuration snapshot", timeout=120)
            config = self._extract_snapshot(buf)
            if not config.strip():
                raise RuntimeError("empty configuration snapshot")
            with open(SAVED_CONFIG, "w") as f:
                f.write(config)
            self.logger.info("configuration saved to %s", SAVED_CONFIG)
            self.tn.write(b"exit\r")
        except Exception as e:
            result = "error: %s" % e
            self.logger.error("containerlab save failed: %s", e)
        finally:
            try:
                self.tn.close()
            except Exception:
                pass
            with open(SAVE_DONE, "w") as f:
                f.write(result + "\n")

    @staticmethod
    def _extract_snapshot(buf):
        """Keep the configuration lines of "show configuration snapshot".

        Async console messages ("+++ Link 1/1/4 operationally up") can be
        printed in the middle of a line: "spb+++ Link ...\n isis ..." is
        rejoined as "spb isis ...".
        """
        lines = buf.decode(errors="ignore").replace("\r", "").split("\n")
        for i, line in enumerate(lines):
            if "show configuration snapshot" in line:
                lines = lines[i + 1:]
                break
        out = []
        i = 0
        while i < len(lines):
            line = lines[i]
            i += 1
            if LINE_PROMPT_RE.match(line):
                break  # CLI prompt after the output
            if line.startswith("+++") or ASYNC_HEADER_RE.match(line):
                continue  # async console message on its own line
            pos = line.find("+++")
            if pos > 0:
                # message inserted mid-line: the rest of the line follows it
                while i < len(lines) and lines[i].startswith("+++"):
                    i += 1
                rest = lines[i] if i < len(lines) else ""
                i += 1
                line = line[:pos] + rest
            out.append(line)
        while out and not out[-1].strip():
            out.pop()
        while out and not out[0].strip():
            out.pop(0)
        return "\n".join(out) + "\n"

    # ---- console bootstrap ---------------------------------------------

    def _send(self, cmd, timeout=30):
        """Send one CLI command and wait for the "->" prompt. If vAOS says it
        is not ready yet, wait and send the command again."""
        deadline = time.time() + READY_TIMEOUT
        while True:
            self.logger.info("vAOS> %s", cmd)
            self.tn.write((cmd + "\r").encode())
            ridx, _, buf = self.tn.expect([PROMPT_RE], timeout)
            if ridx != 0:
                self.logger.warning("no prompt after '%s' (continuing)", cmd)
                return buf
            if not re.search(NOT_READY_RE, buf):
                return buf
            if time.time() > deadline:
                self.logger.error("vAOS still not ready after %ds, giving up on '%s'",
                                  READY_TIMEOUT, cmd)
                return buf
            self.logger.info("vAOS not ready yet, retrying in %ds", READY_RETRY_DELAY)
            time.sleep(READY_RETRY_DELAY)

    def _sync_emp_mac(self, emp):
        """mgmt passthrough: copy the EMP MAC address to the container eth0.

        vrnetlab gives the container eth0 the MAC of the VM management NIC, so
        that traffic for the ports kept in the container (5000-5007, serial
        console) is accepted by the container. vAOS does not use that MAC for
        EMP but its own, so the launcher learns it with an ARP probe sent to
        the VM on tap0 and sets it on eth0.
        """
        ip = socket.inet_aton(emp.split("/")[0])
        try:
            with open("/sys/class/net/%s/address" % self.mgmt_intf) as f:
                src = bytes.fromhex(f.read().strip().replace(":", ""))
            # ETH_P_ALL: frames from the VM are redirected to eth0 by tc on tap0
            # ingress; only an ETH_P_ALL socket sees them before that
            sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0003))
            sock.bind(("tap0", 0))
            sock.settimeout(1)
        except OSError as e:
            self.logger.warning("EMP MAC sync skipped: %s", e)
            return
        # ARP probe: who-has <EMP IP>, sender IP 0.0.0.0
        probe = (b"\xff" * 6 + src + b"\x08\x06"
                 + struct.pack("!HHBBH", 1, 0x0800, 6, 4, 1)
                 + src + b"\x00" * 4 + b"\x00" * 6 + ip)
        mac = None
        deadline = time.time() + 30
        try:
            while mac is None and time.time() < deadline:
                sock.send(probe)
                end = time.time() + 2
                while time.time() < end:
                    try:
                        pkt = sock.recv(2048)
                    except socket.timeout:
                        break
                    # ARP (reply, or gratuitous ARP) sent by the VM from the EMP IP
                    if len(pkt) >= 42 and pkt[12:14] == b"\x08\x06" \
                            and pkt[28:32] == ip and pkt[22:28] != src:
                        mac = pkt[22:28]
                        break
        finally:
            sock.close()
        if mac is None:
            self.logger.warning("EMP MAC sync: no ARP reply from EMP")
            return
        mac_str = ":".join("%02x" % b for b in mac)
        res = subprocess.run(["ip", "link", "set", "dev", self.mgmt_intf, "address", mac_str],
                             capture_output=True, text=True)
        if res.returncode != 0:
            self.logger.warning("EMP MAC sync: %s", res.stderr.strip())
            return
        self.logger.info("EMP MAC %s copied to container %s", mac_str, self.mgmt_intf)

    def _login(self, username, password):
        """Log in on the console. The first login prompt can come before AAA
        is ready, so retry a few times."""
        for attempt in range(1, LOGIN_RETRIES + 1):
            self.logger.info("vAOS console login attempt %d as %s", attempt, username)
            self.tn.write((username + "\r").encode())
            ridx, _, _ = self.tn.expect([b"assword:"], 30)
            if ridx != 0:
                self.tn.write(b"\r")
                self.tn.expect([b"login:"], 30)
                continue
            self.tn.write((password + "\r").encode())
            ridx, _, _ = self.tn.expect(
                [PROMPT_RE, rb"Authentication failure|Login incorrect"], 60
            )
            if ridx == 0:
                self.logger.info("vAOS console login OK")
                return True
            self.logger.warning("vAOS login failed, retrying in %ds", LOGIN_RETRY_DELAY)
            time.sleep(LOGIN_RETRY_DELAY)
            self.tn.write(b"\r")
            self.tn.expect([b"login:"], 30)
        return False

    def _push_config(self, path):
        """Send a startup config to the CLI, one line at a time."""
        with open(path, errors="ignore") as f:
            lines = [l.strip() for l in f]
        cmds = [l for l in lines if l and not l.startswith("!")]
        self.logger.info("pushing startup config %s (%d commands)", path, len(cmds))
        errors = 0
        for cmd in cmds:
            buf = self._send(cmd, timeout=60)
            if re.search(rb"\bERROR\b", buf):
                errors += 1
                self.logger.warning("startup config line rejected: %s", cmd)
        self.logger.info("startup config pushed: %d commands, %d rejected",
                         len(cmds), errors)

    def bootstrap_config(self):
        """Log in on the serial console and configure EMP (+ optional extras)."""
        username = os.environ.get("VAOS_USERNAME") or self.username
        password = os.environ.get("VAOS_PASSWORD") or self.password

        # EMP address: the node management address (mgmt passthrough), or
        # 10.0.0.15/24, where vrnetlab forwards ssh/snmp/... in NAT mode
        emp = self.mgmt_address_ipv4
        if not emp or emp == "dhcp":
            emp = None
        elif "/" not in emp:
            emp += "/24"

        if re.search(LINK_UP_RE, getattr(self, "_boot_out", b"")):
            self.logger.info("vAOS boot complete (Link 1/1/1 operationally up)")
        else:
            self.logger.info("waiting for '+++ Link 1/1/1 operationally up' (max %ds)",
                             BOOT_TIMEOUT)
            ridx, _, _ = self.tn.expect([LINK_UP_RE], BOOT_TIMEOUT)
            if ridx == 0:
                self.logger.info("vAOS boot complete (Link 1/1/1 operationally up)")
            else:
                self.logger.warning("Link 1/1/1 not up after %ds, trying anyway",
                                    BOOT_TIMEOUT)
        # get a fresh login prompt
        self.tn.write(b"\r")
        self.tn.expect([b"login:"], 30)

        if not self._login(username, password):
            raise RuntimeError("could not log in as %s" % username)

        hostname = os.environ.get("HOSTNAME") or self.hostname
        self._send("system name %s" % hostname)
        self._send('session prompt default "%s->"' % hostname)

        if emp:
            self._send("ip interface emp address %s" % emp)
            if self.mgmt_passthrough:
                self._sync_emp_mac(emp)
        else:
            self.logger.info("no EMP address to configure (DHCP mode)")

        for route in filter(None, os.environ.get("EMP_ROUTES", "").split(",")):
            prefix, _, gw = route.strip().partition("=")
            try:
                net = ipaddress.IPv4Network(prefix.strip(), strict=False)
                gw_ip = ipaddress.IPv4Address(gw.strip())
            except ValueError:
                self.logger.warning("ignoring malformed EMP_ROUTES entry '%s'", route)
                continue
            if net.prefixlen == 0:
                self.logger.warning(
                    "ignoring EMP_ROUTES entry '%s': a default route is not "
                    "allowed on EMP, use specific prefixes", route)
                continue
            self._send("ip static-route %s gateway %s" % (net, gw_ip))

        cfg_path = os.environ.get("VAOS_STARTUP_CONFIG")
        if cfg_path and not os.path.isfile(cfg_path):
            self.logger.warning("startup config %s not found in the container", cfg_path)
        for path in ([cfg_path] if cfg_path else STARTUP_CONFIG_PATHS):
            if path and os.path.isfile(path) and os.path.getsize(path) > 0:
                self._push_config(path)
                break

        for cmd in os.environ.get("VAOS_EXTRA_CMDS", "").split(";"):
            if cmd.strip():
                self._send(cmd.strip())

        if os.environ.get("VAOS_SAVE", "true").lower() != "false":
            self._send("write memory flash-synchro", timeout=180)

        self._send("show ip interface")
        self.logger.info("vAOS bootstrap done (EMP %s)", emp)


class VAOS(vrnetlab.VR):
    def __init__(self, hostname, username, password, nics, conn_mode):
        super(VAOS, self).__init__(username, password)
        self.vms = [VAOS_vm(hostname, username, password, nics, conn_mode)]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="vAOS vrnetlab launcher")
    parser.add_argument("--trace", action="store_true", help="enable trace logging")
    parser.add_argument("--hostname", default="vaos", help="VM hostname")
    parser.add_argument("--username", default="admin", help="Username")
    parser.add_argument("--password", default="Switch@123", help="Password")
    parser.add_argument("--nics", type=int, default=13, help="Number of NICs")
    parser.add_argument(
        "--connection-mode", default="tc", help="vrnetlab datapath connection mode"
    )
    args = parser.parse_args()

    LOG_FORMAT = "%(asctime)s: %(module)-10s %(levelname)-8s %(message)s"
    logging.basicConfig(format=LOG_FORMAT)
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    if args.trace:
        logger.setLevel(TRACE_LEVEL_NUM)

    vr = VAOS(args.hostname, args.username, args.password, args.nics, args.connection_mode)
    vr.start()
