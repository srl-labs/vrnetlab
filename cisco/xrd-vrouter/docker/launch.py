#!/usr/bin/env python3
"""vrnetlab launcher for Cisco XRd vRouter.

XRd vRouter is distributed as a *container* whose dataplane needs PCI NICs, so it
cannot run directly as a vrnetlab VM. This launcher boots a thin micro-VM (the
"golden" qcow2) that provides q35 + Intel vIOMMU and emulated data NICs (vmxnet3
by default, or igb); inside, guest-init runs the XRd vRouter container and binds
those NICs to vfio-pci for its DPDK dataplane.

By subclassing vrnetlab.VM we inherit the full containerlab-native machinery:
tc-mirred datapath stitching, management port-forwarding (SSH/NETCONF/gNMI/SNMP),
credentials, interface aliasing and health. We only override the bits that differ
for the nested model:
  * gen_nics(): emulated data NICs (vmxnet3 or igb) on per-NIC PCIe root ports
    (isolated IOMMU groups), reusing the framework's tc taps.
  * machine: q35 + intel-iommu (+ virtio-balloon for RAM reclaim).
  * a config CD delivering XR first-boot config + node params to guest-init.
  * bootstrap: wait for guest-init's readiness marker on the VM console.
"""

import os
import re
import signal
import subprocess
import sys
import tempfile

import vrnetlab

STARTUP_CONFIG_FILE = "/config/startup-config.cfg"
READY_MARKER = "CLAB_XRD_READY"


def handle_SIGCHLD(_signal, _frame):
    os.waitpid(-1, os.WNOHANG)


def handle_SIGTERM(_signal, _frame):
    sys.exit(0)


signal.signal(signal.SIGINT, handle_SIGTERM)
signal.signal(signal.SIGTERM, handle_SIGTERM)
signal.signal(signal.SIGCHLD, handle_SIGCHLD)


