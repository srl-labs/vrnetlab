#!/usr/bin/env python3

import datetime
import ipaddress
import json
import logging
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tarfile
import telnetlib
import tempfile
import time
from pathlib import Path

try:
    from scrapli import Driver
except ImportError:
    pass

MAX_RETRIES = 60

DEFAULT_SCRAPLI_TIMEOUT = 900

# set fancy logging colours
logging.addLevelName(
    logging.INFO, f"\x1b[1;32m\t{logging.getLevelName(logging.INFO)}\x1b[0m"
)
logging.addLevelName(
    logging.WARN, f"\x1b[1;38;5;220m\t{logging.getLevelName(logging.WARN)}\x1b[0m"
)
logging.addLevelName(
    logging.DEBUG, f"\x1b[1;94m\t{logging.getLevelName(logging.DEBUG)}\x1b[0m"
)
logging.addLevelName(
    logging.ERROR, f"\x1b[1;91m\t{logging.getLevelName(logging.ERROR)}\x1b[0m"
)
logging.addLevelName(
    logging.CRITICAL, f"\x1b[1;91m\t{logging.getLevelName(logging.CRITICAL)}\x1b[0m"
)


def gen_mac(last_octet=None):
    """Generate a random MAC address that is in recognizable (0C:00) OUI space
    and that has the given last octet.
    """
    return "0C:00:%02x:%02x:%02x:%02x" % (
        random.randint(0x00, 0xFF),
        random.randint(0x00, 0xFF),
        random.randint(0x00, 0xFF),
        last_octet,
    )


# sorting function to naturally sort interfaces by names
def natural_sort_key(s, _nsre=re.compile("([0-9]+)")):
    return [int(text) if text.isdigit() else text.lower() for text in _nsre.split(s)]


def run_command(cmd, cwd=None, background=False, shell=False):
    res = None
    try:
        if background:
            p = subprocess.Popen(cmd, cwd=cwd, shell=shell)
        else:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, cwd=cwd, shell=shell)
            res = p.communicate()
    except:
        pass
    return res


# boot_delay delays the VM boot by number of seconds
# set by BOOT_DELAY env var
def boot_delay():
    delay = os.getenv("BOOT_DELAY")
    if delay and (delay != "" or delay != 0):
        logging.getLogger().info(f"Delaying VM boot by {delay} seconds")
        time.sleep(int(delay))


