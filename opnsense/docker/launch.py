#!/usr/bin/env python3

import datetime
import ipaddress
import logging
import os
import re
import signal
import sys

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

# console markers
PASSWORD_PROMPT = "Password:"
MENU_PROMPT = "Enter an option:"
SHELL_PROMPT = "root@OPNsense:~ #"  # OPNsense root login shell (console menu -> 8)


class OPNsense_vm(vrnetlab.VM):
    """OPNsense firewall/router VM (FreeBSD based).

    Built from the pre-installed OPNsense *nano* serial image, used unmodified.
    The stock nano image puts a static 192.168.1.1 on the LAN and ships with
    SSH disabled, so it is unreachable over vrnetlab's management plane out of
    the box. On the first boot launch.py logs in over the serial console,
    configures the LAN interface (vtnet0) as the management interface, enables
    sshd with root login + password auth, and reboots once to apply.

    The LAN is configured according to vrnetlab's management datapath mode:

      * **host-forwarded** (default): the LAN is set to DHCP and picks up the
        address qemu's user-mode networking hands out (10.0.0.15);
      * **transparent / passthrough** (``CLAB_MGMT_PASSTHROUGH=true``): the LAN
        is given the static address containerlab assigned to the container's
        eth0 (``self.mgmt_address_ipv4``) plus a default gateway
        (``self.mgmt_gw_ipv4``), so the node shows its real management IP. If
        passthrough is combined with ``CLAB_MGMT_DHCP=true`` the LAN is left on
        DHCP for an external server to address.

    The appliance is rebooted once to apply, so we expect two login prompts:
    configure on the first, declare the node ready on the second.
    Default credentials: root / opnsense.
    """

    def __init__(self, hostname, username, password, nics, conn_mode):
        disk_image = ""
        for e in sorted(os.listdir("/")):
            if re.search(r"\.qcow2$", e):
                disk_image = "/" + e
                break

        super(OPNsense_vm, self).__init__(
            username, password, disk_image=disk_image, ram=2048
        )

        self.num_nics = nics
        self.hostname = hostname
        self.conn_mode = conn_mode
        self.nic_type = "virtio-net-pci"

        # one-time console config is applied on the first boot and the VM is
        # rebooted once to apply it; we expect two login prompts.
        self.configured = False

    def gen_mgmt(self):
        """Augment the parent to keep the mgmt interface on the first bus.

        Like the freebsd/openbsd guests, OPNsense enumerates virtio NICs in PCI
        bus order. The parent places the mgmt NIC on a separate bus, which would
        make the OS assign it the last index instead of vtnet0. Force it onto
        pci.1 so it becomes vtnet0 -- the LAN/management interface.
        """
        res = super(OPNsense_vm, self).gen_mgmt()
        if "bus=pci.1" not in res[-3]:
            res[-3] = res[-3] + ",bus=pci.1"
        return res

    def bootstrap_spin(self):
        """Called periodically; stay quiet until the login prompt.

        Pressing a key earlier drops OPNsense into the interactive
        interface-assignment wizard; left alone it auto-proceeds with the
        image's vtnet0=LAN / vtnet1=WAN assignment.
        """
        if self.spins > 600:
            # too many spins with no result -> give up, restart
            self.stop()
            self.start()
            return

        (ridx, match, res) = self.tn.expect([b"login:"], 1)
        if match:
            if ridx == 0:
                if not self.configured:
                    self.logger.debug("login prompt -- applying configuration")
                    self.bootstrap_config()
                    self.configured = True
                    self.spins = 0
                    return
                self.logger.debug("login prompt -- configuration applied")
                self.running = True
                self.tn.close()
                startup_time = datetime.datetime.now() - self.start_time
                self.logger.info("Startup complete in: %s", startup_time)
                return

        if res != b"":
            self.logger.trace("OUTPUT: %s" % res.decode())
            self.spins = 0

        self.spins += 1
        return

    def bootstrap_config(self):
        """Log in over the console, edit /conf/config.xml and reboot.

        OPNsense applies /conf/config.xml on boot, so the changes are made on
        the first boot and a reboot brings the appliance up fully configured.
        bootstrap_spin catches the post-reboot login prompt and declares the
        node ready.
        """
        self.logger.info("applying bootstrap configuration over the console")

        # log in (the login prompt was already consumed by bootstrap_spin)
        self.wait_write("root", wait=None)
        self.wait_write("opnsense", wait=PASSWORD_PROMPT)

        # root's login shell is the OPNsense console menu; option 8 = Shell
        self.wait_write("8", wait=MENU_PROMPT)

        # configure the LAN (vtnet0) management interface for the active
        # management datapath mode
        if self.mgmt_passthrough and not self.mgmt_dhcp:
            self.configure_mgmt_static()
        else:
            self.configure_mgmt_dhcp()

        # enable sshd with root login + password auth
        self.wait_write(
            "sed -i '' -e 's|<group>admins</group>|<group>admins</group>"
            "<enabled>enabled</enabled><permitrootlogin>1</permitrootlogin>"
            "<passwordauth>1</passwordauth>|' /conf/config.xml",
            wait=SHELL_PROMPT,
        )

        # reboot to apply; bootstrap_spin will catch the next login prompt
        self.wait_write("reboot", wait=SHELL_PROMPT)

    def configure_mgmt_dhcp(self):
        """Set the LAN (vtnet0) to DHCP.

        Used for the default host-forwarded datapath (qemu user-mode networking
        hands out 10.0.0.15) and for passthrough + CLAB_MGMT_DHCP, where an
        external DHCP server addresses the management interface.
        """
        self.logger.info("configuring LAN (vtnet0) for DHCP")
        self.wait_write(
            "sed -i '' -e 's|<ipaddr>192.168.1.1</ipaddr>|<ipaddr>dhcp</ipaddr>|' /conf/config.xml",
            wait=SHELL_PROMPT,
        )
        self.wait_write(
            "sed -i '' -e 's|<subnet>24</subnet>|<subnet></subnet>|' /conf/config.xml",
            wait=SHELL_PROMPT,
        )

    def configure_mgmt_static(self):
        """Give the LAN (vtnet0) the management IP containerlab assigned.

        In transparent management mode the mgmt interface mirrors the
        container's eth0, so the guest must carry that exact address plus a
        default gateway to be reachable beyond its own subnet.

        We edit /conf/config.xml with sed, like the other edits here: each
        command is short enough to pass cleanly over the serial console (a
        single long one-liner can exceed the console's line limit and hang).
        The IP/subnet are simple field swaps; the default gateway is a small
        <gateways> object inserted ahead of the existing <staticroutes>
        element, with the LAN pointed at it.
        """
        addr = ipaddress.ip_interface(self.mgmt_address_ipv4)
        ip = str(addr.ip)
        prefix = str(addr.network.prefixlen)
        gw = self.mgmt_gw_ipv4
        self.logger.info(
            "configuring LAN (vtnet0) with static mgmt address %s/%s gw %s",
            ip,
            prefix,
            gw,
        )

        self.wait_write(
            "sed -i '' -e 's|<ipaddr>192.168.1.1</ipaddr>|<ipaddr>%s</ipaddr>|' /conf/config.xml"
            % ip,
            wait=SHELL_PROMPT,
        )
        self.wait_write(
            "sed -i '' -e 's|<subnet>24</subnet>|<subnet>%s</subnet>|' /conf/config.xml"
            % prefix,
            wait=SHELL_PROMPT,
        )
        # point the LAN at a named gateway ...
        self.wait_write(
            "sed -i '' -e 's|<if>vtnet0</if>|<if>vtnet0</if><gateway>MGMTGW</gateway>|' /conf/config.xml",
            wait=SHELL_PROMPT,
        )
        # ... and define that gateway as the system default route
        self.wait_write(
            "sed -i '' -e 's#<staticroutes #<gateways><gateway_item>"
            "<interface>lan</interface><gateway>%s</gateway><name>MGMTGW</name>"
            "<weight>1</weight><ipprotocol>inet</ipprotocol><defaultgw>1</defaultgw>"
            "</gateway_item></gateways><staticroutes #' /conf/config.xml" % gw,
            wait=SHELL_PROMPT,
        )


class OPNsense(vrnetlab.VR):
    def __init__(self, hostname, username, password, nics, conn_mode):
        super(OPNsense, self).__init__(username, password)
        self.vms = [OPNsense_vm(hostname, username, password, nics, conn_mode)]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="")
    parser.add_argument(
        "--trace", action="store_true", help="enable trace level logging"
    )
    parser.add_argument("--username", default="root", help="Username")
    parser.add_argument("--password", default="opnsense", help="Password")
    parser.add_argument("--hostname", default="opnsense", help="VM Hostname")
    parser.add_argument("--nics", type=int, default=16, help="Number of NICS")
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

    vr = OPNsense(
        args.hostname,
        args.username,
        args.password,
        args.nics,
        args.connection_mode,
    )
    vr.start()
