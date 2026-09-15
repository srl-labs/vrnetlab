import logging

import vrnetlab
from net_mgmt_strategy import NetMgmtStrategy

BRIDGE_V4_ADDR = "172.31.255.29"
MGMT_V4_ADDR = "172.31.255.30"
V4_PREFIX_LENGTH = "30"
BRIDGE_V6_ADDR = "200::"
MGMT_V6_ADDR = "200::1"
V6_PREFIX_LENGTH = "127"


class HostForwardedBridge(NetMgmtStrategy):
    mgmt_passthrough = False

    def configure_vm_mgmt(self, vm):
        vm.mgmt_passthrough = False
        vm.mgmt_address_ipv4 = f"{MGMT_V4_ADDR}/{V4_PREFIX_LENGTH}"
        vm.mgmt_gw_ipv4 = BRIDGE_V4_ADDR
        vm.mgmt_address_ipv6 = f"{MGMT_V6_ADDR}/{V6_PREFIX_LENGTH}"
        vm.mgmt_gw_ipv6 = BRIDGE_V6_ADDR

    def gen_mgmt_netdev(self, vm):
        return "bridge,br=br-mgmt,id=p00"

    def prep(self):
        vrnetlab.run_command(["pkill", "socat"])

        # redirecting incoming tcp traffic (except serial port 5000) from eth0 to management interface
        vrnetlab.run_command(
            f"iptables-nft -t nat -A PREROUTING -i eth0 -p tcp ! --dport 5000 -j DNAT --to-destination {MGMT_V4_ADDR}".split()
        )
        vrnetlab.run_command(
            f"ip6tables-nft -t nat -A PREROUTING -i eth0 -p tcp ! --dport 5000 -j DNAT --to-destination {MGMT_V6_ADDR}".split()
        )
        # same redirection but for UDP
        vrnetlab.run_command(
            f"iptables-nft -t nat -A PREROUTING -i eth0 -p udp -j DNAT --to-destination {MGMT_V4_ADDR}".split()
        )
        vrnetlab.run_command(
            f"ip6tables-nft -t nat -A PREROUTING -i eth0 -p udp -j DNAT --to-destination {MGMT_V6_ADDR}".split()
        )
        # masquerading the incoming traffic so SR OS is able to reply back
        vrnetlab.run_command(
            "iptables-nft -t nat -A POSTROUTING -o br-mgmt -j MASQUERADE".split()
        )
        vrnetlab.run_command(
            "ip6tables-nft -t nat -A POSTROUTING -o br-mgmt -j MASQUERADE".split()
        )
        # allow sros breakout to management network by NATing via eth0
        vrnetlab.run_command(
            "iptables-nft -t nat -A POSTROUTING -o eth0 -j MASQUERADE".split()
        )
        vrnetlab.run_command(
            "ip6tables-nft -t nat -A POSTROUTING -o eth0 -j MASQUERADE".split()
        )

        # =====================================================
        # set up bridge for management interface to a localhost
        logger = logging.getLogger()
        logger.info("Creating br-mgmt bridge for management interface")
        # This is to whitlist all bridges
        vrnetlab.run_command(["mkdir", "-p", "/etc/qemu"])
        vrnetlab.run_command(["echo 'allow all' > /etc/qemu/bridge.conf"], shell=True)
        # Enable IPv6 inside the container
        vrnetlab.run_command(["sysctl net.ipv6.conf.all.disable_ipv6=0"], shell=True)
        # Enable IPv6 routing inside the container
        vrnetlab.run_command(["sysctl net.ipv6.conf.all.forwarding=1"], shell=True)
        vrnetlab.run_command(["brctl", "addbr", "br-mgmt"])
        vrnetlab.run_command(
            ["echo 16384 > /sys/class/net/br-mgmt/bridge/group_fwd_mask"],
            shell=True,
        )
        vrnetlab.run_command(["ip", "link", "set", "br-mgmt", "up"])
        vrnetlab.run_command(
            [
                "ip",
                "addr",
                "add",
                "dev",
                "br-mgmt",
                f"{BRIDGE_V4_ADDR}/{V4_PREFIX_LENGTH}",
            ]
        )
        vrnetlab.run_command(
            [
                "ip",
                "addr",
                "add",
                "dev",
                "br-mgmt",
                f"{BRIDGE_V6_ADDR}/{V6_PREFIX_LENGTH}",
            ]
        )
