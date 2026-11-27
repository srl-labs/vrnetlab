#!/usr/bin/env -S uv run

import datetime
import logging
import os
import re
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


class GENUSCREEN_vm(vrnetlab.VM):
    def __init__(self, hostname, username, password, conn_mode, install_mode=False):
        disk_image = None
        iso_image = None
        
        for e in os.listdir("/"):
            if re.search(r"\.qcow2$", e):
                disk_image = "/" + e
            elif re.search(r"\.iso$", e):
                iso_image = "/" + e

        self.install_mode = install_mode
        
        if self.install_mode and iso_image:
            # Create empty disk for installation
            disk_image = "/genuscreen-disk.qcow2"
            if not os.path.exists(disk_image):
                vrnetlab.run_command([
                    "qemu-img", "create", "-f", "qcow2", disk_image, "20G"
                ])
            self.iso_image = iso_image
        elif not disk_image:
            raise ValueError("No disk image found")

        super(GENUSCREEN_vm, self).__init__(
            username, password, disk_image=disk_image, ram=4096, smp="2"
        )
        
        self.hostname = hostname
        self.conn_mode = conn_mode
        self.num_nics = 8
        self.nic_type = "virtio-net-pci"

        # Add entropy sources for better performance
        self.qemu_args.extend([
            "-object", "rng-random,filename=/dev/urandom,id=rng0",
            "-device", "virtio-rng-pci,rng=rng0,max-bytes=1024,period=1000"
        ])

        if self.install_mode and hasattr(self, 'iso_image'):
            self.qemu_args.extend([
                "-boot", "order=cd",
                "-cdrom", self.iso_image
            ])

    def bootstrap_spin(self):
        """This function should be called periodically to do work."""

        if self.spins > 1200:
            # too many spins with no result -> give up
            self.stop()
            self.start()
            return

        if self.install_mode:
            return self._handle_installation()
        else:
            return self._handle_normal_boot()

    def _handle_installation(self):
        """Handle the installation process"""
        installation_prompts = [
            b"proceed",
            b"32-bit appliance", 
            b"Keyboard mapping",
            b"Fully Qualified Domain Name", 
            b"Which interface", 
            b"Address?",
            b"Netmask length",
            b"Media",
            b"Default gateway",
            b"New password:",
            b"Retype new password:",
            b"Enable SSH daemon",
            b"Restrict access to Web-GUI",
            b"Admin-ACL network",
            b"Admin-ACL netmask length",
            b"Save configuration to disk",
            b"wait for more?",
            b"login:"
        ]
        
        installation_responses = [
            "yes",           # proceed
            "no",            # 32-bit appliance
            "de",            # Keyboard mapping
            self.hostname,   # FQDN
            "",              # Which interface (default)
            "10.0.0.15",     # Address
            "24",            # Netmask length
            "",              # Media (default)
            "10.0.0.2",      # Default gateway
            self.password,   # New password
            self.password,   # Retype password
            "yes",           # Enable SSH
            "no",            # Restrict Web-GUI
            "192.168.1.0",   # Admin-ACL network
            "24",            # Admin-ACL netmask length
            "yes",           # Save configuration
            "no",            # wait for more
            None             # login prompt - installation complete
        ]
        
        (ridx, match, res) = self.tn.expect(installation_prompts, 1)

        if match:
            if ridx == len(installation_prompts) - 1:  # login prompt - installation complete
                self.logger.info("Installation completed successfully")
                install_time = datetime.datetime.now() - self.start_time
                self.logger.info("Install complete in: %s", install_time)
                self.running = True
                return
            elif ridx < len(installation_responses) and installation_responses[ridx] is not None:
                self.wait_write(installation_responses[ridx], wait=None)

        # no match, if we saw some output it's probably still installing
        if res != b"":
            self.logger.trace("INSTALL OUTPUT: %s", res.decode())
            self.spins = 0

        self.spins += 1

    def _handle_normal_boot(self):
        """Handle normal boot process"""
        (ridx, match, res) = self.tn.expect([b"login:"], 1)
        
        if match and ridx == 0:
            self.logger.info("Genuscreen boot completed")
            self.wait_write(self.username, wait=None)
            self.wait_write(self.password, wait="Password:")
            
            # close telnet connection
            self.tn.close()
            startup_time = datetime.datetime.now() - self.start_time
            self.logger.info("Startup complete in: %s", startup_time)
            self.running = True
            return

        # no match, if we saw some output from the router it's probably
        # booting, so let's give it some more time
        if res != b"":
            self.logger.trace("BOOT OUTPUT: %s", res.decode())
            # reset spins if we saw some output
            self.spins = 0

        self.spins += 1


class GENUSCREEN(vrnetlab.VR):
    def __init__(self, hostname, username, password, conn_mode):
        super(GENUSCREEN, self).__init__(username, password)
        self.vms = [GENUSCREEN_vm(hostname, username, password, conn_mode)]


class GENUSCREEN_installer(GENUSCREEN):
    """GENUSCREEN installer
    
    Will start Genuscreen with ISO mounted and perform installation
    to create the final QCOW2 image for subsequent boots.
    """
    
    def __init__(self, hostname, username, password, conn_mode):
        super(GENUSCREEN, self).__init__(username, password)
        self.vms = [
            GENUSCREEN_vm(hostname, username, password, conn_mode, install_mode=True)
        ]

    def install(self):
        """Run the installation process"""
        self.logger.info("Installing Genuscreen")
        genuscreen = self.vms[0]
        while not genuscreen.running:
            genuscreen.work()
        
        time.sleep(10)
        genuscreen.stop()
        self.logger.info("Installation complete")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="")
    parser.add_argument(
        "--trace", action="store_true", help="enable trace level logging"
    )
    parser.add_argument("--hostname", default="vr-genuscreen", help="Router hostname")
    parser.add_argument("--username", default="root", help="Username")
    parser.add_argument("--password", default="VR-netlab9", help="Password")
    parser.add_argument(
        "--connection-mode",
        default="tc",
        help="Connection mode to use in the datapath",
    )
    parser.add_argument("--install", action="store_true", help="Install Genuscreen")
    
    args = parser.parse_args()

    LOG_FORMAT = "%(asctime)s: %(module)-10s %(levelname)-8s %(message)s"
    logging.basicConfig(format=LOG_FORMAT)
    logger = logging.getLogger()

    logger.setLevel(logging.DEBUG)
    if args.trace:
        logger.setLevel(1)

    logger.debug(f"Environment variables: {os.environ}")
    vrnetlab.boot_delay()

    if args.install:
        vr = GENUSCREEN_installer(
            args.hostname, args.username, args.password, args.connection_mode
        )
        vr.install()
    else:
        vr = GENUSCREEN(
            args.hostname, args.username, args.password, args.connection_mode
        )
        vr.start()