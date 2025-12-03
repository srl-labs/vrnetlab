#!/usr/bin/env python3

import datetime
import logging
import os
import re
import signal
import subprocess
import sys

import vrnetlab

CLOUD_INIT_CONFIG_FILE = "/config/cloud-init.yaml"
ZCLOUD_XML_FILE = "/config/zcloud.xml"


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


class Sdwan_component_vm(vrnetlab.VM):
    def __init__(
        self,
        hostname,
        username,
        password,
        nics,
        conn_mode,
        component_type,
    ):
        for e in os.listdir("/"):
            if re.search(".qcow2$", e):
                disk_image = "/" + e
                # Detect component type from filename if not specified
                if not component_type:
                    e_lower = e.lower()
                    if "manage" in e_lower:
                        component_type = "manager"
                    elif "smart" in e_lower:
                        component_type = "controller"
                    elif "bond" in e_lower or "edge" in e_lower:
                        component_type = "validator"

        # Set RAM and SMP based on component type
        # Allow environment variables to override defaults
        ram_map = {
            "manager": 32768,
            "controller": 4096,
            "validator": 2048,
        }
        smp_map = {
            "manager": "4",
            "controller": "2",
            "validator": "1",
        }

        super(Sdwan_component_vm, self).__init__(
            username, password, disk_image=disk_image,
            ram=ram_map.get(component_type, 4096),
            smp=smp_map.get(component_type, "1")
        )

        self.conn_mode = conn_mode
        self.component_type = component_type
        self.nic_type = "virtio-net-pci"

        # Set hostname to sdwan-<component-type> if not specified
        self.hostname = hostname if hostname else f"sdwan-{component_type}"

        # Store number of NICs for later use
        self.num_nics = nics

        self.image_name = "cloud_init.iso"
        self.create_boot_image()

        self.qemu_args.extend(["-cdrom", "/" + self.image_name])

        # Add data disk for vManage only (virtio since nvme not supported in this QEMU)
        if self.component_type == "manager":
            self.add_disk("50G", driveif="virtio")

        if "ADD_DISK" in os.environ:
            disk_size = os.getenv("ADD_DISK")

            self.add_disk(disk_size)

    def load_template(self, template_name):
        """Load and render a template file"""
        template_path = f"/templates/{template_name}"
        with open(template_path, "r") as f:
            template = f.read()

        # Extract IP and prefix from CIDR notation (e.g., "10.0.0.15/24")
        ip_with_prefix = self.mgmt_address_ipv4
        variables = {
            "hostname": self.hostname,
            "username": self.username,
            "password": self.password,
            "mgmt_ip": ip_with_prefix.split('/')[0],
            "mgmt_prefix": ip_with_prefix.split('/')[1],
            "mgmt_gw": self.mgmt_gw_ipv4
        }

        for key, value in variables.items():
            template = template.replace(f"{{{{ {key} }}}}", str(value))

        return template

    def gen_cloud_config(self, custom_zcloud=None):
        """Generate cloud-init configuration based on component type

        Args:
            custom_zcloud: Optional custom zcloud.xml content. If None, uses template.
        """
        # Map component types to their personalities and template names
        component_map = {
            "manager": {"personality": "vmanage", "template": "manager-zcloud.xml.j2"},
            "controller": {"personality": "vsmart", "template": "controller-zcloud.xml.j2"},
            "validator": {"personality": "vbond", "template": "validator-zcloud.xml.j2"}
        }

        config = component_map.get(self.component_type, component_map["manager"])

        # Use custom zcloud if provided, otherwise load from template
        if custom_zcloud:
            zcloud_xml = custom_zcloud
        else:
            zcloud_xml = self.load_template(config["template"])

        # Build cloud-init config
        cloud_config = "#cloud-config\n"

        # Vinit to define persona
        if self.component_type == "validator":
            cloud_config += f"""vinit:
  personality: {config['personality']}
"""

        # Add disk setup only for manager
        if self.component_type == "manager":
            cloud_config += """disk_setup:
  /dev/vda:
    table_type: mbr
    layout: false
    overwrite: false
fs_setup:
- device: /dev/vda
  label: data
  partition: none
  filesystem: ext4
  overwrite: false
mounts:
- [ /dev/vda, /opt/data ]
"""


        cloud_config += "write_files:\n"

        # Add persona file only for manager
        if self.component_type == "manager":
            cloud_config += """- path: /opt/web-app/etc/persona
  owner: vmanage:vmanage-admin
  permissions: '0644'
  content: '{"persona":"COMPUTE_AND_DATA"}'
"""

        # Add XML config
        cloud_config += f"""
- path: /etc/confd/init/zcloud.xml
  content: |
{chr(10).join('    ' + line for line in zcloud_xml.split(chr(10)))}
"""

        return cloud_config

    def create_boot_image(self):
        """Creates a cloud-init iso image with a bootstrap configuration"""
        cloud_config = self._load_user_config() or self._generate_default_config()

        with open("/bootstrap_config.yaml", "w") as cfg_file:
            cfg_file.write(cloud_config)

        subprocess.run(["cloud-localds", "-v", "/" + self.image_name, "/bootstrap_config.yaml"], check=True)

    def _load_user_config(self):
        """Load user-provided configuration if present"""
        if os.path.exists(CLOUD_INIT_CONFIG_FILE):
            self.logger.info("Found full cloud-init configuration at %s", CLOUD_INIT_CONFIG_FILE)
            with open(CLOUD_INIT_CONFIG_FILE, "r") as f:
                return f.read()

        if os.path.exists(ZCLOUD_XML_FILE):
            self.logger.info("Found zcloud.xml configuration at %s", ZCLOUD_XML_FILE)
            with open(ZCLOUD_XML_FILE, "r") as f:
                return self.gen_cloud_config(custom_zcloud=f.read())

        return None

    def _generate_default_config(self):
        """Generate default cloud-init configuration"""
        self.logger.info("Generating default configuration for %s", self.component_type)
        return self.gen_cloud_config()

    def bootstrap_spin(self):
        """This function should be called periodically to do work."""

        if self.spins > 6000:
            # too many spins with no result ->  give up
            self.logger.debug("Too many spins -> give up")
            self.stop()
            self.start()
            return

        (ridx, match, res) = self.tn.expect([b"System Ready", b"CDB boot error"], 1)
        if match:  # got a match!
            if ridx == 0:  # System Ready
                self.logger.debug("System Ready detected")
                self.wait_write("", wait=None)

                self.running = True
                # close telnet connection
                self.tn.close()
                # startup time?
                startup_time = datetime.datetime.now() - self.start_time
                self.logger.info("Startup complete in: %s", startup_time)
                return
            elif ridx == 1:  # CDB boot error
                self.logger.error("CDB boot error detected in logs - restarting")
                self.stop()
                self.start()
                return

        # no match, if we saw some output from the router it's probably
        # booting, so let's give it some more time
        if res != b"":
            self.write_to_stdout(res)
            # reset spins if we saw some output
            self.spins = 0

        self.spins += 1

        return

    # No gen_nics override - use parent class implementation like c8000v does

    def add_disk(self, disk_size, driveif="ide"):
        additional_disk = f"disk_{disk_size}.qcow2"

        if not os.path.exists(additional_disk):
            self.logger.debug(f"Creating additional disk image {additional_disk}")
            vrnetlab.run_command(
                ["qemu-img", "create", "-f", "qcow2", additional_disk, disk_size]
            )

        self.qemu_args.extend(
            [
                "-drive",
                f"if={driveif},file={additional_disk}",
            ]
        )


