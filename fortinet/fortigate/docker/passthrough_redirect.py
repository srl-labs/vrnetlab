import logging
import os

import vrnetlab
from net_mgmt_strategy import NetMgmtStrategy

DEFAULT_VETH_MAC_ADDR = "3a:3a:3a:3a:3a:3a"
DEFAULT_NS_NAME = "fakehost"
DEFAULT_ROOT_NS_VETH_LINK_NAME = "RA"
DEFAULT_PRIV_NS_VETH_LINK_NAME = "FA"
DEFAULT_TEMP_REDIR_DST = "169.254.254.254/16"


class PassthroughRedirect(NetMgmtStrategy):
    mgmt_passthrough = True

    def __init__(self,
                 target_port_ranges,
                 logger: logging.Logger = None,
                 ns_name=DEFAULT_NS_NAME,
                 veth_mac_addr=DEFAULT_VETH_MAC_ADDR,
                 veth_root_ns_link_name=DEFAULT_ROOT_NS_VETH_LINK_NAME,
                 veth_priv_ns_link_name=DEFAULT_PRIV_NS_VETH_LINK_NAME,
                 temp_redir_dst=DEFAULT_TEMP_REDIR_DST):
        """

        :param target_port_ranges: List of strings with format: "<tcp | udp>:<port | port-port>".
        I.E. "udp:69", "tcp:52000-52400"
        :param logger:
        :param ns_name:
        :param veth_mac_addr:
        :param veth_root_ns_link_name:
        :param veth_priv_ns_link_name:
        :param temp_redir_dst:
        """
        super().__init__()
        self._target_port_ranges = target_port_ranges
        if logger is None:
            logger = logging.getLogger()
        self.ns_name = ns_name
        self._logger = logger
        self._temp_redir_dst = temp_redir_dst
        self._veth_priv_ns_link_name = veth_priv_ns_link_name
        self._veth_root_ns_link_name = veth_root_ns_link_name
        self._veth_mac_addr = veth_mac_addr

    def _cleanup_fakehost(self):
        self._logger.info("Pre-clean of passthrough redirect")
        cmds = [
            f"ip link del {self._veth_priv_ns_link_name}",
            f"ip link del {self._veth_root_ns_link_name}",
            f"ip netns del {self.ns_name}",
            f"umount /run/netns/{self.ns_name}",
            f"rm -f /run/netns/{self.ns_name} /var/run/netns/{self.ns_name}",
        ]

        for cmd in cmds:
            vrnetlab.run_command(cmd.split())

    def prep(self):
        self._cleanup_fakehost()
        self._logger.info("Installing Mgmt Passthrough network redirect towards container")
        # In management pass-through mode the container runs a tftp server in a dedicated namepace.
        # This namespace will use the IPv4 default gateway of the container as interface
        # tc flower rules will intercept tftp traffic and redirect it to this namespace
        # create namespace

        vrnetlab.run_command(f"ip netns add {self.ns_name}".split())
        # create vethts: FA in fakehost ns, RA in "root" ns
        vrnetlab.run_command(
            f"ip link add {self._veth_priv_ns_link_name} type veth peer name {self._veth_root_ns_link_name}".split())
        # assign FA veth to ns
        vrnetlab.run_command(f"ip link set {self._veth_priv_ns_link_name} netns {self.ns_name}".split())
        # enable veth root ns
        vrnetlab.run_command(f"ip link set {self._veth_root_ns_link_name} up".split())
        # enable loop in ns
        vrnetlab.run_command(f"ip netns exec {self.ns_name} ip link set dev lo up".split())
        # enable veth in fakehost ns
        vrnetlab.run_command(f"ip netns exec {self.ns_name} ip link set {self._veth_priv_ns_link_name} up".split())
        # assign a dummy mac that will not collide with the real docker bridge mac address
        vrnetlab.run_command(
            f"ip netns exec {self.ns_name} ip link set dev {self._veth_priv_ns_link_name} address {DEFAULT_VETH_MAC_ADDR}".split()
        )
        # configure a temporary ip address so the tftp server can start.
        # modified later in the startup process in the create_tc_tap_mgmt_ifup function
        vrnetlab.run_command(
            f"ip netns exec {self.ns_name} ip addr add {self._temp_redir_dst} dev {self._veth_priv_ns_link_name}".split()
        )
        # block arp responses in fakehost namespace so it doesn't interfere with root namespace
        vrnetlab.run_command(
            f"ip netns exec {self.ns_name} sysctl -w net.ipv4.conf.all.arp_ignore=8".split()
        )

    def before_gen_mgmt(self, vm):
        self.write_ifup_script(vm)

    def gen_mgmt_netdev(self, vm):
        return "tap,ifname=tap0,id=p00,script=/etc/tc-tap-mgmt-ifup,downscript=no"

    def write_ifup_script(self, vm):
        mgmt_ip_v4_address, mgmt_ip_v4_prefixlen = vm.mgmt_address_ipv4.split("/")
        ifup_script = self.get_tc_tap_mgmt_ifup(
            vm.mgmt_gw_ipv4,
            mgmt_ip_v4_prefixlen,
            mgmt_ip_v4_address,
            vm.mgmt_mac,
        )

        with open("/etc/tc-tap-mgmt-ifup", "w") as f:
            f.write(ifup_script)
        os.chmod("/etc/tc-tap-mgmt-ifup", 0o777)

    def get_tc_tap_mgmt_ifup(self,
                             redir_addr,
                             redir_prefix_len,
                             src_addr,
                             src_mac):

        # override the parent's function with sros requirements
        # this is used when using pass-through mode for mgmt connectivity
        """Create tap ifup script that is used in tc datapath mode, specifically for the management interface"""
        ifup_script = """#!/bin/bash

        ip link set tap0 up
        ip link set tap0 mtu 65000

        # disable IPv6 to avoid sending periodic traffic like router solicitations from the vrnetlab container
        ip -6 addr flush tap0
        
        # create tc eth<->tap redirect rules

        tc qdisc add dev eth0 clsact
        
        # exception for TCP ports 5000-5007
        tc filter add dev eth0 ingress prio 1 protocol ip flower ip_proto tcp dst_port 5000-5007 action pass
        
        # mirror ARP traffic to container
        tc filter add dev eth0 ingress prio 2 protocol arp flower action mirred egress mirror dev tap0
        # redirect rest of ingress traffic of eth0 to egress of tap0
        tc filter add dev eth0 ingress prio 3 flower action mirred egress redirect dev tap0

        tc qdisc add dev tap0 clsact
        # redirect all ingress traffic of tap0 to egress of eth0
        tc filter add dev tap0 ingress flower action mirred egress redirect dev eth0

        # clone management MAC of the VM
        ip link set dev eth0 address {SRC_MAC}
     
        tc qdisc add dev {RA} clsact
        
        # configure the ip address of the namespace as it was the host and remove the temporary one
        ip netns exec {NS_NAME} ip addr add {REDIR_ADDR}/{REDIR_PREFIX_LEN} dev {FA}
        ip netns exec {NS_NAME} ip addr del {TEMP_REDIR_DST} dev {FA}

        """
        prio = 1
        for entry in self._target_port_ranges:
            proto, port = entry.split(":")
            ifup_script += ("""
            # Redirect traffic from VM to private NS
            tc filter add dev tap0 ingress protocol ip prio {PRIO} \
                flower ip_proto {PROTO} dst_port {PORT} dst_ip {REDIR_ADDR} \
                action pedit ex munge eth dst set {PRIV_NS_VETH_MAC_ADDR} pipe \
                action mirred egress redirect dev {RA}

            """
                            .replace("{PROTO}", proto)
                            .replace("{PORT}", port)
                            .replace("{PRIO}", str(prio)))
            prio += 1

        for entry in self._target_port_ranges:
            proto, port = entry.split(":")
            ifup_script += ("""
            # Redirect traffic from private NS to VM
            tc filter add dev {RA} ingress protocol ip prio {PRIO} \
                flower ip_proto {PROTO} src_port {PORT} dst_ip {SRC_ADDR} 	\
                action pedit ex munge eth dst set {SRC_MAC} pipe \
                action mirred egress redirect dev tap0

            """
                            .replace("{PROTO}", proto)
                            .replace("{PORT}", port)
                            .replace("{PRIO}", str(prio)))
            prio += 1

        # FA, RA, TEMP_REDIR_DST
        ifup_script = ifup_script.replace("{RA}", self._veth_root_ns_link_name)
        ifup_script = ifup_script.replace("{FA}", self._veth_priv_ns_link_name)
        ifup_script = ifup_script.replace("{NS_NAME}", self.ns_name)
        ifup_script = ifup_script.replace("{TEMP_REDIR_DST}", self._temp_redir_dst)
        ifup_script = ifup_script.replace("{SRC_MAC}", src_mac)
        ifup_script = ifup_script.replace(
            "{PRIV_NS_VETH_MAC_ADDR}", self._veth_mac_addr
        )
        ifup_script = ifup_script.replace("{REDIR_ADDR}", redir_addr)
        ifup_script = ifup_script.replace("{REDIR_PREFIX_LEN}", redir_prefix_len)
        ifup_script = ifup_script.replace("{SRC_ADDR}", src_addr)
        self._logger.info(f"Traffic towards {redir_addr} on ports: [] redirected towards {self.ns_name} mac: "
                          f"{self._veth_mac_addr} and return traffic directed to IP {src_addr} with MAC: {src_mac}")
        return ifup_script