class VM:
    def __str__(self):
        return self.__class__.__name__

    def _overlay_disk_image_format(self) -> str:
        res = run_command(["qemu-img", "info", "--output", "json", self.image])
        if res is not None:
            image_info = json.loads(res[0])
            if "format" in image_info:
                return image_info["format"]
        raise ValueError(f"Could not read image format for {self.image}")

    def __init__(
        self,
        username,
        password,
        disk_image="",
        num=0,
        ram=4096,
        driveif="ide",
        provision_pci_bus=True,
        cpu="host",
        smp="1",
        mgmt_passthrough=False,
        mgmt_intf="eth0",
        mgmt_dhcp=False,
        min_dp_nics=0,
        use_scrapli=False,
        data_intf_prefix="eth"
    ):
        self.use_scrapli = use_scrapli

        # configure logging
        self.logger = logging.getLogger()

        """
        Configure Scrapli logger to only be INFO level.
        Scrapli uses 'scrapli' logger by default, and
        will write all channel i/o as DEBUG log level.
        """
        self.scrapli_logger = logging.getLogger("scrapli")
        
        scrapli_log_level = logging.DEBUG if os.getenv("DEBUG_SCRAPLI", "false").lower() == "true" else logging.INFO
        self.scrapli_logger.setLevel(scrapli_log_level)
        
        

        # configure scrapli
        if self.use_scrapli:
            # init scrapli_tn -- main telnet device
            scrapli_tn_dev = {
                "host": "127.0.0.1",
                "port": 5000 + num,
                "auth_bypass": True,
                "auth_strict_key": False,
                "transport": "telnet",
                "timeout_socket": 3600,
                "timeout_transport": 3600,
                "timeout_ops": 3600,
            }

            self.scrapli_tn = Driver(**scrapli_tn_dev)

            # init scrapli_qm_dev -- qemu monitor device
            scrapli_qm_dev = {
                "host": "127.0.0.1",
                "port": 4000 + num,
                "auth_bypass": True,
                "auth_strict_key": False,
                "transport": "telnet",
                "timeout_socket": 3600,
                "timeout_transport": 3600,
                "timeout_ops": 3600,
            }

            self.scrapli_qm = Driver(**scrapli_qm_dev)

        # username / password to configure
        self.username = username
        self.password = password

        self.num = num
        self.image = disk_image

        self.running = False
        self.spins = 0
        self.p = None
        self.tn = None

        self._ram = ram
        self._cpu = cpu
        self._smp = smp
        self.mgmt_intf = os.environ.get("CLAB_MGMT_INTF", mgmt_intf)

        # various settings
        self.uuid = None
        self.fake_start_date = None
        self.nic_type = "e1000"
        self.num_nics = 0
        # number of nics that are actually *provisioned* (as in nics that will be added to container)
        self.num_provisioned_nics = int(os.environ.get("CLAB_INTFS", 0))
        # "highest" provisioned nic num -- used for making sure we can allocate nics without needing
        # to have them allocated sequential from eth1
        self.highest_provisioned_nic_num = 0

        # Whether the management interface is pass-through or host-forwarded.
        # Host-forwarded is the original vrnetlab mode where a VM gets a static IP for its management address,
        # which **does not** match the eth0 interface of a container.
        # In pass-through mode the VM container uses the same IP as the container's eth0 interface and transparently forwards traffic between the two interfaces.
        # See https://github.com/hellt/vrnetlab/issues/286
        self.mgmt_passthrough = (
            os.environ.get("CLAB_MGMT_PASSTHROUGH", "").lower() == "true"
            if os.environ.get("CLAB_MGMT_PASSTHROUGH")
            else mgmt_passthrough
        )

        # Check if CLAB_MGMT_DHCP environment variable is set
        self.mgmt_dhcp = (
            os.environ.get("CLAB_MGMT_DHCP", "").lower() == "true"
            if os.environ.get("CLAB_MGMT_DHCP")
            else mgmt_dhcp
        )

        # Check if CLAB_INTF_PREFIX environment variable is set
        self.data_intf_prefix = os.environ.get("CLAB_INTF_PREFIX", data_intf_prefix)

        # Populate management IP and gateway
        # If CLAB_MGMT_DHCP environment variable is set, we assume that a DHCP client
        # inside of the VM will take care about setting the management IP and gateway.
        if self.mgmt_passthrough:
            if self.mgmt_dhcp:
                self.mgmt_address_ipv4 = "dhcp"
                self.mgmt_address_ipv6 = "dhcp"
                self.mgmt_gw_ipv4 = "dhcp"
                self.mgmt_gw_ipv6 = "dhcp"
            else:
                self.mgmt_address_ipv4, self.mgmt_address_ipv6 = self.get_mgmt_address()
                self.mgmt_gw_ipv4, self.mgmt_gw_ipv6 = self.get_mgmt_gw()
        else:
            self.mgmt_address_ipv4 = "10.0.0.15/24"
            self.mgmt_address_ipv6 = "2001:db8::2/64"
            self.mgmt_gw_ipv4 = "10.0.0.2"
            self.mgmt_gw_ipv6 = "2001:db8::1"

        self.insuffucient_nics = False
        self.min_nics = 0
        # if an image needs minimum amount of dataplane nics to bootup, specify
        if min_dp_nics:
            self.min_nics = min_dp_nics

        # management subnet properties, defaults
        self.mgmt_subnet = "10.0.0.0/24"
        self.mgmt_host_ip = 2
        self.mgmt_guest_ip = 15

        #  Default TCP ports forwarded (TODO tune per platform):
        #  80    - http
        #  443   - https
        #  830   - netconf
        #  6030  - gnmi/gnoi arista
        #  8080  - sonic gnmi/gnoi, other http apis
        #  9339  - iana gnmi/gnoi
        #  32767 - gnmi/gnoi juniper
        #  50051 - cisco nx-os gnmi/gnoi
        #  57400 - nokia gnmi/gnoi
        self.mgmt_tcp_ports = [80, 443, 830, 6030, 8080, 9339, 32767, 50051, 57400]

        # we setup pci bus by default
        self.provision_pci_bus = provision_pci_bus
        self.nics_per_pci_bus = 26  # tested to work with XRv
        self.smbios = []

        self.start_nic_eth_idx = 1

        # wait_pattern is the pattern we wait on the serial connection when pushing config commands
        self.wait_pattern = "#"

        # Check if we're restoring from snapshot
        snapshot_disk = f"/snapshot-data/vm{self.num}/disk.qcow2"
        snapshot_state = f"/snapshot-data/vm{self.num}/state.img"
        snapshot_metadata = f"/snapshot-data/vm{self.num}/metadata.json"
        restoring_snapshot = os.path.exists(snapshot_disk) and os.path.exists(snapshot_state)

        # Load snapshot metadata if restoring
        self.snapshot_metadata = None
        if restoring_snapshot and os.path.exists(snapshot_metadata):
            with open(snapshot_metadata, "r") as f:
                self.snapshot_metadata = json.load(f)
            self.logger.info(f"Loaded snapshot metadata: {self.snapshot_metadata}")

        if restoring_snapshot:
            self.logger.info(f"Restoring from snapshot")
            self.logger.info(f"Snapshot disk: {snapshot_disk}")
            self.logger.info(f"Snapshot state: {snapshot_state}")
            overlay_disk_image = f"/snapshot-data/vm{self.num}/disk-overlay.qcow2"
            base_disk = snapshot_disk
            base_format = "qcow2"
        else:
            overlay_disk_image = re.sub(r"(\.[^.]+$)", r"-overlay\1", disk_image)
            if hasattr(self, "role"):
                tokens = overlay_disk_image.split(".")
                tokens[0] = tokens[0] + "-" + self.role + str(self.num)
                overlay_disk_image = ".".join(tokens)
            base_disk = disk_image
            base_format = self._overlay_disk_image_format()

        if not os.path.exists(overlay_disk_image):
            self.logger.debug(f"Creating overlay disk image: {overlay_disk_image}")
            run_command(
                [
                    "qemu-img",
                    "create",
                    "-f",
                    "qcow2",
                    "-F",
                    base_format,
                    "-b",
                    base_disk,
                    overlay_disk_image,
                ]
            )

        # Build qemu args
        self.qemu_args = [
            "qemu-system-x86_64",
            "-display",
            "none",
            "-machine",
            "pc",
            f"-chardev socket,id=monitor0,host=::,port=40{self.num:02d},server=on,wait=off",
            "-monitor chardev:monitor0",
            f"-chardev socket,id=serial0,host=::,port=50{self.num:02d},server=on,wait=off,telnet=on",
            "-serial chardev:serial0",
            "-m",  # memory
            str(self.ram),
            "-cpu",  # cpu type
            self.cpu,
            "-smp",
            self.smp,  # cpu core configuration
        ]

        # Always use -drive to create the disk device - migration requires exact device match
        self.qemu_args.extend(["-drive", f"if={driveif},file={overlay_disk_image}"])

        # If restoring from snapshot, add -incoming parameter
        if restoring_snapshot:
            self.qemu_args.extend(["-incoming", f"exec:cat\\ \\<\\ {snapshot_state}"])

        # add additional qemu args if they were provided
        if self.qemu_additional_args:
            self.qemu_args.extend(self.qemu_additional_args)

        # enable hardware assist if KVM is available
        if os.path.exists("/dev/kvm"):
            self.qemu_args.insert(1, "-enable-kvm")

    def start(self):
        # self.logger.info("Starting %s" % self.__class__.__name__)
        self.logger.info("START ENVIRONMENT VARIABLES".center(60, "-"))
        for var, value in sorted(os.environ.items()):
            self.logger.info(f"{var}: {value}")
        self.logger.info("END ENVIRONMENT VARIABLES".center(60, "-"))

        self.logger.info(
            f"Launching {self.__class__.__name__} with {self.smp} SMP/VCPU and {self.ram} M of RAM"
        )

        # give nice colours. Red if disabled, Green if enabled
        mgmt_passthrough_coloured = format_bool_color(
            self.mgmt_passthrough, "Enabled", "Disabled"
        )
        use_scrapli_coloured = format_bool_color(
            self.use_scrapli, "Enabled", "Disabled"
        )

        self.logger.info(f"Scrapli: {use_scrapli_coloured}")
        self.logger.info(f"Transparent mgmt interface: {mgmt_passthrough_coloured}")

        self.start_time = datetime.datetime.now()

        cmd = list(self.qemu_args)

        # uuid
        if self.uuid:
            cmd.extend(["-uuid", self.uuid])

        # do we have a fake start date?
        if self.fake_start_date:
            cmd.extend(["-rtc", "base=" + self.fake_start_date])

        # smbios
        # adding quotes to smbios value so it can be processed by bash shell
        for smbios_line in self.smbios:
            quoted_smbios = '"' + smbios_line + '"'
            cmd.extend(["-smbios", quoted_smbios])

        # Always create devices (NICs, PCI buses) - migration requires exact device match
        # setup PCI buses
        if self.provision_pci_bus:
            for i in range(1, math.ceil(self.num_nics / self.nics_per_pci_bus) + 1):
                cmd.extend(["-device", f"pci-bridge,chassis_nr={i},id=pci.{i}"])

        # generate mgmt NICs
        cmd.extend(self.gen_mgmt())
        # generate normal NICs
        cmd.extend(self.gen_nics())
        # generate dummy NICs
        if self.insuffucient_nics:
            cmd.extend(self.gen_dummy_nics())

        self.logger.debug("qemu cmd: {}".format(" ".join(cmd)))

        self.p = subprocess.Popen(
            " ".join(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            shell=True,
            executable="/bin/bash",
        )

        try:
            outs, errs = self.p.communicate(timeout=2)
            self.logger.info("STDOUT: %s" % outs)
            self.logger.info("STDERR: %s" % errs)
        except:
            pass

        for i in range(1, MAX_RETRIES + 1):
            try:
                if self.use_scrapli:
                    self.scrapli_qm.open()
                else:
                    self.qm = telnetlib.Telnet("127.0.0.1", 4000 + self.num)
                break
            except:
                self.logger.error(
                    "Unable to connect to qemu monitor (port {}), retrying in a second (attempt {})".format(
                        4000 + self.num, i
                    )
                )
                time.sleep(1)
            if i == MAX_RETRIES:
                raise QemuBroken(
                    "Unable to connect to qemu monitor on port {}".format(
                        4000 + self.num
                    )
                )

        for i in range(1, MAX_RETRIES + 1):
            try:
                if self.use_scrapli:
                    self.scrapli_tn.open()
                else:
                    self.tn = telnetlib.Telnet("127.0.0.1", 5000 + self.num)
                break
            except:
                self.logger.error(
                    "Unable to connect to qemu monitor (port {}), retrying in a second (attempt {})".format(
                        5000 + self.num, i
                    )
                )
                time.sleep(1)
            if i == MAX_RETRIES:
                raise QemuBroken(
                    "Unable to connect to qemu monitor on port {}".format(
                        5000 + self.num
                    )
                )

        # If we restored from snapshot, resume the VM (it's paused after migration)
        snapshot_state = f"/snapshot-data/vm{self.num}/state.img"
        if os.path.exists(snapshot_state):
            self.logger.info("Resuming VM after snapshot restore")
            if self.use_scrapli:
                self.scrapli_qm.channel.write("cont\r")
            else:
                self.qm.write(b"cont\r")
            time.sleep(2)
            self.logger.info("VM resumed")
            # Mark VM as running - snapshot restored a fully booted VM
            self.running = True
            self.logger.info("VM marked as running (restored from snapshot)")

            # Close serial console connection to free it for external access
            try:
                if self.use_scrapli:
                    self.scrapli_tn.close()
                    self.logger.info("Closed scrapli serial connection")
                else:
                    self.tn.close()
                    self.logger.info("Closed telnet serial connection")
            except Exception as e:
                self.logger.warning(f"Could not close serial connection: {e}")

        try:
            outs, errs = self.p.communicate(timeout=2)
            self.logger.info("STDOUT: %s" % outs)
            self.logger.info("STDERR: %s" % errs)
        except:
            pass

    def create_tc_tap_ifup(self):
        """Create tap ifup script that is used in tc datapath mode"""
        ifup_script = """#!/bin/bash

        TAP_IF=$1
        # get interface index number up to 3 digits (everything after first three chars)
        # tap0 -> 0
        # tap123 -> 123
        INDEX=${TAP_IF:3:3}

        ip link set $TAP_IF up
        ip link set $TAP_IF mtu 65000

        # disable IPv6 to avoid sending periodic traffic like router solicitations from the vrnetlab container
        ip -6 addr flush $TAP_IF

        # create tc eth<->tap redirect rules
        tc qdisc add dev {INTF_PREFIX}$INDEX clsact
        tc filter add dev {INTF_PREFIX}$INDEX ingress flower action mirred egress redirect dev tap$INDEX

        tc qdisc add dev $TAP_IF clsact
        tc filter add dev $TAP_IF ingress flower action mirred egress redirect dev {INTF_PREFIX}$INDEX
        """
        
        ifup_script = ifup_script.replace("{INTF_PREFIX}", self.data_intf_prefix)

        with open("/etc/tc-tap-ifup", "w") as f:
            f.write(ifup_script)
        os.chmod("/etc/tc-tap-ifup", 0o777)

    def create_tc_tap_mgmt_ifup(self):
        """Create tap ifup script that is used in tc datapath mode, specifically for the management interface"""
        ifup_script = """#!/bin/bash

        ip link set tap0 up
        ip link set tap0 mtu 65000

        # disable IPv6 to avoid sending periodic traffic like router solicitations from the vrnetlab container
        ip -6 addr flush $TAP_IF

        # create tc eth<->tap redirect rules

        tc qdisc add dev {MGMT_INTF} clsact
        # exception for TCP ports 5000-5007
        tc filter add dev {MGMT_INTF} ingress prio 1 protocol ip flower ip_proto tcp dst_port 5000-5007 action pass
        # mirror ARP traffic to container
        tc filter add dev {MGMT_INTF} ingress prio 2 protocol arp flower action mirred egress mirror dev tap0
        # redirect rest of ingress traffic of eth0 to egress of tap0
        tc filter add dev {MGMT_INTF} ingress prio 3 flower action mirred egress redirect dev tap0

        tc qdisc add dev tap0 clsact
        # redirect all ingress traffic of tap0 to egress of eth0
        tc filter add dev tap0 ingress flower action mirred egress redirect dev {MGMT_INTF}

        # clone management MAC of the VM
        ip link set dev {MGMT_INTF} address {MGMT_MAC}
        """

        ifup_script = ifup_script.replace("{MGMT_INTF}", self.mgmt_intf)
        ifup_script = ifup_script.replace("{MGMT_MAC}", self.mgmt_mac)

        with open("/etc/tc-tap-mgmt-ifup", "w") as f:
            f.write(ifup_script)
        os.chmod("/etc/tc-tap-mgmt-ifup", 0o777)

    def get_mgmt_mac(self, last_octet=0) -> str:
        """Get the MAC address for the management interface from the envvar
        `CLAB_MGMT_MAC` or generate a random one using `gen_mac(last_octet)`.
        """
        return os.environ.get("CLAB_MGMT_MAC") or gen_mac(last_octet)

    def gen_mgmt(self):
        """Generate qemu args for the mgmt interface(s)

        Default TCP ports forwarded:
          80    - http
          443   - https
          830   - netconf
          6030  - gnmi/gnoi arista
          8080  - sonic gnmi/gnoi, other http apis
          9339  - iana gnmi/gnoi
          32767 - gnmi/gnoi juniper
          50051 - cisco nx-os gnmi/gnoi
          57400 - nokia gnmi/gnoi
        """
        if self.mgmt_host_ip + 1 >= self.mgmt_guest_ip:
            self.logger.error(
                "Guest IP (%s) must be at least 2 higher than host IP(%s)",
                self.mgmt_guest_ip,
                self.mgmt_host_ip,
            )

        network = ipaddress.ip_network(self.mgmt_subnet)
        host = str(network[self.mgmt_host_ip])
        dns = str(network[self.mgmt_host_ip + 1])
        guest = str(network[self.mgmt_guest_ip])

        res = []
        res.append("-device")

        # If restoring from snapshot, use the saved management MAC address
        if self.snapshot_metadata and "mac_addresses" in self.snapshot_metadata and len(self.snapshot_metadata["mac_addresses"]) > 0:
            self.mgmt_mac = self.snapshot_metadata["mac_addresses"][0]
            self.logger.info(f"Using saved management MAC: {self.mgmt_mac}")
        else:
            self.mgmt_mac = self.get_mgmt_mac()

        res.append(self.nic_type + f",netdev=p00,mac={self.mgmt_mac}")
        res.append("-netdev")

        if self.mgmt_passthrough:
            # mgmt interface is passthrough - we just create a normal mirred tap interface
            res.append(
                "tap,id=p00,ifname=tap0,script=/etc/tc-tap-mgmt-ifup,downscript=no"
            )
            self.create_tc_tap_mgmt_ifup()
        else:
            # mgmt interface is host-forwarded - we use qemu user mode network
            # with hostfwd rules to forward ports from the host to the guest
            res.append(
                f"user,id=p00,net={self.mgmt_subnet},host={host},dns={dns},dhcpstart={guest},"
                + f"hostfwd=tcp:0.0.0.0:22-{guest}:22,"  # ssh
                + f"hostfwd=udp:0.0.0.0:161-{guest}:161,"  # snmp
                + (
                    ",".join(
                        [
                            f"hostfwd=tcp:0.0.0.0:{p}-{guest}:{p}"
                            for p in self.mgmt_tcp_ports
                        ]
                    )
                )
                + ",tftp=/tftpboot"
            )

        return res

    def get_mgmt_address(self):
        """Returns the IPv4 and IPv6 address of the eth0 interface of the container"""
        stdout, _ = run_command(["ip", "--json", "address", "show", "dev", "eth0"])
        command_json = json.loads(stdout.decode("utf-8"))
        intf_addrinfos = command_json[0]["addr_info"]
        mgmt_cidr_v4 = None
        mgmt_cidr_v6 = None
        for addrinfo in intf_addrinfos:
            if addrinfo["family"] == "inet" and addrinfo["scope"] == "global":
                mgmt_address_v4 = addrinfo["local"]
                mgmt_prefixlen_v4 = addrinfo["prefixlen"]
                mgmt_cidr_v4 = mgmt_address_v4 + "/" + str(mgmt_prefixlen_v4)
            if addrinfo["family"] == "inet6" and addrinfo["scope"] == "global":
                mgmt_address_v6 = addrinfo["local"]
                mgmt_prefixlen_v6 = addrinfo["prefixlen"]
                mgmt_cidr_v6 = mgmt_address_v6 + "/" + str(mgmt_prefixlen_v6)

        if not mgmt_cidr_v4:
            raise ValueError("No IPv4 address set on management interface eth0!")

        return mgmt_cidr_v4, mgmt_cidr_v6

    def get_mgmt_gw(self):
        """Returns the IPv4 and IPv6 default gateways of the container, used for generating the management default route"""
        stdout_v4, _ = run_command(["ip", "--json", "-4", "route", "show", "default"])
        command_json_v4 = json.loads(stdout_v4.decode("utf-8"))
        try:
            mgmt_gw_v4 = command_json_v4[0]["gateway"]
        except IndexError as e:
            raise IndexError(
                "No default gateway route on management interface eth0!"
            ) from e

        stdout_v6, _ = run_command(["ip", "--json", "-6", "route", "show", "default"])
        command_json_v6 = json.loads(stdout_v6.decode("utf-8"))
        try:
            mgmt_gw_v6 = command_json_v6[0]["gateway"]
        except IndexError:
            mgmt_gw_v6 = None

        return mgmt_gw_v4, mgmt_gw_v6

    def get_intf_mac(self, intf_name: str) -> str:
        """Get the MAC address of a container interface
        
        Returns the MAC address of the specified interface, or None if not found.
        This is used to sync VM NIC MAC addresses with container interface MAC addresses
        for proper macvlan bridge mode operation.
        """
        try:
            stdout, _ = run_command(["ip", "--json", "link", "show", "dev", intf_name])
            if stdout:
                command_json = json.loads(stdout.decode("utf-8"))
                if command_json and len(command_json) > 0:
                    mac_address = command_json[0].get("address")
                    if mac_address:
                        self.logger.debug(f"Found MAC address {mac_address} for interface {intf_name}")
                        return mac_address
        except Exception as e:
            self.logger.debug(f"Could not get MAC address for {intf_name}: {e}")
        
        return None

    def nic_provision_delay(self) -> None:
        self.logger.debug(
            f"number of provisioned data plane interfaces is {self.num_provisioned_nics}"
        )

        # no nics provisioned and/or not running from containerlab so we can bail
        if self.num_provisioned_nics == 0:
            # unless the node has a minimum nic requirement
            if self.min_nics:
                self.insuffucient_nics = True
            return

        self.logger.debug("waiting for provisioned interfaces to appear...")

        # start_eth means eth index for VM
        # particularly for multiple slot LC
        start_eth = self.start_nic_eth_idx
        end_eth = self.start_nic_eth_idx + self.num_nics

        inf_path = Path("/sys/class/net/")
        while True:
            provisioned_nics = list(inf_path.glob(f"{self.data_intf_prefix}*"))
            # if we see num provisioned +1 (for mgmt) we have all nics ready to roll!
            if len(provisioned_nics) >= self.num_provisioned_nics + 1:
                nics = [
                    int(re.search(pattern=r"\d+", string=nic.name).group())
                    for nic in provisioned_nics
                ]

                # Ensure the max eth is in range of allocated eth index of VM LC
                nics = [nic for nic in nics if nic in range(start_eth, end_eth)]

                if nics:
                    self.highest_provisioned_nic_num = max(nics)

                self.logger.debug(
                    f"highest allocated interface id determined to be: {self.highest_provisioned_nic_num}..."
                )
                self.logger.debug("interfaces provisioned, continuing...")
                break
            time.sleep(5)

        # check if we need to provision any more nics, do this after because they shouldn't interfere with the provisioned nics
        if self.num_provisioned_nics < self.min_nics:
            self.insuffucient_nics = True

    # if insuffucient amount of nics are defined in the topology file, generate dummmy nics so cat9kv can boot.
    def gen_dummy_nics(self):
        # calculate required num of nics to generate
        nics = self.min_nics - self.num_provisioned_nics

        self.logger.debug(f"Insuffucient NICs defined. Generating {nics} dummy nics")

        res = []

        pci_bus_ctr = self.num_provisioned_nics

        for i in range(0, nics):
            # dummy interface naming
            interface_name = f"dummy{str(i + self.num_provisioned_nics)}"

            # PCI bus counter is to ensure pci bus index starts from 1
            # and continuing in sequence regardles the eth index
            pci_bus_ctr += 1

            pci_bus = math.floor(pci_bus_ctr / self.nics_per_pci_bus) + 1
            addr = (pci_bus_ctr % self.nics_per_pci_bus) + 1

            res.extend(
                [
                    "-device",
                    f"{self.nic_type},netdev={interface_name},id={interface_name},mac={gen_mac(i)},bus=pci.{pci_bus},addr=0x{addr}",
                    "-netdev",
                    f"tap,ifname={interface_name},id={interface_name},script=no,downscript=no",
                ]
            )
        return res

    def gen_nics(self):
        """Generate qemu args for the normal traffic carrying interface(s)"""
        self.nic_provision_delay()

        res = []

        if self.conn_mode == "tc":
            self.create_tc_tap_ifup()

        start_eth = self.start_nic_eth_idx
        end_eth = self.start_nic_eth_idx + self.num_nics
        pci_bus_ctr = 0
        for i in range(start_eth, end_eth):
            # PCI bus counter is to ensure pci bus index starts from 1
            # and continuing in sequence regardles the eth index
            pci_bus_ctr += 1

            # calc which PCI bus we are on and the local add on that PCI bus
            x = pci_bus_ctr
            if "vEOS" in self.image:
                x = pci_bus_ctr + 1

            pci_bus = math.floor(x / self.nics_per_pci_bus) + 1
            addr = (x % self.nics_per_pci_bus) + 1

            # if the matching container interface ethX doesn't exist, we don't create a nic
            if not os.path.exists(f"/sys/class/net/{self.data_intf_prefix}{i}"):
                if i >= self.highest_provisioned_nic_num:
                    continue

                # current intf number is *under* the highest provisioned nic number, so we need
                # to allocate a "dummy" interface so that when the users data plane interface is
                # actually provisioned it is provisioned in the appropriate "slot"
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

            mac = None
            # If restoring from snapshot, use saved MAC addresses
            # MAC at index 0 is management, so data plane NICs start at index 1
            if self.snapshot_metadata and "mac_addresses" in self.snapshot_metadata:
                mac_index = 1 + (i - start_eth)
                if mac_index < len(self.snapshot_metadata["mac_addresses"]):
                    mac = self.snapshot_metadata["mac_addresses"][mac_index]
                    self.logger.info(f"Using saved MAC from snapshot for eth{i}: {mac}")

            # otherwise get mac from container interface
            if not mac:
                intf_name = f"{self.data_intf_prefix}{i}"
                mac = self.get_intf_mac(intf_name)
                if mac:
                    self.logger.info(f"Using container interface MAC {mac} for VM NIC {intf_name}")

            # otherwise generate the mac
            if not mac:
                mac = gen_mac(i)
                self.logger.debug(f"Generated MAC {mac} for VM NIC eth{i}")

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
                    f"tap,id=p{i:02d},ifname=tap{i},script=/etc/tc-tap-ifup,downscript=no"
                )

        return res

    def stop(self):
        """Stop this VM"""
        self.running = False

        try:
            self.p.terminate()
        except ProcessLookupError:
            return

        try:
            self.p.communicate(timeout=10)
        except:
            try:
                # this construct is included as an example at
                # https://docs.python.org/3.6/library/subprocess.html but has
                # failed on me so wrapping in another try block. It was this
                # communicate() that failed with:
                # ValueError: Invalid file object: <_io.TextIOWrapper name=3 encoding='ANSI_X3.4-1968'>
                self.p.kill()
                self.p.communicate(timeout=10)
            except:
                # just assume it's dead or will die?
                self.p.wait(timeout=10)

    def restart(self):
        """Restart this VM"""
        self.stop()
        self.start()

    def wait_write(
        self, cmd, wait="__defaultpattern__", con=None, clean_buffer=False, hold=""
    ):
        """Wait for something on the serial port and then send command

        Defaults to using self.tn as connection but this can be overridden
        by passing a telnetlib.Telnet object in the con argument.
        """

        if self.use_scrapli:
            return self.wait_write_scrapli(cmd, wait)

        con_name = "custom con"
        if con is None:
            con = self.tn

        if con == self.tn:
            con_name = "serial console"
        if con == self.qm:
            con_name = "qemu monitor"

        if wait:
            # use class default wait pattern if none was explicitly specified
            if wait == "__defaultpattern__":
                wait = self.wait_pattern
            self.logger.info(f"waiting for '{wait}' on {con_name}")
            res = con.read_until(wait.encode())

            while hold and (hold in res.decode()):
                self.logger.info(
                    f"Holding pattern '{hold}' detected: {res.decode()}, retrying in 10s..."
                )
                con.write("\r".encode())
                time.sleep(10)
                res = con.read_until(wait.encode())

            cleaned_buf = (
                (con.read_very_eager()) if clean_buffer else None
            )  # Clear any remaining characters in buffer

            self.logger.info(f"read from {con_name}: '{res.decode()}'")
            # log the cleaned buffer if it's not empty
            if cleaned_buf:
                self.logger.info(f"cleaned buffer: '{cleaned_buf.decode()}'")

        self.logger.debug(f"writing to {con_name}: '{cmd}'")
        con.write("{}\r".format(cmd).encode())

    def wait_write_scrapli(self, cmd, wait="__defaultpattern__"):
        """
        Wait for something on the serial port and then send command using Scrapli telnet channel

        Arguments are:
        - cmd: command to send (string)
        - wait: prompt to wait for before sending command, defaults to # (string)
        """
        if wait:
            # use class default wait pattern if none was explicitly specified
            if wait == "__defaultpattern__":
                wait = self.wait_pattern

            self.logger.info(f"Waiting on console for: '{wait}'")

            self.con_read_until(wait)

        time.sleep(0.1)  # don't write to the console too fast

        self.write_to_stdout(b"\n")

        self.logger.info(f"Writing to console: '{cmd}'")
        self.scrapli_tn.channel.write(f"{cmd}\r")

    def con_expect(self, regex_list, timeout=None):
        """
        Implements telnetlib expect() functionality, for usage with scrapli driver.
        Wait for something on the console.

        Takes list of byte strings and an optional timeout (block) time (float) as arguments.

        Returns tuple of:
        - index of matched object from regex.
        - match object.
        - buffer of cosole read until match, or function exit.
        """

        buf = b""

        if timeout:
            t_end = time.time() + timeout
            while time.time() < t_end:
                buf += self.scrapli_tn.channel.read()
        else:
            buf = self.scrapli_tn.channel.read()

        for i, obj in enumerate(regex_list):
            match = re.search(obj.decode(), buf.decode())
            if match:
                return i, match, buf

        return -1, None, buf

    def con_read_until(self, match_str, timeout=None):
        """
        Implements telnetlib read_until() functionality, for usage with scrapli driver.

        Read until a given string is encountered or until timeout.

        When no match is found, return whatever is available instead,
        possibly the empty string.

        Arguments:
        - match_str: string to match on (string)
        - timeout: timeout in seconds, defaults to None (float)
        """
        buf = b""

        if timeout:
            t_end = time.time() + timeout

        while True:
            current_buf = self.scrapli_tn.channel.read()
            buf += current_buf

            match = re.search(match_str, current_buf.decode())

            # for reliability purposes, doublecheck the entire buffer
            # maybe the current buffer only has partial output
            if match is None:
                match = re.search(match_str, buf.decode())

            self.write_to_stdout(current_buf)

            if match:
                break
            if timeout and time.time() > t_end:
                break

        return buf

    def write_to_stdout(self, bytes):
        """
        Quick and dirty way to write to stdout (docker logs) instead of
        using the python logger which poorly formats the output.

        Mainly for printing console to docker logs
        """
        sys.stdout.buffer.write(bytes)
        sys.stdout.buffer.flush()

    def _get_overlay_path(self):
        """Helper to get current overlay disk path."""
        for i, arg in enumerate(self.qemu_args):
            if arg == "-drive":
                drive_spec = self.qemu_args[i + 1]
                for part in drive_spec.split(","):
                    if part.startswith("file="):
                        return part.split("=", 1)[1]
        raise Exception("Could not find overlay disk path")

    def snapshot_save_to_dir(self, vm_dir):
        """Save VM state to directory using QEMU migrate to file."""
        os.makedirs(vm_dir, exist_ok=True)

        state_file = os.path.join(vm_dir, "state.img")
        disk_file = os.path.join(vm_dir, "disk.qcow2")
        overlay_path = self._get_overlay_path()

        try:
            # Pause VM
            self.logger.info(f"Pausing VM {self.num}")
            if self.use_scrapli:
                self.scrapli_qm.channel.write("stop\r")
            else:
                self.qm.write(b"stop\r")
            time.sleep(1)

            # Dump state via migration
            self.logger.info(f"Saving state for VM {self.num}")
            cmd = f'migrate "exec:cat > {state_file}"\r'
            if self.use_scrapli:
                self.scrapli_qm.channel.write(cmd)
            else:
                self.qm.write(cmd.encode())

            # Wait for migration complete
            timeout = 300
            start = time.time()
            while time.time() - start < timeout:
                time.sleep(2)
                if self.use_scrapli:
                    self.scrapli_qm.channel.write("info migrate\r")
                else:
                    self.qm.write(b"info migrate\r")
                time.sleep(0.5)

                if self.use_scrapli:
                    response = self.scrapli_qm.channel.read().decode()
                else:
                    response = self.qm.read_very_eager().decode()

                if "completed" in response:
                    break
                elif "failed" in response:
                    raise Exception("Migration failed")

            # Copy disk
            self.logger.info(f"Copying disk for VM {self.num}")
            shutil.copy2(overlay_path, disk_file)

            # Save metadata (MAC addresses for NICs) to restore devices correctly
            metadata_file = os.path.join(vm_dir, "metadata.json")
            metadata = {
                "mac_addresses": [],
                "num_nics": self.num_nics,
                "provision_pci_bus": self.provision_pci_bus,
                "nics_per_pci_bus": self.nics_per_pci_bus
            }

            # Extract MAC addresses from QEMU via "info network" command
            try:
                if self.use_scrapli:
                    self.scrapli_qm.channel.write("info network\r")
                    time.sleep(0.5)
                    response = self.scrapli_qm.channel.read().decode()
                else:
                    self.qm.write(b"info network\r")
                    time.sleep(0.5)
                    response = self.qm.read_very_eager().decode()

                # Extract MAC addresses using regex pattern
                mac_pattern = r'macaddr=([0-9a-f:]{17})'
                macs = re.findall(mac_pattern, response, re.IGNORECASE)
                metadata["mac_addresses"] = macs
                self.logger.info(f"Extracted MAC addresses from QEMU: {macs}")
            except Exception as e:
                self.logger.warning(f"Could not extract MACs from QEMU: {e}")

            with open(metadata_file, "w") as f:
                json.dump(metadata, f)
            self.logger.info(f"Saved device metadata: {metadata}")

            # Resume VM
            self.logger.info(f"Resuming VM {self.num}")
            if self.use_scrapli:
                self.scrapli_qm.channel.write("cont\r")
            else:
                self.qm.write(b"cont\r")

            return {"state": state_file, "disk": disk_file, "metadata": metadata_file}

        except Exception as e:
            # Always try to resume
            try:
                if self.use_scrapli:
                    self.scrapli_qm.channel.write("cont\r")
                else:
                    self.qm.write(b"cont\r")
            except:
                pass
            raise Exception(f"Snapshot failed for VM {self.num}: {e}")

    def work(self):
        self.check_qemu()
        if not self.running:
            try:
                self.bootstrap_spin()
            except EOFError:
                self.logger.error("Telnet session was disconnected, restarting")
                self.restart()

    def check_qemu(self):
        """Check health of qemu. This is mostly just seeing if there's error
        output on STDOUT from qemu which means we restart it.
        """
        if self.p is None:
            self.logger.debug("VM not started; starting!")
            self.start()

        # check for output
        try:
            outs, errs = self.p.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            return
        self.logger.info("STDOUT: %s" % outs)
        self.logger.info("STDERR: %s" % errs)

        if errs != "":
            self.logger.debug("KVM error, restarting")
            self.stop()
            self.start()

    @property
    def version(self):
        """Read version number from VERSION environment variable

        The VERSION environment variable is set at build time using the value
        from the makefile. If the environment variable is not defined please add
        the variables in the Dockerfile (see csr)"""
        version = os.environ.get("VERSION")
        if version is not None:
            return version
        raise ValueError("The VERSION environment variable is not set")

    @property
    def ram(self):
        """
        Read memory size from the QEMU_MEMORY environment variable and use it in the qemu parameters for the VM.
        If the QEMU_MEMORY environment variable is not set, use the default value.
        Should be provided as a number of MB. e.g. 4096.
        """

        if "QEMU_MEMORY" in os.environ:
            return get_digits(str(os.getenv("QEMU_MEMORY")))

        return self._ram

    @property
    def cpu(self):
        """
        Read the CPU type the QEMU_CPU environment variable and use it in the qemu parameters for the VM.
        If the QEMU_CPU environment variable is not set, use the default value.
        """

        if "QEMU_CPU" in os.environ:
            return str(os.getenv("QEMU_CPU"))

        return str(self._cpu)

    @property
    def smp(self):
        """
        Read SMP parameter (e.g. number of CPU cores) from the QEMU_SMP environment variable.
        If the QEMU_SMP parameter is not set, the default value is used.
        Should be provided as a number, e.g. 2
        """

        if "QEMU_SMP" in os.environ:
            return str(os.getenv("QEMU_SMP"))

        return str(self._smp)

    @property
    def qemu_additional_args(self):
        """
        Read additional qemu arguments (e.g. number of CPU cores) from the QEMU_ADDITIONAL_ARGS environment variable.
        If the QEMU_ADDITIONAL_ARGS parameter is not set, nothing is added to the default args set.
        Should be provided as a space separated list of arguments, e.g. "-machine pc -display none"
        """

        if "QEMU_ADDITIONAL_ARGS" in os.environ:
            s = str(os.getenv("QEMU_ADDITIONAL_ARGS"))
            if s:
                return s.split()


