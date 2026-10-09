#!/usr/bin/env python3

import datetime
import ipaddress
import logging
import os
import re
import signal
import sys
import time

import vrnetlab

STARTUP_CONFIG_FILE = "/config/startup-config.cfg"


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


class ONTAP_vm(vrnetlab.VM):
    """NetApp Simulate ONTAP (vsim) virtual machine.

    The simulator ships as an OVA with four IDE disks:
      disk1 - boot device / CF card (kernel, loader environment)
      disk2 - simulated NVRAM
      disk3 - /var
      disk4 - simulated disk shelves (sparse, ~230G virtual)

    and e1000 NICs that ONTAP names e0a, e0b, e0c, ... in PCI probe
    order. e0a/e0b are the cluster ports and e0c carries the node
    management LIF, so the vrnetlab management NIC is pinned to the
    third PCI slot to become e0c. Container interfaces map as
    eth1->e0a, eth2->e0b, eth3->e0d, eth4->e0e, ...
    """

    # PCI slot on the pci.1 bridge for the management NIC (-> e0c)
    MGMT_PCI_ADDR = 3

    # a directory mounted here keeps the disk overlays across container re-creations (containerlab redeploys): the
    # cluster, its configuration and its volumes survive like a physical system's disks would
    PERSIST_DIR = "/persist"

    def __init__(self, hostname, username, password, conn_mode, nics):
        disk_image = "/disk1.qcow2"
        if os.path.isdir(self.PERSIST_DIR):
            # vrnetlab puts the primary overlay next to the base image, so the base is linked into the persistent dir
            disk_image = os.path.join(self.PERSIST_DIR, "disk1.qcow2")
            if not os.path.exists(disk_image):
                os.symlink("/disk1.qcow2", disk_image)

        super().__init__(
            username,
            password,
            disk_image=disk_image,
            ram=6144,
            smp="2",
            driveif="ide",
        )

        self.hostname = hostname
        # the cluster name defaults to the container's hostname
        self.cluster_name = os.getenv("ONTAP_CLUSTER_NAME", hostname)
        self.conn_mode = conn_mode
        self.num_nics = nics
        self.nic_type = "e1000"
        self.wait_pattern = "::>"

        # second simulator node of a 2-node cluster needs its own system
        # id and serial number (the values NetApp ships licenses for)
        self.sysid = os.getenv("ONTAP_SYSID", "")
        self.serial = os.getenv("ONTAP_SERIAL", "")

        # ports that will become members of an interface group (LACP / multimode): ONTAP gives an
        # ifgroup the MAC address of e0a and expects the member NICs to accept frames for it through
        # their unicast filter, which the emulated e1000 does not do, so those NICs get e0a's MAC
        self.lag_ports = [
            p.strip() for p in os.getenv("ONTAP_LAG_PORTS", "").split(",") if p.strip()
        ]

        # multi-node clusters: ONTAP_CLUSTER_LIFS pins the addresses of the
        # cluster LIFs on e0a/e0b (comma separated, 169.254.0.0/16).
        # ONTAP_NODE_INDEX > 0 marks a member node: it only configures its
        # node management LIF and is added to the cluster by the first node
        # (index 0), which lists the members' cluster LIF addresses in
        # ONTAP_ADD_NODES (comma separated).
        self.cluster_lifs = [
            a.strip() for a in os.getenv("ONTAP_CLUSTER_LIFS", "").split(",") if a.strip()
        ]
        self.node_index = int(os.getenv("ONTAP_NODE_INDEX", "0"))
        self.add_nodes = [
            a.strip() for a in os.getenv("ONTAP_ADD_NODES", "").split(",") if a.strip()
        ]
        # every LIF address must be unique cluster-wide, so a member node's
        # host-forwarded management address moves up by its index
        # (10.0.0.15 cluster mgmt + 10.0.0.16 node mgmt on the first node,
        # 10.0.0.17, 10.0.0.18, ... node mgmt on the members)
        if self.node_index and not self.mgmt_passthrough:
            self.mgmt_guest_ip += 1 + self.node_index

        # attach the remaining three simulator disks. vrnetlab only creates
        # an overlay for the primary disk, so we create our own overlays to
        # keep the base images pristine (snapshots pick them up as secondary
        # disks via the -drive args).
        overlay_dir = self.PERSIST_DIR if os.path.isdir(self.PERSIST_DIR) else "/"
        for i in (2, 3, 4):
            base = f"/disk{i}.qcow2"
            overlay = os.path.join(overlay_dir, f"disk{i}-overlay.qcow2")
            if not os.path.exists(overlay):
                self.logger.debug(f"Creating overlay disk image: {overlay}")
                vrnetlab.run_command(
                    ["qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b", base, overlay]
                )
            self.qemu_args.extend(["-drive", f"if=ide,file={overlay}"])

    def gen_mgmt(self):
        """Place the management NIC on the pci.1 bridge at a fixed slot so
        that FreeBSD/ONTAP enumerates it as the third port (e0c)."""
        res = super().gen_mgmt()
        # the "-device" value is the second element returned by the parent
        res[1] = res[1] + f",bus=pci.1,addr=0x{self.MGMT_PCI_ADDR:x}"
        return res

    def gen_nics(self):
        """Renumber data NIC PCI slots so that eth1/eth2 come before the
        management NIC (e0a, e0b) and eth3+ come after it (e0d, ...), and
        fill slots 1 and 2 with dummy NICs when the topology leaves them
        empty, so the management NIC is always enumerated as e0c."""
        res = super().gen_nics()
        used = set()
        for idx, arg in enumerate(res):
            m = re.search(r"bus=pci\.1,addr=0x([0-9a-f]+)", arg)
            if not m:
                continue
            # the parent assigns eth<i> to addr i+1
            eth = int(m.group(1), 16) - 1
            addr = eth if eth < self.MGMT_PCI_ADDR else eth + 1
            used.add(addr)
            res[idx] = re.sub(
                r"bus=pci\.1,addr=0x[0-9a-f]+", f"bus=pci.1,addr=0x{addr:x}", arg
            )
        for slot in range(1, self.MGMT_PCI_ADDR):
            if slot in used:
                continue
            name = f"dummy{slot}"
            self.logger.debug(f"no container interface for PCI slot {slot}, adding {name}")
            res.extend(
                [
                    "-device",
                    f"{self.nic_type},netdev={name},id={name},mac={vrnetlab.gen_mac(slot)},"
                    f"bus=pci.1,addr=0x{slot:x}",
                    "-netdev",
                    f"tap,ifname={name},id={name},script=no,downscript=no",
                ]
            )
        if self.lag_ports:
            self._apply_lag_macs(res)
        return res

    @staticmethod
    def _port_name(slot):
        """ONTAP port name of the NIC in PCI slot `slot` of pci.1 (e0a, e0b, e0c = mgmt, e0d, ...)"""
        return "e0" + chr(ord("a") + slot - 1)

    def _apply_lag_macs(self, res):
        """Give the NICs of the ifgroup member ports (ONTAP_LAG_PORTS) the MAC address of e0a"""
        macs = {}
        for arg in res:
            m = re.search(r"mac=([0-9a-fA-F:]{17}).*bus=pci\.1,addr=0x([0-9a-f]+)", arg)
            if m:
                macs[int(m.group(2), 16)] = m.group(1)
        e0a_mac = macs.get(1)
        if not e0a_mac:
            self.logger.warning("ONTAP_LAG_PORTS set but no NIC in slot 1 (e0a); not changing MACs")
            return
        for idx, arg in enumerate(res):
            m = re.search(r"bus=pci\.1,addr=0x([0-9a-f]+)", arg)
            if not m or "mac=" not in arg:
                continue
            port = self._port_name(int(m.group(1), 16))
            if port in self.lag_ports:
                self.logger.info(f"{port}: ifgroup member, using e0a's MAC {e0a_mac}")
                res[idx] = re.sub(r"mac=[0-9a-fA-F:]{17}", f"mac={e0a_mac}", arg)

    def bootstrap_spin(self):
        """This function should be called periodically to do work."""

        if self.spins > 600:
            # too many spins with no result ->  give up
            self.logger.error("Too many spins without output, restarting VM")
            self.stop()
            self.start()
            return

        (ridx, match, res) = self.tn.expect(
            [
                rb"Hit \[Enter\] to boot immediately",
                rb"VLOADER>",
                rb"login: ",
            ],
            1,
        )
        if match:  # got a match!
            if ridx == 0:  # loader countdown -> any key for the loader prompt
                self.logger.info("Interrupting the boot loader")
                self.wait_write(" ", wait=None)
            elif ridx == 1:  # loader prompt
                self.loader_config()
            elif ridx == 2:  # login
                self.logger.info("matched login prompt")
                self.bootstrap_config()
                self.startup_config()
                # close telnet connection
                self.tn.close()
                # startup time?
                startup_time = datetime.datetime.now() - self.start_time
                self.logger.info("Startup complete in: %s", startup_time)
                # mark as running
                self.running = True
                return

        # no match, if we saw some output from the router it's probably
        # booting, so let's give it some more time
        if res != b"":
            self.logger.trace("OUTPUT: %s" % res.decode())
            # reset spins if we saw some output
            self.spins = 0

        self.spins += 1

        return

    def loader_config(self):
        """Configure the simulator boot loader (VLOADER) and boot ONTAP.

        The simulator defaults to the video console; we make the serial
        port the primary console so the boot and the cluster shell are
        reachable on the vrnetlab serial socket. VLOADER persists setenv
        to the boot device, so this survives reboots from within ONTAP.
        """
        self.logger.info("Configuring the boot loader")
        self.wait_write("setenv console comconsole,vidconsole", wait=None)
        if self.sysid:
            self.wait_write(f"setenv bootarg.nvram.sysid {self.sysid}", wait="VLOADER>")
        if self.serial:
            self.wait_write(f"setenv SYS_SERIAL_NUM {self.serial}", wait="VLOADER>")
        self.wait_write("boot_ontap", wait="VLOADER>")

    def _cmd(self, cmd, timeout=300):
        """Send a cluster shell command and return its output. Assumes the
        previous prompt was consumed (every _cmd leaves it consumed)."""
        self.wait_write(cmd, wait=None)
        # the prompt is "::>" at admin and "::*>" at advanced/diag privilege
        (ridx, match, res) = self.tn.expect([rb"::\*?>"], timeout)
        return res.decode(errors="ignore")

    def _switchless_cluster(self):
        """Back-to-back cluster links (eth1-eth1, eth2-eth2) need ONTAP's
        switchless-cluster option, or the add-node ping test between the
        cross cluster LIFs fails."""
        self._cmd("set -privilege advanced -confirmations off")
        # automatic detection blocks the manual setting (and does not detect
        # the simulator's links), so turn it off first
        self._cmd("network options detect-switchless modify -enabled false")
        self._cmd("network options switchless-cluster modify -enabled true")
        self._cmd("set -privilege admin -confirmations off")

    def _answer_wizard(self, answers, done, timeout=1200):
        """Drive an interactive ONTAP wizard: `answers` maps a prompt regex
        to the reply to send; stop when `done` matches."""
        t_end = datetime.datetime.now() + datetime.timedelta(seconds=timeout)
        patterns = [done.encode()] + [
            re.sub(r" ", r"\\s+", p).encode() for p in answers
        ]
        replies = [None] + list(answers.values())
        idle = b""
        while datetime.datetime.now() < t_end:
            (ridx, match, res) = self.tn.expect(patterns, 5)
            if ridx < 0:
                idle += res
                if res == b"" and idle.strip():
                    # output stopped without a known prompt: show what the
                    # wizard is waiting for and nudge it with an empty line
                    self.logger.warning(
                        "no known wizard prompt in: %r", idle[-300:].decode(errors="ignore")
                    )
                    idle = b""
                continue
            idle = b""
            if ridx == 0:
                return True
            self.logger.debug(f"wizard prompt '{match.group(0)}' -> '{replies[ridx]}'")
            self.wait_write(replies[ridx], wait=None)
        self.logger.error("timed out waiting for the wizard to finish")
        return False

    def bootstrap_config(self):
        """Log in and, on first boot, run the cluster setup wizard to create
        a single-node cluster with the management LIFs on e0c."""
        self.logger.info("applying bootstrap configuration")

        # an uninitialised node logs admin in without a password and shows a
        # bare "::>" prompt; a configured one asks for a password and shows
        # "<cluster>::>"
        self.wait_write("admin", wait=None)
        (ridx, match, res) = self.tn.expect([rb"Password:", rb"::>"], 60)
        if ridx == 0:
            self.wait_write(self.password, wait=None)
            (ridx, match, res) = self.tn.expect([rb"::>"], 60)

        if re.search(rb"\S::>", res):
            self.logger.info("cluster already configured, skipping cluster setup")
            self._cmd("set -rows 0 -confirmations off")
            return

        # the cluster management LIF gets the (reachable) management address
        # of the first node, every node management LIF the next one
        v4_addr, v4_prefix = self.mgmt_address_ipv4.split("/")
        v4_mask = vrnetlab.cidr_to_ddn(self.mgmt_address_ipv4)[1]
        v4_gw = self.mgmt_gw_ipv4
        if self.mgmt_passthrough:
            if self.node_index:
                node_addr, node_mask, node_gw = v4_addr, v4_mask, v4_gw
            else:
                # no second address available in pass-through mode; park the
                # node management LIF on a link-local address
                node_addr, node_mask, node_gw = "169.254.1.1", "255.255.0.0", ""
        else:
            network = ipaddress.ip_network(self.mgmt_subnet)
            node_addr = str(network[self.mgmt_guest_ip + (0 if self.node_index else 1)])
            node_mask, node_gw = v4_mask, v4_gw

        single_node = not (self.node_index or self.add_nodes or self.cluster_lifs)
        answers = {
            r"Type yes to confirm and continue": "yes",
            r"node management interface port": "e0c",
            r"node management interface IP address": node_addr,
            r"node management interface netmask": node_mask,
            r"node management interface default gateway": node_gw,
            r"press Enter to complete cluster setup using the command line": "",
            # a member node leaves the wizard here; the first node adds it
            r"\{create, join\}": "exit" if self.node_index else "create",
            r"single node cluster\? \{yes, no\}": "yes" if single_node else "no",
            r"Do you want to use this configuration\? \{yes, no\}": "yes",
            r"\(username \"admin\"\) password:": self.password,
            r"Retype the password:": self.password,
            r"Enter the cluster name:": self.cluster_name,
            r"additional license key": "",
            r"cluster management interface port": "e0c",
            r"cluster management interface IP address": v4_addr,
            r"cluster management interface netmask": v4_mask,
            r"cluster management interface default gateway": v4_gw,
            r"Enter the DNS domain names": "",
            r"Where is the controller located": "",
            r"system config backup destination address": "",
        }

        # pin the cluster LIF addresses before the wizard (it then offers
        # them as the existing configuration). Two passes in opposite
        # orders, so a pinned address that equals another LIF's current
        # (auto-generated) address does not block the change.
        if self.cluster_lifs:
            lifs = list(zip(("clus1", "clus2"), self.cluster_lifs))
            for lif, addr in lifs[::-1] + lifs:
                self._cmd(
                    f"network interface modify -vserver Cluster -lif {lif} "
                    f"-address {addr} -netmask 255.255.0.0"
                )
            self._cmd("network interface show -vserver Cluster")

        self.wait_write("cluster setup", wait=None)
        ok = self._answer_wizard(answers, done=r"::>" if self.node_index else r"\S::>")
        if not ok:
            return
        self._cmd("set -rows 0 -confirmations off")

        if self.node_index:
            self._switchless_cluster()
            self.logger.info(
                "member node ready; waiting to be added to the cluster by node 0"
            )
            return

        # add the member nodes once they show up on the cluster network
        if self.add_nodes:
            self._switchless_cluster()
        for ip in self.add_nodes:
            self.logger.info(f"waiting for node {ip} on the cluster network")
            t_end = datetime.datetime.now() + datetime.timedelta(seconds=1500)
            while datetime.datetime.now() < t_end:
                if ip in self._cmd("system node show-discovered"):
                    break
                time.sleep(20)
            else:
                self.logger.error(f"node {ip} never appeared, not adding it")
                continue
            self.logger.info(f"adding node {ip} to the cluster")
            self._cmd(f"cluster add-node -cluster-ips {ip}")
            t_end = datetime.datetime.now() + datetime.timedelta(seconds=1500)
            while datetime.datetime.now() < t_end:
                status = self._cmd("cluster add-node-status")
                row = re.search(rf"{re.escape(ip)}\s+(\w+)", status)
                if row and row.group(1) == "success":
                    self.logger.info(f"node {ip} added")
                    break
                if row and row.group(1) == "failure":
                    self.logger.error(f"adding node {ip} failed: {status}")
                    break
                time.sleep(20)
            else:
                self.logger.error(f"adding node {ip} timed out")
        if self.add_nodes:
            self._cmd("cluster show")

        # make every simulated disk available for aggregates
        nodes = re.findall(r"^(\S+-\d\d)\s*$", self._cmd("system node show -fields node"), re.M)
        for node in nodes or [f"{self.cluster_name}-01"]:
            self._cmd(f"storage disk assign -all true -node {node}")

        if self.username != "admin":
            if self.username in self.password:
                self.logger.error(
                    f"not creating user {self.username}: ONTAP rejects passwords "
                    "that contain the username; log in as admin instead"
                )
            else:
                for app in ("ssh", "http", "ontapi", "console"):
                    self.wait_write(
                        f"security login create -user-or-group-name {self.username} "
                        f"-application {app} -authentication-method password -role admin",
                        wait=None,
                    )
                    (ridx, match, res) = self.tn.expect(
                        [rb"Please enter a password for user", rb"::>"], 30
                    )
                    if ridx == 0:
                        self.wait_write(self.password, wait=None)
                        self.wait_write(self.password, wait="Please enter it again:")
                        self.tn.read_until(b"::>", 30)

    def startup_config(self):
        """Push the lines of the startup config (if any) to the cluster shell"""
        if not os.path.exists(STARTUP_CONFIG_FILE):
            self.logger.trace(f"Startup config file {STARTUP_CONFIG_FILE} not found")
            return

        self.logger.info(f"Startup config file {STARTUP_CONFIG_FILE} exists")
        with open(STARTUP_CONFIG_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                self._cmd(line)


class ONTAP(vrnetlab.VR):
    def __init__(self, hostname, username, password, conn_mode, nics):
        super().__init__(username, password)
        self.vms = [ONTAP_vm(hostname, username, password, conn_mode, nics)]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="")
    parser.add_argument(
        "--trace", action="store_true", help="enable trace level logging"
    )
    parser.add_argument("--hostname", default="ontap", help="Cluster name")
    parser.add_argument("--username", default="admin", help="Username")
    parser.add_argument(
        "--password",
        default="Netapp123",
        help="Password (ONTAP requires 8+ chars with letters and digits, "
        "and it must not contain the username)",
    )
    parser.add_argument(
        "--connection-mode",
        default="tc",
        help="Connection mode to use in the datapath",
    )
    parser.add_argument("--nics", type=int, default=8, help="Number of data NICs")
    args = parser.parse_args()

    LOG_FORMAT = "%(asctime)s: %(module)-10s %(levelname)-8s %(message)s"
    logging.basicConfig(format=LOG_FORMAT)
    logger = logging.getLogger()

    logger.setLevel(logging.DEBUG)
    if args.trace:
        logger.setLevel(1)

    vr = ONTAP(
        args.hostname,
        args.username,
        args.password,
        args.connection_mode,
        args.nics,
    )
    vr.start()