class Sdwan_component(vrnetlab.VR):
    def __init__(self, hostname, username, password, nics, conn_mode, component_type):
        super(Sdwan_component, self).__init__(username, password)
        self.vms = [Sdwan_component_vm(hostname, username, password, nics, conn_mode, component_type)]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="")
    parser.add_argument(
        "--trace", action="store_true", help="enable trace level logging"
    )
    parser.add_argument("--username", default="admin", help="Username")
    parser.add_argument("--password", default="admin", help="Password")
    parser.add_argument("--hostname", default=None, help="VM Hostname (default: sdwan-<component-type>)")
    parser.add_argument("--nics", type=int, default=5, help="Number of NICS")
    parser.add_argument(
        "--component-type",
        choices=["manager", "controller", "validator"],
        help="SD-WAN component type (auto-detected from image if not specified)",
    )
    parser.add_argument(
        "--connection-mode",
        default=os.environ.get("CONNECTION_MODE", "tc"),
        help="Connection mode to use in the datapath",
    )
    args = parser.parse_args()

    LOG_FORMAT = "%(asctime)s: %(module)-10s %(levelname)-8s %(message)s"
    logging.basicConfig(format=LOG_FORMAT)
    logger = logging.getLogger()

    logger.setLevel(logging.DEBUG)
    if args.trace:
        logger.setLevel(TRACE_LEVEL_NUM)

    vr = Sdwan_component(
        args.hostname,
        args.username,
        args.password,
        args.nics,
        args.connection_mode,
        args.component_type,
    )
    vr.start()