class VR:
    def __init__(self, username, password, mgmt_passthrough: bool = False):
        self.logger = logging.getLogger()

        # Whether the management interface is pass-through or host-forwarded.
        # Host-forwarded is the original vrnetlab mode where a VM gets a static IP for its management address,
        # which **does not** match the eth0 interface of a container.
        # In pass-through mode the VM container uses the same IP as the container's eth0 interface and transparently forwards traffic between the two interfaces.
        # See https://github.com/hellt/vrnetlab/issues/286
        self.mgmt_passthrough = mgmt_passthrough
        mgmt_passthrough_override = os.environ.get("CLAB_MGMT_PASSTHROUGH", "")
        if mgmt_passthrough_override:
            self.mgmt_passthrough = mgmt_passthrough_override.lower() == "true"

        # Check if we should restore from snapshot before VMs are created
        if os.environ.get("RESTORE_SNAPSHOT") == "1" and os.path.exists("/snapshot.tar"):
            self.logger.info("Restoring from /snapshot.tar")
            self.snapshot_restore()

        try:
            os.mkdir("/tftpboot")
        except:
            pass

    def update_health(self, exit_status, message):
        health_file = open("/health", "w")
        health_file.write("%d %s" % (exit_status, message))
        health_file.close()

    def snapshot_save(self):
        """Save all VMs to snapshot tar archive."""
        tar_path = "/snapshot-output.tar"
        self.logger.info(f"Creating snapshot: {tar_path}")

        # Create temp directory for snapshot data
        with tempfile.TemporaryDirectory() as tmpdir:
            # Save each VM
            for vm in self.vms:
                vm_dir = os.path.join(tmpdir, f"vm{vm.num}")
                try:
                    vm.snapshot_save_to_dir(vm_dir)
                    self.logger.info(f"VM {vm.num} saved")
                except Exception as e:
                    self.logger.error(f"Failed to save VM {vm.num}: {e}")
                    raise

            # Create tar
            self.logger.info(f"Creating tar archive")
            with tarfile.open(tar_path, "w") as tar:
                for vm in self.vms:
                    vm_dir = os.path.join(tmpdir, f"vm{vm.num}")
                    tar.add(vm_dir, arcname=f"vm{vm.num}")

        self.logger.info(f"Snapshot saved: {tar_path}")
        return tar_path

    def snapshot_restore(self):
        """Restore snapshot data from tar archive to prepare for VM restore."""
        tar_path = "/snapshot.tar"
        extract_dir = "/snapshot-data"

        if not os.path.exists(tar_path):
            raise Exception(f"Snapshot file not found: {tar_path}")

        self.logger.info(f"Extracting snapshot: {tar_path}")
        os.makedirs(extract_dir, exist_ok=True)

        with tarfile.open(tar_path, "r") as tar:
            tar.extractall(extract_dir)

        self.logger.info(f"Snapshot extracted to: {extract_dir}")
        return extract_dir

    def start(self):
        """Start the virtual router"""
        self.logger.debug("Starting vrnetlab %s" % self.__class__.__name__)
        self.logger.debug("VMs: %s" % self.vms)

        started = False
        while True:
            all_running = True
            for vm in self.vms:
                vm.work()
                if vm.running != True:
                    all_running = False

            if all_running:
                self.update_health(0, "running")
                started = True
            else:
                if started:
                    self.update_health(1, "VM failed - restarting")
                else:
                    self.update_health(1, "starting")

            # file-based signalling backdoor to trigger a system reset (via qemu-monitor) on all or specific VMs.
            # if file is empty: reset whole VR (all VMs)
            # if file is non-empty: reset only specified VMs (comma separated list)
            if os.path.exists("/reset"):
                with open("/reset", "rt") as f:
                    fcontent = f.read().strip()
                vm_num_list = fcontent.split(",")
                for vm in self.vms:
                    if (str(vm.num) in vm_num_list) or not fcontent:
                        try:
                            if vm.use_scrapli:
                                vm.scrapli_qm.channel.write("system_reset\r")
                            else:
                                vm.qm.write("system_reset\r".encode())
                            self.logger.debug(
                                f"Sent qemu-monitor system_reset to VM num {vm.num} "
                            )
                        except Exception as e:
                            self.logger.error(
                                f"Failed to send qemu-monitor system_reset to VM num {vm.num} ({e})"
                            )
                try:
                    os.remove("/reset")
                except Exception as e:
                    self.logger.error(
                        f"Failed to cleanup /reset file({e}). qemu-monitor system_reset will likely be triggered again on VMs"
                    )

            # Snapshot save
            if os.path.exists("/snapshot-save"):
                try:
                    self.snapshot_save()
                    self.logger.info("Snapshot saved to /snapshot-output.tar")
                except Exception as e:
                    self.logger.error(f"Snapshot save failed: {e}")

                try:
                    os.remove("/snapshot-save")
                except Exception as e:
                    self.logger.error(f"Failed to cleanup /snapshot-save: {e}")


