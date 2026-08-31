#!/usr/bin/env python3

import datetime
import ipaddress
import logging
import os
import re
import shutil
import signal
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
    # Yes, logger takes its '*args' as 'args'.
    if self.isEnabledFor(TRACE_LEVEL_NUM):
        self._log(TRACE_LEVEL_NUM, message, args, **kws)


logging.Logger.trace = trace


class OpenWRT_vm(vrnetlab.VM):
    def __init__(
        self,
        username,
        password,
        conn_mode,
        nics,
        hostname,
        packet_repository_domains,
        packet_repository_dns_server,
        packages,
        lan_ip,
        lan_netmask,
    ):
        for e in os.listdir("/"):
            if re.search(".img$", e):
                disk_image = "/" + e
        super(OpenWRT_vm, self).__init__(
            username, password, disk_image=disk_image, ram=128
        )
        self.nic_type = "virtio-net-pci"
        self.num_nics = nics
        self.conn_mode = conn_mode
        self.hostname = hostname
        self.lan_ip = lan_ip
        self.lan_netmask = lan_netmask
        self.lan_interface_device = "eth2"
        self.mgmt_interface_device = "eth0"
        self.mgmt_passthrough_ipv4_address = self.get_mgmt_address()[0]
        self.mgmt_passthrough_ipv4_gateway = self.get_mgmt_gw()[0]
        self.mgmt_interface_interface = "mgmt"
        self.packet_repository_domains = packet_repository_domains
        self.packet_repository_dns_server = packet_repository_dns_server
        self.packages = packages

    def vm_stop_start_rm_tc_rules(self):
        import subprocess

        self.stop()
        self.logger.info("Clearing all existing tc rules before stopping the VM.")

        # List all interfaces
        interfaces = [
            iface
            for iface in os.listdir("/sys/class/net/")
            if iface.startswith("eth") or iface.startswith("tap")
        ]

        # Remove all tc rules for each interface
        for iface in interfaces:
            self.logger.info(f"Flushing tc rules for {iface}")
            try:
                subprocess.run(
                    ["/sbin/tc", "qdisc", "del", "dev", iface, "clsact"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                self.logger.info(f"Successfully flushed tc rules for {iface}")
            except subprocess.CalledProcessError as e:
                self.logger.error(f"Failed to flush tc rules for {iface}: {e.stderr}")

        self.start()

    def bootstrap_spin(self):
        """This function should be called periodically to do work."""

        # Define the overlay directory
        overlay_dir = "/overlay"

        # Ensure the overlay directory exists
        if not os.path.exists(overlay_dir):
            os.makedirs(overlay_dir, exist_ok=True)
            self.logger.info(f"Created overlay directory: {overlay_dir}")

        # Detect the base image dynamically
        base_image = None
        for filename in os.listdir("/"):
            if re.match(r"openwrt-.*-x86-64-generic-ext4-combined\.img$", filename):
                base_image = filename
                break  # Use the first matching image

        # Raise an error if no base image is found
        if not base_image:
            self.logger.error("No OpenWrt base image found!")
            return

        # Determine expected overlay image names
        overlay_img = f"/{base_image.replace('.img', '-overlay.img')}"
        overlay_target = os.path.join(overlay_dir, os.path.basename(overlay_img))

        # Check if an overlay image already exists in the overlay directory
        if os.path.exists(overlay_target):
            self.logger.info(f"Overlay image already exists: {overlay_target}")

            # Verify that the overlay image corresponds to the correct base version
            base_version = os.path.basename(base_image).split("-")[1]  # Extract version
            overlay_version = os.path.basename(overlay_target).split("-")[
                1
            ]  # Extract overlay version

            if base_version == overlay_version:
                self.logger.info(
                    f"Overlay version matches base image ({base_version}), no update needed."
                )
            else:
                self.logger.warning(
                    f"Overlay image version mismatch: Base({base_version}) vs Overlay({overlay_version})"
                )

        else:
            # If no overlay image exists in overlay_dir but one is in /, move it
            if os.path.exists(overlay_img):
                shutil.move(overlay_img, overlay_target)
                self.logger.info(
                    f"Overlay image moved: {overlay_img} → {overlay_target}"
                )
                if os.path.exists(overlay_img) and not os.path.islink(overlay_img):
                    self.logger.info(
                        f"Removing existing file at {overlay_img} before creating symlink."
                    )
                    os.remove(overlay_img)
                # Ensure the symlink is created only if necessary
                if not os.path.islink(overlay_img):
                    self.logger.info(
                        f"Symlink does not exist, creating: {overlay_img} → {overlay_target}"
                    )
                    os.symlink(overlay_target, overlay_img)
                    self.vm_stop_start_rm_tc_rules()

                elif os.readlink(overlay_img) != overlay_target:
                    self.logger.info(
                        f"Symlink is incorrect, updating: {overlay_img} → {overlay_target}"
                    )
                    os.remove(overlay_img)
                    os.symlink(overlay_target, overlay_img)
                    self.vm_stop_start_rm_tc_rules()
                else:
                    self.logger.info("Symlink is already correct, no changes needed.")

        # Ensure the symlink can be created by removing existing files if necessary
        if os.path.exists(overlay_img) and not os.path.islink(overlay_img):
            self.logger.info(
                f"Removing existing file at {overlay_img} before creating symlink."
            )
            os.remove(overlay_img)

        # Ensure the symlink is created only if necessary
        if not os.path.islink(overlay_img):
            self.logger.info(
                f"Symlink does not exist, creating: {overlay_img} → {overlay_target}"
            )
            os.symlink(overlay_target, overlay_img)
            self.vm_stop_start_rm_tc_rules()

        elif os.readlink(overlay_img) != overlay_target:
            self.logger.info(
                f"Symlink is incorrect, updating: {overlay_img} → {overlay_target}"
            )
            os.remove(overlay_img)
            os.symlink(overlay_target, overlay_img)
            self.vm_stop_start_rm_tc_rules()
        else:
            self.logger.info("Symlink is already correct, no changes needed.")

            if self.spins > 300:
                # too many spins with no result ->  give up
                self.vm_stop_start_rm_tc_rules()
                return

        (ridx, match, res) = self.tn.expect([b"br-lan"], 1)
        if match:  # got a match!
            if ridx == 0:  # login
                self.logger.debug("VM started")
                # run main config!
                self.bootstrap_config()
                # close telnet connection
                self.tn.close()
                # startup time?
                startup_time = datetime.datetime.now() - self.start_time
                self.logger.info("Startup complete in: %s" % startup_time)
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

    def get_network_config(self):
        """Retrieve the current network configuration from OpenWrt"""
        self.tn.write(b"cat /etc/config/network\n")
        time.sleep(1)
        output = self.tn.read_very_eager().decode("utf-8").strip()
        return output

    def get_ready(self):
        self.tn.write(b"\r\n")
        self.tn.write(b"\x03")
        self.tn.write(b"echo READY\n")
        self.tn.read_until(b"READY", timeout=10)
        self.tn.read_until(b"#", timeout=5)

    def configure_mgmt_interface(self, output):
        """Check and configure the mgmt interface if needed"""
        self.get_ready()
        changes_network = 0
        expected_mgmt_address_ipv4 = str(
            ipaddress.IPv4Interface(self.mgmt_address_ipv4).ip
        )

        if "config interface 'mgmt'" not in output:
            self.logger.info("❌ MGMT Interface not found, creating it...")

            self.tn.write(b"uci set network.mgmt=interface\n")
            time.sleep(0.5)
            self.tn.write(
                f"uci set network.mgmt.device='{self.mgmt_interface_device}'\n".encode(
                    "utf-8"
                )
            )
            time.sleep(0.5)
            self.tn.write(b"uci set network.mgmt.proto='static'\n")
            time.sleep(0.5)
            self.tn.write(
                f"uci set network.mgmt.ipaddr='{ipaddress.IPv4Interface(self.mgmt_address_ipv4).ip}'\n".encode(
                    "utf-8"
                )
            )
            time.sleep(0.5)
            self.tn.write(
                f"uci set network.mgmt.netmask='{ipaddress.IPv4Interface(self.mgmt_address_ipv4).netmask}'\n".encode()
            )
            time.sleep(0.5)
            if (
                self.mgmt_passthrough_ipv4_address == self.mgmt_address_ipv4
                and self.mgmt_address_ipv6
            ):
                # MGMT passthrough active, lets enable IPv6 aswell
                self.tn.write(
                    f"uci set network.mgmt.ip6addr='{self.mgmt_address_ipv6}'\n".encode(
                        "utf-8"
                    )
                )
                time.sleep(0.5)
                self.tn.write(b"uci set network.mgmt.delegate='0'\n")
                time.sleep(0.5)
            # Commit changes
            self.tn.write(b"uci commit network\n")
            time.sleep(1)
            self.tn.write(b"echo READY\n")
            self.tn.read_until(b"READY", timeout=10)
            self.tn.read_until(b"#", timeout=5)
            self.logger.info("✅ New MGMT interface created.")
            changes_network = 1
        else:
            # MGMT interface exists, check the current IP address
            self.logger.info("🔍 Checking current MGMT IP configuration...")
            self.tn.write(b"uci get network.mgmt.ipaddr\n")
            time.sleep(0.5)
            current_mgmt_address_ipv4 = (
                self.tn.read_very_eager().decode("utf-8").strip()
            )
            ip_pattern = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
            match = ip_pattern.search(current_mgmt_address_ipv4)
            if match:
                current_mgmt_address_ipv4 = match.group()
            current_mgmt_address_ipv4 = current_mgmt_address_ipv4.split("\n")[-1]
            if current_mgmt_address_ipv4 == expected_mgmt_address_ipv4:
                self.logger.info(
                    f"✅ MGMT IP is already correct: {current_mgmt_address_ipv4}"
                )
            else:
                if (
                    self.mgmt_passthrough_ipv4_address != self.mgmt_address_ipv4
                    or not self.mgmt_address_ipv6
                ):
                    # Disable IPv6 when passthrough is inactive or has no IPv6 address
                    self.tn.write(b"uci del network.mgmt.ip6addr\n")
                    time.sleep(0.5)
                self.logger.warning(
                    f"⚠ MGMT IP mismatch! Current: {current_mgmt_address_ipv4}, Expected: {expected_mgmt_address_ipv4}"
                )
                self.logger.info("🔄 Updating MGMT IP address...")

                self.tn.write(
                    f"uci set network.mgmt.ipaddr='{expected_mgmt_address_ipv4}'\n".encode(
                        "utf-8"
                    )
                )
                time.sleep(0.5)
                if (
                    self.mgmt_passthrough_ipv4_address == self.mgmt_address_ipv4
                    and self.mgmt_address_ipv6
                ):
                    # MGMT passthrough active, lets enable IPv6 aswell
                    self.tn.write(
                        f"uci set network.mgmt.ip6addr='{self.mgmt_address_ipv6}'\n".encode(
                            "utf-8"
                        )
                    )
                    time.sleep(0.5)
                    self.tn.write(b"uci set network.mgmt.delegate='0'\n")
                    time.sleep(0.5)
                self.tn.write(b"uci commit network\n")
                time.sleep(1)
                self.tn.write(b"echo READY\n")
                self.tn.read_until(b"READY", timeout=10)
                self.tn.read_until(b"#", timeout=5)
                self.logger.info(f"✅ MGMT IP updated to {expected_mgmt_address_ipv4}.")
                changes_network = 1

        return changes_network

    def configure_firewall_zone(self):
        """Ensure the mgmt firewall zone exists"""
        self.get_ready()
        changes_firewall = 0

        self.tn.write(b"cat /etc/config/firewall\n")
        time.sleep(1)
        firewall_output = self.tn.read_very_eager().decode("utf-8").strip()

        if "option name 'mgmt'" not in firewall_output:
            self.logger.info("❌ Firewall zone `mgmt` is missing, creating it...")
            self.tn.write(b"uci add firewall zone\n")
            time.sleep(0.5)
            self.tn.write(b"uci set firewall.@zone[-1].name='mgmt'\n")
            time.sleep(0.5)
            self.tn.write(b"uci set firewall.@zone[-1].input='ACCEPT'\n")
            time.sleep(0.5)
            self.tn.write(b"uci set firewall.@zone[-1].output='ACCEPT'\n")
            time.sleep(0.5)
            self.tn.write(b"uci set firewall.@zone[-1].forward='ACCEPT'\n")
            time.sleep(0.5)
            self.tn.write(b"uci add_list firewall.@zone[-1].network='mgmt'\n")
            time.sleep(0.5)
            self.tn.write(b"uci commit firewall\n")
            time.sleep(1)
            self.logger.info("✅ Firewall zone `mgmt` created!")
            changes_firewall = 1

        return changes_firewall

    def configure_route(self, output):
        """Ensure the mgmt route exists"""
        self.get_ready()
        changes_network = 0
        if "config route" not in output or (
            "option comment 'passthrough_on'" not in output
            and "option comment 'passthrough_off'" not in output
        ):
            self.logger.info("❌ Route `mgmt` is missing, creating it...")
            self.logger.info(
                f"Route option target '{ipaddress.IPv4Interface(self.mgmt_address_ipv4).network}'"
            )
            self.tn.write(b"uci add network route\n")
            time.sleep(0.5)
            self.tn.write(
                f"uci set network.@route[-1].interface='{self.mgmt_interface_interface}'\n".encode(
                    "utf-8"
                )
            )
            time.sleep(0.5)

            if self.mgmt_passthrough_ipv4_address == self.mgmt_address_ipv4:
                self.tn.write(
                    f"uci set network.@route[-1].target='{ipaddress.IPv4Interface(self.mgmt_address_ipv4).network}'\n".encode(
                        "utf-8"
                    )
                )
                time.sleep(0.5)
                self.tn.write(
                    f"uci set network.@route[-1].gateway='{self.mgmt_passthrough_ipv4_gateway}'\n".encode(
                        "utf-8"
                    )
                )
                time.sleep(0.5)
                self.tn.write(b"uci set network.@route[-1].comment='passthrough_on'\n")
                time.sleep(0.5)
            else:
                self.tn.write(
                    f"uci set network.@route[-1].target='{ipaddress.IPv4Interface(self.mgmt_passthrough_ipv4_address).network}'\n".encode(
                        "utf-8"
                    )
                )
                time.sleep(0.5)
                self.tn.write(
                    f"uci set network.@route[-1].gateway='{self.mgmt_gw_ipv4}'\n".encode(
                        "utf-8"
                    )
                )
                time.sleep(0.5)
                self.tn.write(b"uci set network.@route[-1].comment='passthrough_off'\n")
                time.sleep(0.5)

            self.tn.write(b"uci commit network\n")
            time.sleep(1)
            self.logger.info("✅ Route `mgmt` created!")
            changes_network = 1

        # Get all routes with comments containing 'passthrough_on' or 'passthrough_off'
        self.tn.write(b'uci show network | grep "comment=\'passthrough_"\n')
        time.sleep(0.5)
        output = self.tn.read_very_eager().decode("utf-8").strip()

        # Iterate over each found route
        for line in output.splitlines():
            match = re.search(
                r"network.@route\[(\d+)\].comment='(passthrough_on|passthrough_off)'",
                line,
            )
            if match:
                route_index = match.group(1)
                passthrough_status = match.group(2)

                if passthrough_status == "passthrough_on":
                    if self.mgmt_passthrough_ipv4_address != self.mgmt_address_ipv4:
                        self.logger.info("Switching MGMT route to passthrough_off")
                        self.tn.write(
                            f"uci set network.@route[{route_index}].target='{ipaddress.IPv4Interface(self.mgmt_passthrough_ipv4_address).network}'\n".encode(
                                "utf-8"
                            )
                        )
                        time.sleep(0.5)
                        self.tn.write(
                            f"uci set network.@route[{route_index}].gateway='{self.mgmt_gw_ipv4}'\n".encode(
                                "utf-8"
                            )
                        )
                        time.sleep(0.5)
                        self.tn.write(
                            f"uci set network.@route[{route_index}].comment='passthrough_off'\n".encode(
                                "utf-8"
                            )
                        )
                        time.sleep(0.5)
                        self.tn.write(b"uci commit network\n")
                        time.sleep(0.5)
                        changes_network = 1
                elif passthrough_status == "passthrough_off":
                    if self.mgmt_passthrough_ipv4_address == self.mgmt_address_ipv4:
                        self.logger.info("Switching MGMT route to passthrough_on")
                        self.tn.write(
                            f"uci set network.@route[{route_index}].target='{ipaddress.IPv4Interface(self.mgmt_address_ipv4).network}'\n".encode(
                                "utf-8"
                            )
                        )
                        time.sleep(0.5)
                        self.tn.write(
                            f"uci set network.@route[{route_index}].gateway='{self.mgmt_passthrough_ipv4_gateway}'\n".encode(
                                "utf-8"
                            )
                        )
                        time.sleep(0.5)
                        self.tn.write(
                            f"uci set network.@route[{route_index}].comment='passthrough_on'\n".encode(
                                "utf-8"
                            )
                        )
                        time.sleep(0.5)
                        self.tn.write(b"uci commit network\n")
                        time.sleep(0.5)
                        changes_network = 1

        return changes_network

    def check_br_lan_ports(self):
        """Check and update br-lan ports"""
        self.get_ready()
        self.logger.info("\n[🔎] Checking br-lan ports...")

        changes_network = 0  # Track if network configuration has changed
        # Retrieve the network configuration
        self.tn.write(b"cat /etc/config/network\r\n")
        time.sleep(1)
        output = self.tn.read_very_eager().decode("utf-8").strip()
        # debug
        # self.logger.debug("Received network2 configuration:\n%s", output)
        # Search for the br-lan configuration block
        brlan_match = re.search(
            r"(config device[\s\S]+?option name 'br-lan'[\s\S]+?)(?=config|\Z)", output
        )

        if brlan_match:
            brlan_block = brlan_match.group(1)

            eth0_in_brlan = f"list ports '{self.mgmt_interface_device}'" in brlan_block
            eth2_in_brlan = f"list ports '{self.lan_interface_device}'" in brlan_block

            # Modify br-lan ports if needed
            if eth0_in_brlan or not eth2_in_brlan:
                self.logger.info("🔄 Changes needed for br-lan...")

                # Remove eth0 from br-lan if present
                if eth0_in_brlan:
                    self.logger.info("❌ Removing `eth0` from `br-lan`...")
                    self.tn.write(
                        f"uci del_list network.@device[-1].ports='{self.mgmt_interface_device}'\r\n".encode(
                            "utf-8"
                        )
                    )
                    time.sleep(0.5)
                    changes_network = 1  # Mark network as changed

                # Add eth2 to br-lan if not present
                if not eth2_in_brlan:
                    self.logger.info("✅ Adding `eth2` to `br-lan`...")
                    self.tn.write(
                        f"uci add_list network.@device[-1].ports='{self.lan_interface_device}'\r\n".encode(
                            "utf-8"
                        )
                    )
                    time.sleep(0.5)
                    changes_network = 1  # Mark network as changed

                # Commit the changes if any modifications were made
                if changes_network:
                    self.tn.write(b"uci commit network\r\n")
                    time.sleep(1)

                    # Ensure OpenWrt is ready after commit
                    self.tn.write(b"echo READY\r\n")
                    self.tn.read_until(b"READY", timeout=5)
                    self.tn.read_until(b"#", timeout=5)

                    self.logger.info("✅ br-lan updated!")

            else:
                self.logger.info("✅ No changes required for br-lan.")

        else:
            self.logger.error("❌ br-lan device not found in /etc/config/network!")

        return changes_network  # Return whether a change was made

    def reload_services(self, changes_network, changes_firewall):
        """Restart and reload network and firewall services if changes were made"""
        self.get_ready()
        if changes_network:
            self.logger.info("\n[🔄] Reloading network configuration...")
            self.tn.write(b"/etc/init.d/network restart\n")
            time.sleep(3)
            self.logger.info("✅ Network restarted!")

        if changes_firewall:
            self.logger.info("\n[🔄] Reloading firewall configuration...")
            self.tn.write(b"/etc/init.d/firewall reload\n")
            time.sleep(3)
            self.logger.info("✅ Firewall reloaded!")

    def routes_and_dns(self):
        # Target domains & DNS server
        domains = (
            "downloads.openwrt.org"  # Multiple domains separated by space or comma
        )
        if self.packet_repository_domains:
            domains = f"{domains} {self.packet_repository_domains}"

        # Convert domain string into a list
        domain_list = re.split(r"[ ,]+", domains.strip())

        # Add a temporary route to the DNS server
        self.logger.info(
            f"➕ Adding temporary route to {self.packet_repository_dns_server}..."
        )
        self.tn.write(b"uci add network route\r\n")
        time.sleep(0.5)
        self.tn.write(
            f"uci set network.@route[-1].interface='{self.mgmt_interface_interface}'\r\n".encode(
                "utf-8"
            )
        )
        time.sleep(0.5)
        self.tn.write(
            f"uci set network.@route[-1].target='{self.packet_repository_dns_server}/32'\r\n".encode(
                "utf-8"
            )
        )
        time.sleep(0.5)
        if self.mgmt_passthrough_ipv4_address == self.mgmt_address_ipv4:
            self.tn.write(
                f"uci set network.@route[-1].gateway='{self.mgmt_passthrough_ipv4_gateway}'\r\n".encode(
                    "utf-8"
                )
            )
            time.sleep(0.5)
        else:
            self.tn.write(
                f"uci set network.@route[-1].gateway='{self.mgmt_gw_ipv4}'\r\n".encode(
                    "utf-8"
                )
            )
            time.sleep(0.5)
        self.tn.write(b"uci set network.@route[-1].comment='dns'\n")
        time.sleep(0.5)
        self.tn.write(b"uci commit network\r\n")
        time.sleep(1)
        self.tn.write(b"/etc/init.d/network restart\r\n")
        time.sleep(3)
        self.logger.info("✅ Temporary route to DNS server added!")

        # Storage for resolved IPs
        ipv4_addresses = {}

        # Resolve each domain
        for domain in domain_list:
            self.logger.info(
                f"[🔍] Resolving {domain} using {self.packet_repository_dns_server} via Telnet..."
            )
            self.tn.write(
                f"nslookup {domain} {self.packet_repository_dns_server}\r\n".encode(
                    "utf-8"
                )
            )
            time.sleep(2)  # Wait for response
            nslookup_output = self.tn.read_very_eager().decode("utf-8")

            # Extract IPv4 addresses
            resolved_ips = set(
                re.findall(r"Address:\s+(\d+\.\d+\.\d+\.\d+)", nslookup_output)
            )  # Remove duplicates
            resolved_ips.discard(
                self.packet_repository_dns_server
            )  # Remove the nameserver itself

            if not resolved_ips:
                self.logger.info(f"❌ No IPv4 addresses found for {domain}.")
            else:
                self.logger.info(
                    f"✅ Found IPv4 addresses for {domain}: {', '.join(resolved_ips)}"
                )
                ipv4_addresses[domain] = resolved_ips

        # Check current `/etc/hosts` and network routes
        self.tn.write(b"cat /etc/hosts\r\n")
        time.sleep(1)
        hosts_output = self.tn.read_very_eager().decode("utf-8")

        self.tn.write(b"cat /etc/config/network\r\n")
        time.sleep(1)
        network_output = self.tn.read_very_eager().decode("utf-8")

        # Change tracking
        changes_hosts = 0
        changes_routes = 0

        # Remove domains from /etc/hosts if domain not in domain_list, but skip localhost
        existing_hosts = re.findall(
            r"(\d+\.\d+\.\d+\.\d+)\s+(\S+)\s+#\s+opkg", hosts_output
        )
        for ip, domain in existing_hosts:
            if ip == "127.0.0.1" and domain == "localhost":
                continue
            if domain not in domain_list or ip not in ipv4_addresses.get(domain, []):
                self.logger.info(
                    f"❌ Removing {ip} from /etc/hosts (domain {domain} no longer in domain_list or IP changed)..."
                )
                self.tn.write(
                    f"sed -i '/{ip} {domain} # opkg/d' /etc/hosts\r\n".encode("utf-8")
                )
                time.sleep(0.5)
                changes_hosts = 1

        # Remove outdated routes
        self.logger.info("\n🔍 Checking for outdated routes...")

        # Get all configured routes (ensuring comments and targets are captured correctly)
        self.tn.write(b"uci show network | grep route\r\n")
        time.sleep(1)
        network_routes = self.tn.read_very_eager().decode("utf-8").strip().split("\n")

        # Dictionary to store existing routes
        existing_routes = {}

        # Extract existing routes
        for line in network_routes:
            match_target = re.search(
                r"network.@route\[(\d+)\].target='(\d+\.\d+\.\d+\.\d+/32)'", line
            )
            match_comment = re.search(r"network.@route\[(\d+)\].comment='(.+)'", line)
            match_gateway = re.search(
                r"network.@route\[(\d+)\].gateway='(\d+\.\d+\.\d+\.\d+)'", line
            )

            if match_target:
                route_index, route_ip = match_target.groups()
                existing_routes[route_index] = {"ip": route_ip, "comment": ""}

            if match_comment:
                route_index, route_comment = match_comment.groups()
                if route_index in existing_routes:
                    existing_routes[route_index]["comment"] = route_comment

            if match_gateway:
                route_index, route_gateway = match_gateway.groups()
                if route_index in existing_routes:
                    existing_routes[route_index]["gateway"] = route_gateway

        # Create a dictionary with expected valid routes
        expected_routes = {}
        for domain, ips in ipv4_addresses.items():
            for ip in ips:
                expected_routes[ip + "/32"] = domain  # Store IP + comment (domain name)

        # List to store routes that should be deleted
        routes_to_delete = []

        # Remove routes if domain not in domain_list or IP changed
        for route_index, route_data in existing_routes.items():
            route_ip = route_data["ip"]
            route_comment = route_data["comment"]
            route_gateway = route_data["gateway"]

            # Skip DNS/passthrough routes
            if route_comment in ["dns", "passthrough_on", "passthrough_off"]:
                continue

            if self.mgmt_passthrough_ipv4_address == self.mgmt_address_ipv4:
                expected_gateway = self.mgmt_passthrough_ipv4_gateway
            else:
                expected_gateway = self.mgmt_gw_ipv4

            # Strip '-opkg' from the comment for comparison
            if "-opkg" in route_comment:
                stripped_comment = route_comment.replace("-opkg", "")
                if stripped_comment not in domain_list or route_ip not in [
                    ip + "/32" for ip in ipv4_addresses.get(stripped_comment, [])
                ]:
                    self.logger.info(
                        f"❌ Marking route {route_ip} (index {route_index}, comment: {route_comment}) "
                        "for removal (domain not in domain_list or IP changed)..."
                    )
                    routes_to_delete.append(int(route_index))
                if (
                    route_gateway != expected_gateway
                ) and stripped_comment in domain_list:
                    self.logger.info(
                        f"🔄 Updating gateway for route {route_ip} (index {route_index}, comment: {route_comment}) "
                        f"from {route_gateway} to {expected_gateway}..."
                    )
                    self.tn.write(
                        f"uci set network.@route[{route_index}].gateway='{expected_gateway}'\r\n".encode(
                            "utf-8"
                        )
                    )
                    time.sleep(0.5)
                    self.tn.write(b"uci commit network\r\n")
                    time.sleep(0.5)
                    changes_routes = 1

        # Sort routes in descending order and delete them
        routes_to_delete = sorted(
            set(routes_to_delete), reverse=True
        )  # unique + descending
        for route_index in routes_to_delete:
            self.logger.info(f"❌ Removing outdated route at index {route_index}...")
            self.tn.write(
                f"uci delete network.@route[{route_index}]\r\n".encode("utf-8")
            )
            time.sleep(0.5)
            self.tn.write(b"uci commit network\r\n")
            time.sleep(0.5)
            changes_routes = 1

        # Update `/etc/hosts` with missing entries
        for domain, ips in ipv4_addresses.items():
            for ip in ips:
                if f"{ip} {domain} # opkg" not in hosts_output:
                    self.logger.info(f"➕ Adding {ip} to /etc/hosts for {domain}...")
                    self.tn.write(
                        f"echo '{ip} {domain} # opkg' >> /etc/hosts\r\n".encode("utf-8")
                    )
                    time.sleep(0.5)
                    changes_hosts = 1

        # Add missing routes
        for domain, ips in ipv4_addresses.items():
            for ip in ips:
                if self.mgmt_passthrough_ipv4_address == self.mgmt_address_ipv4:
                    route_pattern = rf"config route[\s\S]+?option interface '{self.mgmt_interface_interface}'[\s\S]+?option target '{ip}/32'[\s\S]+?option gateway '{self.mgmt_passthrough_ipv4_gateway}'"
                else:
                    route_pattern = rf"config route[\s\S]+?option interface '{self.mgmt_interface_interface}'[\s\S]+?option target '{ip}/32'[\s\S]+?option gateway '{self.mgmt_gw_ipv4}'"
                route_exists = re.search(route_pattern, network_output) is not None

                if not route_exists:
                    self.logger.info(f"➕ Adding route {ip}/32 for {domain}...")
                    self.tn.write(b"uci add network route\r\n")
                    time.sleep(0.5)
                    self.tn.write(
                        f"uci set network.@route[-1].interface='{self.mgmt_interface_interface}'\r\n".encode(
                            "utf-8"
                        )
                    )
                    time.sleep(0.5)
                    self.tn.write(
                        f"uci set network.@route[-1].target='{ip}/32'\r\n".encode(
                            "utf-8"
                        )
                    )
                    time.sleep(0.5)
                    if self.mgmt_passthrough_ipv4_address == self.mgmt_address_ipv4:
                        self.tn.write(
                            f"uci set network.@route[-1].gateway='{self.mgmt_passthrough_ipv4_gateway}'\r\n".encode(
                                "utf-8"
                            )
                        )
                        time.sleep(0.5)
                    else:
                        self.tn.write(
                            f"uci set network.@route[-1].gateway='{self.mgmt_gw_ipv4}'\r\n".encode(
                                "utf-8"
                            )
                        )
                        time.sleep(0.5)
                    self.tn.write(
                        f"uci set network.@route[-1].comment='{domain}-opkg'\n".encode(
                            "utf-8"
                        )
                    )
                    time.sleep(0.5)
                    self.tn.write(b"uci commit network\r\n")
                    time.sleep(0.5)
                    changes_routes = 1

        # Remove temporary DNS server route
        self.logger.info(
            f"❌ Searching for the temporary DNS route to {self.packet_repository_dns_server}..."
        )

        # Retrieve the DNS route index using `uci show network`
        self.tn.write(b"uci show network | grep route | grep dns\r\n")
        time.sleep(1)
        route_output = self.tn.read_very_eager().decode("utf-8").strip()

        # Extract the route index using regex
        match = re.search(r"network.@route\[(\d+)\].comment='dns'", route_output)

        if match:
            route_index = match.group(1)  # Extract the route index
            self.logger.info(
                f"✅ Found DNS route at index {route_index}, removing it..."
            )

            # Delete the specific route by index
            self.tn.write(
                f"uci delete network.@route[{route_index}]\r\n".encode("utf-8")
            )
            time.sleep(0.5)
            self.tn.write(b"uci commit network\r\n")
            time.sleep(1)
            changes_routes = 1
            self.logger.info("✅ Temporary DNS route removed!")
        else:
            self.logger.info("❌ No temporary DNS route found!")

        # Commit & restart if necessary
        if changes_routes:
            self.logger.info("\n[🔄] Reloading network configuration...")
            self.tn.write(b"/etc/init.d/network restart\r\n")
            time.sleep(3)
            self.logger.info("✅ Network restarted!")

        if changes_hosts:
            self.logger.info("\n[🔄] Restarting dnsmasq to apply /etc/hosts changes...")
            self.tn.write(b"/etc/init.d/dnsmasq restart\r\n")
            time.sleep(3)
            self.logger.info("✅ dnsmasq restarted")

        self.logger.info("\n✅ All routes_and_dns tasks completed!")

    def packet_update(self):
        # 1. Detect OpenWrt prompt dynamically
        self.tn.write(b"echo PROMPT_DETECT\r\n")
        time.sleep(1)
        prompt_output = self.tn.read_very_eager().decode("utf-8").strip()
        prompt_lines = [
            line.strip()
            for line in prompt_output.split("\n")
            if "PROMPT_DETECT" not in line and line.strip()
        ]

        # Extract the actual OpenWrt prompt (last non-empty line)
        prompt = prompt_lines[-1] if prompt_lines else "#"
        # self.logger.info(f"[🔍] Detected prompt: `{prompt}`")
        # 2. Run `opkg update`
        self.logger.info("\n[🔄] Running `opkg update`...")
        self.tn.write(b"opkg update\r\n")
        time.sleep(5)  # Wait for package lists to update
        self.tn.read_very_eager()  # Clear the buffer

        # 3. Retrieve list of upgradable packages
        self.logger.info("\n[🔍] Checking for upgradable packages...")
        self.tn.write(b"opkg list-upgradable\r\n")
        time.sleep(2)
        opkg_output = self.tn.read_very_eager().decode("utf-8").strip()

        # Extract package names for upgrading
        lines = [line.strip() for line in opkg_output.split("\n")]

        # 4. Remove unwanted lines
        filtered_packages = []
        for line in lines:
            if not line or line.startswith("opkg"):  # Ignore `opkg` itself
                continue
            if line.startswith(prompt):  # Ignore prompt lines
                continue
            package_name = line.split()[0]  # Extract first word (package name)
            filtered_packages.append(package_name)

        # 6. Remove duplicates & prepare package list
        upgradable_packages = list(set(filtered_packages))

        if not upgradable_packages:
            self.logger.info("✅ No packages need an upgrade.")
        else:
            self.logger.info(
                f"🔄 Upgrading {len(upgradable_packages)} packages: {', '.join(upgradable_packages)}"
            )

            # 6. Upgrade packages
            for package in upgradable_packages:
                self.logger.info(f"➕ Upgrading {package}...")
                self.tn.write(f"opkg upgrade {package}\r\n".encode("utf-8"))
                time.sleep(2)  # Wait briefly after each upgrade

            self.logger.info("\n✅ All packages updated successfully!")

    def packages_install(self):
        changes_network = 0
        # 1. Read package list from ENV variable
        if not self.packages:
            self.logger.info("❌ No packages specified in ENV variable `PACKAGES`.")
        # Split the package list (supports both spaces and commas)
        packages = [
            pkg.strip()
            for pkg in self.packages.replace(",", " ").split()
            if pkg.strip()
        ]
        if not packages:
            self.logger.info("❌ No valid packages found after parsing `PACKAGES`.")

        # 2. Run `opkg update` before installing packages
        self.logger.info("\n[🔄] Running `opkg update`...")
        self.tn.write(b"opkg update\r\n")
        time.sleep(5)  # Wait for package lists to update
        self.tn.read_very_eager()  # Clear the buffer

        # 3. Get list of installed packages
        self.logger.info("\n[🔍] Checking for already installed packages...")
        self.tn.write(b"opkg list-installed\r\n")
        time.sleep(2)
        installed_output = self.tn.read_very_eager().decode("utf-8").strip()
        # Extract installed package names
        installed_packages = {
            line.split()[0] for line in installed_output.split("\n") if line
        }

        # 4. Filter out already installed packages
        not_installed_packages = [
            pkg for pkg in packages if pkg not in installed_packages
        ]

        if not not_installed_packages:
            self.logger.info(
                "\n✅ All packages are already installed. No installation needed."
            )

        # 5. Check if remaining packages exist in the repository using `opkg find`
        valid_packages = []
        not_found_packages = []
        for package in not_installed_packages:
            self.logger.info(
                f"[🔎] Checking if `{package}` is available in the repository..."
            )
            self.tn.write(f"opkg find {package}\r\n".encode("utf-8"))
            time.sleep(1)
            package_found = self.tn.read_very_eager().decode("utf-8").strip()

            # Ensure that the package name appears at the beginning of a line
            if any(
                line.startswith(package + " - ") for line in package_found.split("\n")
            ):
                valid_packages.append(package)
            else:
                not_found_packages.append(package)

        # 6. Output clear and separate messages
        if not_found_packages:
            self.logger.info(
                f"\n❌ The following packages were NOT found in the repository and will NOT be installed: {', '.join(not_found_packages)}"
            )

        if not valid_packages:
            self.logger.info("\n✅ No valid packages to install.")

        self.logger.info(
            f"\n🔄 Installing {len(valid_packages)} packages: {', '.join(valid_packages)}"
        )

        # 7. Install only valid and missing packages
        for package in valid_packages:
            self.logger.info(f"➕ Installing {package}...")
            self.tn.write(f"opkg install {package}\r\n".encode("utf-8"))
            time.sleep(2)  # Wait briefly after each installation
            changes_network = 1

        self.logger.info("\n✅ All required packages installed successfully!")
        return changes_network

    def bootstrap_config(self):
        """Do the actual bootstrap config"""
        self.logger.info("applying bootstrap configuration")
        # Get a prompt
        self.wait_write("\r", None)
        time.sleep(0.5)
        self.wait_write("\r", None)
        time.sleep(0.5)

        self.tn.write(b"ls /.firstboot\n")
        time.sleep(0.5)
        output = self.tn.read_very_eager().decode("utf-8").strip()

        if "No such file" in output or output == "":
            # File does not exist, create it
            self.logger.info("First boot detected")
            self.tn.write(b"touch /.firstboot\n")
            time.sleep(0.5)

            # Set root password (ssh login prerequisite)
            self.wait_write("passwd", "#")
            self.wait_write(self.password, "New password:")
            self.wait_write(self.password, "Retype password:")
            # Create vrnetlab user
            self.wait_write(
                f"echo '{self.username}:x:501:501:{self.username}:/home/{self.username}:/bin/ash' >> /etc/passwd",
                "#",
            )
            self.wait_write(f"passwd {self.username}")
            self.wait_write(self.password, "New password:")
            self.wait_write(self.password, "Retype password:")
            # Add user to root group
            self.wait_write("sed -i '1d' /etc/group", "#")
            self.wait_write(f"sed -i '1i root:x:0:{self.username}' /etc/group")
            # Create home dir
            self.wait_write(f"mkdir -p /home/{self.username}")
            self.wait_write(f"chown {self.username} /home/{self.username}")
            self.wait_write(f"chown {self.username} /etc/config/ -R")

        # Track changes
        changes_network = 0
        changes_firewall = 0

        # Get current network config
        network_config = self.get_network_config()

        # Configure mgmt interface
        changes_network += self.configure_mgmt_interface(network_config)

        # Configure br-lan ports
        changes_network += self.check_br_lan_ports()

        # Configure firewall
        changes_firewall += self.configure_firewall_zone()

        # Configure route
        changes_network += self.configure_route(network_config)

        # Reload services if changes were made
        self.reload_services(changes_network, changes_firewall)

        self.routes_and_dns()

        self.packet_update()

        changes_network += self.packages_install()
        if changes_firewall == 1:
            changes_firewall = 0

        self.reload_services(changes_network, changes_firewall)

        self.logger.info("completed bootstrap configuration")
        self.tn.write(b"\x04")
        time.sleep(0.5)


class OpenWRT(vrnetlab.VR):
    def __init__(
        self,
        username,
        password,
        conn_mode,
        nics,
        hostname,
        packet_repository_domains,
        packet_repository_dns_server,
        packages,
        lan_ip,
        lan_netmask,
    ):
        super(OpenWRT, self).__init__(username, password)
        self.vms = [
            OpenWRT_vm(
                username,
                password,
                conn_mode,
                nics,
                hostname,
                packet_repository_domains,
                packet_repository_dns_server,
                packages,
                lan_ip,
                lan_netmask,
            )
        ]


import click


@click.command()
@click.option("--tracing", is_flag=True, help="enable trace level logging")
@click.option(
    "--username",
    "-u",
    default="root",
    envvar="USERNAME",
    required=True,
    help="Username",
)
@click.option(
    "--password",
    "-p",
    default="VR-netlab9",
    envvar="PASSWORD",
    required=True,
    help="Password",
)
@click.option(
    "--connection-mode",
    "-c",
    default="tc",
    envvar="CONNECTION_MODE",
    required=True,
    help="connection mode",
)
@click.option(
    "--nics",
    "-n",
    default="16",
    envvar="NICS",
    required=True,
    type=int,
    help="Number of NICS",
)
@click.option("--hostname", "-h", envvar="HOSTNAME", required=False, help="Hostname")
@click.option(
    "--packet-repository-domains",
    "-prd",
    envvar="PACKET_REPOSITORY_DOMAINS",
    required=False,
    help="repository domains like downloads.openwrt.com",
)
@click.option(
    "--packet-repository-dns-server",
    "-prds",
    default="8.8.8.8",
    envvar="PACKET_REPOSITORY_DNS_SERVER",
    required=True,
    help="default 8.8.8.8 only one IP",
)
@click.option(
    "--packages", "-pcks", envvar="PACKAGES", required=False, help="eg tinc htop etc"
)
@click.option(
    "--lan-ip",
    "-ip",
    default="10.0.0.15",
    envvar="LAN_IP",
    required=True,
    help="Lan IP",
)
@click.option(
    "--lan-netmask",
    "-mask",
    default="255.255.255.0",
    envvar="LAN_NETMASK",
    required=True,
    help="Lan netmask",
)
def args(
    tracing,
    username,
    password,
    connection_mode,
    nics,
    hostname,
    packet_repository_domains,
    packet_repository_dns_server,
    packages,
    lan_ip,
    lan_netmask,
):
    LOG_FORMAT = "%(asctime)s: %(module)-10s %(levelname)-8s %(message)s"
    logging.basicConfig(format=LOG_FORMAT)
    logger = logging.getLogger()

    logger.setLevel(logging.DEBUG)
    if tracing:
        logger.setLevel(1)

    vr = OpenWRT(
        username,
        password,
        connection_mode,
        nics,
        hostname,
        packet_repository_domains,
        packet_repository_dns_server,
        packages,
        lan_ip,
        lan_netmask,
    )
    vr.start()


if __name__ == "__main__":
    args()
    # import argparse
    # parser = argparse.ArgumentParser(description='')
    # parser.add_argument('--trace', action='store_true', help='enable trace level logging')
    # parser.add_argument('--username', default='vrnetlab', help='Username')
    # parser.add_argument('--password', default='VR-netlab9', help='Password')
    # parser.add_argument(
    #     "--connection-mode",
    #     default="vrxcon",
    #     help="Connection mode to use in the datapath",
    # )
    # args = parser.parse_args()