class XRd_vRouter_vm(vrnetlab.VM):
    def __init__(self, hostname, username, password, nics, conn_mode, vcpu, ram):
        # Locate the golden micro-VM disk baked into the image.
        disk_image = None
        for e in sorted(os.listdir("/")):
            if disk_image is None and re.search(r"\.qcow2$", e):
                disk_image = "/" + e
        if disk_image is None:
            raise FileNotFoundError("no golden .qcow2 found in /")

        # vRouter needs >=4 cores (dataplane + control plane); floor at 4.
        vcpu = max(int(vcpu), 4)
        # Needs ~5GiB RAM + 3GiB hugepages inside the VM; default 10GiB, hard floor 8GiB.
        ram = max(int(ram), 8192)

        super().__init__(
            username,
            password,
            disk_image=disk_image,
            ram=ram,
            smp=f"cores={vcpu},threads=1,sockets=1",
            cpu="host,+ssse3,+sse4.1,+sse4.2",
            driveif="virtio",
        )

        self.hostname = hostname
        self.conn_mode = conn_mode
        self.num_nics = nics
        self.nic_type = "virtio-net-pci"   # management NIC type
        self.provision_pci_bus = False     # we place data NICs on our own root ports

        # Dataplane NIC model. vmxnet3 (default) is paravirtual, ~1.5× faster
        # than igb for bulk forwarding, and XRd models it as TenGigE (10G). igb
        # (GigabitEthernet, 1G) stays available via XRD_NIC_TYPE. Both device IDs
        # are on XRd vRouter's supported-PCI allowlist.
        self.data_nic = os.getenv("XRD_NIC_TYPE", "vmxnet3").strip().lower()
        if self.data_nic not in ("vmxnet3", "igb"):
            raise ValueError(
                f"XRD_NIC_TYPE must be 'vmxnet3' or 'igb', got {self.data_nic!r}"
            )

        # Passthrough management: the framework tc-mirreds the container's eth0
        # (the clab node IP) to the VM mgmt NIC, so XR's MgmtEth carries the real
        # clab management IP over a transparent L2 bridge. Passthrough rather than
        # slirp host-forwarding because XR owns the management address with its own
        # MAC, which a NAT/DHCP NIC cannot reach.
        self.mgmt_passthrough = True
        self.mgmt_address_ipv4, self.mgmt_address_ipv6 = self.get_mgmt_address()
        self.mgmt_gw_ipv4, self.mgmt_gw_ipv6 = self.get_mgmt_gw()

        # Switch machine to q35 + split irqchip and add an Intel vIOMMU so the
        # guest can bind the data NICs to vfio-pci; add a balloon for RAM reclaim.
        self._patch_machine_and_iommu()

        # Build the config CD (node name + XR first-boot config) and attach it.
        iso = self._build_config_iso()
        self.qemu_args.extend(
            ["-drive", f"file={iso},if=none,id=cfgcd,format=raw,readonly=on",
             "-device", "ide-cd,drive=cfgcd"]
        )

    # ----------------------------------------------------------------- helpers
    def _patch_machine_and_iommu(self):
        args = self.qemu_args
        for i, a in enumerate(args):
            if a == "-machine" and i + 1 < len(args):
                args[i + 1] = "q35,kernel-irqchip=split"
                break
        args.extend([
            "-device", "intel-iommu,intremap=on,caching-mode=on",
            "-device", "virtio-balloon,id=balloon0",
        ])

    def _gen_xr_config(self):
        """XR first-boot config: clab user + management on MgmtEth in the global
        VRF. XRd vRouter's SSH server only listens in the default VRF (unlike
        gRPC), so management stays global. The mgmt subnet is directly connected,
        so no default route is needed and the data routing table stays clean.
        Data IPs come from the user's startup-config."""
        import ipaddress
        mgmt_if = ipaddress.ip_interface(self.mgmt_address_ipv4)  # e.g. 10.0.0.15/24
        mgmt_v4 = f"{mgmt_if.ip} {mgmt_if.netmask}"
        # Modelled on containerlab's official nodes/xrd/xrd.cfg. The bits XRd
        # needs for working SSH: `line default / transport input ssh` and
        # `secret` (not `secret 0`). MgmtEth gets the clab management address
        # explicitly; that subnet is directly connected so no route is needed.
        # gRPC/gNMI on 9339 to match clab tooling.
        cfg = f"""hostname {self.hostname}
username {self.username}
 group root-lr
 group cisco-support
 secret {self.password}
!
line default
 transport input ssh
!
netconf-yang agent
 ssh
!
interface MgmtEth0/RP0/CPU0/0
 ipv4 address {mgmt_v4}
 no shutdown
!
ssh server v2
ssh server netconf
!
grpc
 port 9339
 no-tls
 address-family dual
!
"""
        if os.path.exists(STARTUP_CONFIG_FILE):
            with open(STARTUP_CONFIG_FILE) as f:
                cfg += f.read()
            if not cfg.rstrip().endswith("end"):
                cfg += "\nend\n"
        else:
            cfg += "end\n"
        return cfg

    def _build_config_iso(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "nodename"), "w") as f:
            f.write(self.hostname + "\n")
        with open(os.path.join(d, "first-boot.cfg"), "w") as f:
            f.write(self._gen_xr_config())
        iso = "/xrd-config.iso"
        subprocess.run(
            ["genisoimage", "-quiet", "-output", iso, "-volid", "config",
             "-joliet", "-rock", d],
            check=True,
        )
        return iso

    # ------------------------------------------------------------- overrides
    def gen_nics(self):
        """Emulated data NICs (vmxnet3 or igb, per XRD_NIC_TYPE), each on its own
        PCIe root port (own IOMMU group), wired to the framework's tc taps
        (tapN <-> ethN)."""
        if self.conn_mode == "tc":
            self.create_tc_tap_ifup()
        # wait for containerlab to provision the data interfaces
        self.nic_provision_delay()

        res = []
        i = self.start_nic_eth_idx
        chassis = 1
        while os.path.exists(f"/sys/class/net/{self.data_intf_prefix}{i}"):
            mac = self.get_intf_mac(f"{self.data_intf_prefix}{i}") or vrnetlab.gen_mac(i)
            netdev = f"p{i:02d}"
            res.extend([
                "-device", f"pcie-root-port,id=rp{i},bus=pcie.0,chassis={chassis},slot={chassis}",
                "-device", f"{self.data_nic},netdev={netdev},mac={mac},bus=rp{i}",
            ])
            if self.conn_mode == "tc":
                res.extend([
                    "-netdev",
                    f"tap,id={netdev},ifname=tap{i},script=/etc/tc-tap-ifup,downscript=no",
                ])
            else:
                res.extend(["-netdev", f"socket,id={netdev},listen=:{i + 10000:02d}"])
            i += 1
            chassis += 1
        self.logger.info(f"generated {chassis - 1} {self.data_nic} data NIC(s)")
        return res

    def bootstrap_spin(self):
        """Wait for guest-init to signal XR readiness on the VM console."""
        if self.spins > 900:
            self.logger.warning("XRd failed to come up in time; restarting VM")
            self.stop()
            self.start()
            return

        (ridx, match, res) = self.con_expect([READY_MARKER.encode()])
        if match:
            self.logger.info("XRd vRouter is ready")
            self.running = True
            return
        if res != b"":
            self.write_to_stdout(res)
            self.spins = 0
        self.spins += 1


class XRd_vRouter(vrnetlab.VR):
    def __init__(self, hostname, username, password, nics, conn_mode, vcpu, ram):
        super().__init__(username, password)
        self.vms = [XRd_vRouter_vm(hostname, username, password, nics, conn_mode, vcpu, ram)]


if __name__ == "__main__":
    import argparse
    import logging

    parser = argparse.ArgumentParser(description="XRd vRouter vrnetlab launcher")
    parser.add_argument("--trace", action="store_true", help="enable trace logging")
    parser.add_argument("--hostname", default="vr-xrd-vrouter", help="Router hostname")
    parser.add_argument("--username", default="clab", help="Username")
    parser.add_argument("--password", default="clab@123", help="Password")
    parser.add_argument("--nics", type=int, default=128, help="Number of NICs")
    parser.add_argument("--vcpu", type=int, default=4, help="vCPU count")
    parser.add_argument("--ram", type=int, default=10240, help="RAM in MB")
    parser.add_argument("--connection-mode", default="tc", help="datapath connection mode")
    args = parser.parse_args()

    LOG_FORMAT = "%(asctime)s: %(module)-10s %(levelname)-8s %(message)s"
    logging.basicConfig(format=LOG_FORMAT)
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    if args.trace:
        logger.setLevel(1)

    vrnetlab.boot_delay()
    vr = XRd_vRouter(
        args.hostname, args.username, args.password,
        args.nics, args.connection_mode, args.vcpu, args.ram,
    )
    vr.start()