class QemuBroken(Exception):
    """Our Qemu instance is somehow broken"""


def get_digits(input_str: str) -> int:
    """
    Strip all non-numeric characters from a string
    """

    non_string_chars = re.findall(r"\d", input_str)
    return int("".join(non_string_chars))


def cidr_to_ddn(prefix: str) -> list[str]:
    """
    Convert a IPv4 CIDR notation prefix to address + mask in DDN notation

    Returns a list of IP address (str) and mask (str) in dotted decimal

    Example:
    get_ddn_mask('192.168.0.1/24')
    returns ['192.168.0.1' ,'255.255.255.0']
    """

    network = ipaddress.IPv4Interface(prefix)
    return [str(network.ip), str(network.netmask)]


def format_bool_color(bool_var: bool, text_if_true: str, text_if_false: str) -> str:
    """
    Generate a ANSI escape code colored string based on a boolean.

    Args:
    bool_var:       Boolean to be evaluated
    text_if_true:   Text returned if bool_var is true -- ANSI Formatted in green color
    text_if_false:  Text returned if bool_var is false -- ANSI Formatted in red color
    """
    return (
        f"\x1b[32m{text_if_true}\x1b[0m"
        if bool_var
        else f"\x1b[31m{text_if_false}\x1b[0m"
    )
