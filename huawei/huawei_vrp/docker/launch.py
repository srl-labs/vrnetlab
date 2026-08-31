#!/usr/bin/env python3

import datetime
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


class VRP_vm(vrnetlab.VM):
    def __init__(self, username, password, hostname, conn_mode):
        disk_image = None
        self.vm_type = "UNKNOWN"
        self.vm_version = "UNKNOWN"

        for e in sorted(os.listdir("/")):
            if not disk_image and re.search(".qcow2$", e):
                disk_image = "/" + e
                if "huawei_ne40e" in e:
                    self.vm_type = "NE40E"
                if "huawei_ce12800" in e:
                    self.vm_type = "CE12800"

                # try to detect VRP version from filename (qcow images should contain strings like V800R011,V800R022,V800R023)
                m = re.search(r"(V\d+R\d+)", e)
                if m:
                    self.vm_version = m.group(1)

        # default RAM und CPU values which will be used when there is no match from qcow image name
        ram = 4096
        smp = "4"

        # override depending on version found in qcow image name
        # R22 requires 8G and 8 CPU
        if self.vm_version == "V800R022":
            ram = 8192
            smp = "8"
        # R23 and R11 require 4G and 2 CPU (for sure more is better)
        elif self.vm_version in ["V800R023", "V800R011"]:
            ram = 4096
            smp = "2"

        super(VRP_vm, self).__init__(
            username,
            password,
            disk_image=disk_image,
            ram=ram,
            smp=smp,
            driveif="virtio",
        )

        # Ex-Configuration for machine type and SATA controller tests (this is probably not needed but doesn't hurt either)
        self.qemu_args.extend(
            [
                "-machine",
                "pc-q35-6.2",  # Use q35 machine type for better SATA support
            ]
        )

        self.hostname = hostname
        self.conn_mode = conn_mode
        self.num_nics = 14
        self.nic_type = "virtio-net-pci"

    def bootstrap_spin(self):
        """This function should be called periodically to do work."""

        if self.spins > 300:
            # too many spins with no result -> give up and restart
            self.stop()
            self.start()
            return

        # Nudge the console so the prompt is re-printed if a previous read consumed it.
        self.tn.write(b"\r\n")

        # First check for prompt
        (ridx, match, res) = self.tn.expect([b"<HUAWEI>"], 1)

        # Read any additional available output immediately
        try:
            extra_output = self.tn.read_very_eager()
        except EOFError:
            extra_output = b""

        # Combine the two (so we don’t miss anything)
        full_output = b"".join([res or b"", extra_output or b""])

        # Print/log each line received
        if full_output:
            try:
                decoded_output = full_output.decode(errors="ignore")
            except UnicodeDecodeError:
                decoded_output = str(full_output)

            for line in decoded_output.splitlines():
                if line.strip():  # skip empty lines
                    self.logger.info(f"DEVICE: {line}")
            self.spins = 0  # reset spin counter if we saw anything

        # If prompt matched (via expect OR drained into full_output) do config
        if (match and ridx == 0) or (b"<HUAWEI>" in full_output):
            # fetch VRP version first
            self.logger.info("Fetching VRP version...")
            self.tn.write(b"display version\n")
            time.sleep(3)
            output = self.tn.read_until(b"<", timeout=3).decode(errors="ignore")

            # extract VRP version
            import re

            # Look for something like V800R011C00 (ignore SPC part)
            m = re.search(r"(V\d+R\d+C\d+)", output)
            if m:
                self.vm_version = m.group(1)  # only the first part
                self.logger.info(f"Detected VRP version: {self.vm_version}")
            else:
                self.vm_version = "UNKNOWN"
                self.logger.warning("Could not detect VRP version!")

            # call the startup and bootstrap methods
            self.logger.info("Running bootstrap_config()")
            self.startup_config()
            self.bootstrap_config()
            time.sleep(1)
            self.tn.close()
            startup_time = datetime.datetime.now() - self.start_time
            self.logger.info(f"Startup complete in: {startup_time}")
            self.running = True
            return

        time.sleep(5)
        self.spins += 1

        return

    def wait_write(
        self,
        cmd,
        wait="__defaultpattern__",
        con=None,
        clean_buffer=False,
        hold="",
        timeout=20,
    ):
        """Bound every VRP console wait with a timeout.

        On VRP V800R023 the device in mmi-mode does not always re-print the ']'
        prompt (notably after 'clear configuration this' on the mgmt interface),
        and telnetlib read_until() blocks with no timeout, deadlocking the
        bootstrap. Defaulting to a finite timeout degrades a missed prompt to a
        wait-then-proceed (mmi-mode still executes the queued command) instead of
        a permanent hang.
        """
        super().wait_write(
            cmd,
            wait=wait,
            con=con,
            clean_buffer=clean_buffer,
            hold=hold,
            timeout=timeout,
        )

    def bootstrap_mgmt_interface(self):
        # wait for system to become ready for configuration
        # otherwise we might see Error: The system is busy in building configuration. Please wait for a moment...
        self.logger.info(
            "bootstrap_mgmt_interface - Sleeping for another 60s to wait for the system to become ready...()"
        )
        time.sleep(60)
        self.wait_write(cmd="mmi-mode enable", wait=None)
        self.wait_write(cmd="system-view", wait=">")
        self.wait_write(cmd="ip vpn-instance __MGMT_VPN__", wait="]")
        self.wait_write(cmd="ipv4-family", wait="]")
        self.wait_write(cmd="quit", wait="]")
        if self.mgmt_address_ipv6 and self.mgmt_address_ipv6 != "dhcp":
            self.wait_write(cmd="ipv6-family", wait="]")
            self.wait_write(cmd="quit", wait="]")
        self.wait_write(cmd="quit", wait="]")
        if self.vm_type == "CE12800":
            mgmt_interface = "MEth"
        if self.vm_type == "NE40E":
            mgmt_interface = "GigabitEthernet"
        self.wait_write(cmd=f"interface {mgmt_interface} 0/0/0", wait="]")
        # NOTE: 'clear configuration this' on V800R023 in mmi-mode deadlocks the
        # bootstrap (the mmi action-command summary stalls the telnet reader and
        # the worker thread never returns). It is only meant to wipe any default
        # config off the freshly-booted mgmt interface, which is unnecessary
        # here, so skip it on NE40E and configure the interface directly.
        if self.vm_type != "NE40E":
            self.wait_write(cmd="clear configuration this", wait=None)
            self.tn.read_until(b"Error", 3)
        self.wait_write(cmd="undo shutdown", wait=None)
        self.wait_write(cmd="ip binding vpn-instance __MGMT_VPN__", wait="]")
        self.wait_write(
            cmd=f"ip address {self.mgmt_address_ipv4.replace('/', ' ')}", wait="]"
        )
        if self.mgmt_address_ipv6 and self.mgmt_address_ipv6 != "dhcp":
            self.wait_write(cmd="ipv6 enable", wait="]")
            self.wait_write(
                cmd=f"ipv6 address {self.mgmt_address_ipv6.replace('/', ' ')}", wait="]"
            )
        self.wait_write(cmd="quit", wait="]")
        self.wait_write(
            cmd=f"ip route-static vpn-instance __MGMT_VPN__ 0.0.0.0 0 {self.mgmt_gw_ipv4}",
            wait="]",
        )
        if (
            self.mgmt_address_ipv6
            and self.mgmt_address_ipv6 != "dhcp"
            and self.mgmt_gw_ipv6
            and self.mgmt_gw_ipv6 != "dhcp"
        ):
            self.wait_write(
                cmd=f"ipv6 route-static vpn-instance __MGMT_VPN__ :: 0 {self.mgmt_gw_ipv6}",
                wait="]",
            )

    def bootstrap_config(self):
        """Do the actual bootstrap config"""

        # Example: conditional config
        if getattr(self, "vm_version", None) == "V800R023C00":
            self.logger.info("Applying config for VRP version V800R023C00")
            # run R23 specific commands here
            # self.wait_write(cmd="undo user-security-policy enable", wait="]")
            # self.wait_write(cmd="undo dcn", wait="]")
        if getattr(self, "vm_version", None) == "V800R011C00":
            self.logger.info("Applying config for VRP version V800R011C00")
            # run R11 specific commands here
            # self.wait_write(cmd="undo user-security-policy enable", wait="]")
            # self.wait_write(cmd="undo dcn", wait="]")

        # Default / generic config here
        self.logger.info("Applying generic bootstrap config...")
        # ... rest of your existing bootstrap_config logic ...

        self.bootstrap_mgmt_interface()
        self.wait_write(cmd=f"sysname {self.hostname}", wait="]")

        if self.vm_type == "CE12800":
            self.wait_write(cmd="aaa", wait="]")
            self.wait_write(cmd="undo local-user policy security-enhance", wait="]")
            self.wait_write(cmd="quit", wait="]")
        if self.vm_type == "NE40E":
            self.wait_write(cmd="undo user-security-policy enable", wait="]")
            self.wait_write(cmd="undo dcn", wait="]")

        self.wait_write(cmd="aaa", wait="]")
        self.wait_write(cmd=f"undo local-user {self.username}", wait="]")
        self.wait_write(
            cmd=f"local-user {self.username} password irreversible-cipher {self.password}",
            wait="]",
        )
        self.wait_write(
            cmd=f"local-user {self.username} service-type ssh terminal telnet ftp",
            wait="]",
        )
        self.wait_write(cmd=f"local-user {self.username} level 3", wait="]")

        self.wait_write(cmd=f"authentication-scheme default_admin", wait="]")
        self.wait_write(cmd=f"authentication-mode local hwtacacs", wait="]")
        self.wait_write(cmd="quit", wait="]")
        self.wait_write(cmd=f"authorization-scheme default_admin", wait="]")
        self.wait_write(cmd=f"authorization-mode local hwtacacs", wait="]")
        self.wait_write(cmd="quit", wait="]")
        self.wait_write(cmd="quit", wait="]")

        # Commit is hanging in the bootstrap with version R23 for unknown reason so we rely on the auto-commit with mmi-mode
        # self.wait_write(cmd="commit", wait="]")
        time.sleep(5)

        # self.wait_write(
        #    cmd=f"local-user {self.username} user-group manage-ug", wait="]"
        # )
        # self.wait_write(cmd="quit", wait="]")

        # VTY configuration
        self.wait_write(cmd="user-interface vty 0 4", wait="]")
        self.wait_write(cmd="authentication-mode aaa", wait="]")
        # We want all protocols to be allowed on the vty
        self.wait_write(cmd="protocol inbound all", wait="]")
        # We want only ssh to be allowed on the vty
        # self.wait_write(cmd="protocol inbound ssh", wait="]")
        self.wait_write(cmd="quit", wait="]")

        # Commit is hanging in the bootstrap with version R23 for unknown reason so we rely on the auto-commit with mmi-mode
        # self.wait_write(cmd="commit", wait="]")
        time.sleep(5)

        # Enable stelnet, sftp, scp, ssh
        self.wait_write(cmd="stelnet server enable", wait="]")
        self.wait_write(cmd="sftp ipv4 server enable", wait="]")
        self.wait_write(cmd="sftp ipv6 server enable", wait="]")
        self.wait_write(cmd="scp server enable", wait="]")
        self.wait_write(cmd="ssh authentication-type default password", wait="]")
        self.wait_write(cmd="ssh server-source all-interface", wait="]")
        self.wait_write(cmd="ssh ipv6 server-source all-interface", wait="]")
        self.wait_write(cmd="sftp server default-directory cfcard:/", wait="]")

        # Set some ciphers for compatibility
        self.wait_write(
            cmd="ssh server cipher aes256_gcm aes128_gcm aes256_ctr aes192_ctr aes128_ctr aes256_cbc aes128_cbc 3des_cbc",
            wait="]",
        )
        self.wait_write(
            cmd="ssh server hmac sha2_512 sha2_256_96 sha2_256 sha1 sha1_96 md5 md5_96",
            wait="]",
        )
        self.wait_write(
            cmd="ssh server key-exchange dh_group_exchange_sha256 dh_group_exchange_sha1 dh_group14_sha1 dh_group1_sha1 ecdh_sha2_nistp256 ecdh_sha2_nistp384 ecdh_sha2_nistp521 sm2_kep dh_group16_sha512 curve25519_sha256",
            wait="]",
        )
        self.wait_write(
            cmd="ssh server publickey dsa ecc rsa rsa_sha2_256 rsa_sha2_512", wait="]"
        )
        self.wait_write(cmd="ssh server dh-exchange min-len 1024", wait="]")
        self.wait_write(
            cmd="ssh client publickey dsa ecc rsa rsa_sha2_256 rsa_sha2_512", wait="]"
        )
        self.wait_write(
            cmd="ssh client cipher aes256_gcm aes128_gcm aes256_ctr aes192_ctr aes128_ctr aes256_cbc aes128_cbc 3des_cbc",
            wait="]",
        )
        self.wait_write(
            cmd="ssh client hmac sha2_512 sha2_256_96 sha2_256 sha1 sha1_96 md5 md5_96",
            wait="]",
        )
        self.wait_write(
            cmd="ssh client key-exchange dh_group_exchange_sha256 dh_group_exchange_sha1 dh_group14_sha1 dh_group1_sha1 ecdh_sha2_nistp256 ecdh_sha2_nistp384 ecdh_sha2_nistp521 sm2_kep dh_group16_sha512 curve25519_sha256",
            wait="]",
        )

        # NETCONF seems to crash the virtual R23 router so do not configure it
        # self.wait_write(cmd="snetconf server enable", wait="]")
        # self.wait_write(cmd="netconf", wait="]")
        # self.wait_write(cmd="protocol inbound ssh port 830", wait="]")
        # self.wait_write(cmd="quit", wait="]")

        time.sleep(5)
        # We will only do a final quit here and with mmi-mode enable all changes will be commited automatically
        self.wait_write(cmd="quit", wait="]")
        # if we do not commit we will not see the ">", with mmi-mode enable the system automatically commits when leaving system-view
        # self.wait_write(cmd="save", wait=">")
        # Under heavy load commit might take some seconds. Better give some more time to wait for commit to complete.
        time.sleep(15)
        # self.wait_write(cmd="undo mmi-mode enable", wait=">")

    def startup_config(self):
        if not os.path.exists(STARTUP_CONFIG_FILE):
            self.logger.trace(f"Startup config file {STARTUP_CONFIG_FILE} not found")
            return

        vrnetlab.run_command(["cp", STARTUP_CONFIG_FILE, "/tftpboot/containerlab.cfg"])

        if self.vm_type == "CE12800":
            with open(STARTUP_CONFIG_FILE, "r+") as file:
                cfg = file.read()
                modified = False

                if "device board 1 " not in cfg:
                    cfg = "device board 1 board-type CE-LPUE\n" + cfg
                    modified = True

                if "interface NULL0" not in cfg:
                    cfg = cfg + "\ninterface NULL0"
                    modified = True

                if modified:
                    file.seek(0)
                    file.write(cfg)
                    file.truncate()

        self.bootstrap_mgmt_interface()
        # self.wait_write(cmd="commit", wait="]")

        self.wait_write(cmd=f"return", wait="]")
        time.sleep(1)
        self.wait_write(
            cmd=f"tftp 10.0.0.2 vpn-instance __MGMT_VPN__ get containerlab.cfg",
            wait=">",
        )
        self.wait_write(cmd="startup saved-configuration containerlab.cfg", wait=">")
        self.wait_write(cmd="reboot fast", wait=">")
        self.wait_write(cmd="reboot", wait="#")
        self.wait_write(cmd="", wait="The current login time is")
        print(f"File '{STARTUP_CONFIG_FILE}' successfully loaded")

    def gen_mgmt(self):
        """Generate qemu args for the mgmt interface(s)"""
        # call parent function to generate the mgmt interface
        res = super().gen_mgmt()

        # Creates required dummy interface
        res.append(f"-device virtio-net-pci,netdev=dummy,mac={vrnetlab.gen_mac(0)}")
        res.append("-netdev tap,ifname=vrp-dummy,id=dummy,script=no,downscript=no")

        return res


class VRP(vrnetlab.VR):
    def __init__(self, hostname, username, password, conn_mode):
        super(VRP, self).__init__(username, password)
        self.vms = [VRP_vm(username, password, hostname, conn_mode)]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="")
    parser.add_argument(
        "--trace", action="store_true", help="enable trace level logging"
    )
    parser.add_argument("--hostname", default="vr-VRP", help="Router hostname")
    parser.add_argument("--username", default="vrnetlab", help="Username")
    parser.add_argument("--password", default="VR-netlab9", help="Password")
    parser.add_argument(
        "--connection-mode",
        default="tc",
        help="Connection mode to use in the datapath",
    )

    args = parser.parse_args()

    LOG_FORMAT = "%(asctime)s: %(module)-10s %(levelname)-8s %(message)s"
    logging.basicConfig(format=LOG_FORMAT)
    logger = logging.getLogger()

    logger.setLevel(logging.DEBUG)

    if args.trace:
        logger.setLevel(1)

    vr = VRP(
        args.hostname, args.username, args.password, conn_mode=args.connection_mode
    )
    vr.start()
