#!/usr/bin/env python3

import argparse
import datetime
import ipaddress
import logging
import os
import signal
import subprocess
import sys
import tempfile
import textwrap

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
    if self.isEnabledFor(TRACE_LEVEL_NUM):
        self._log(TRACE_LEVEL_NUM, message, args, **kws)


logging.Logger.trace = trace


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def make_cidata_iso(seed_dir, mgmt_ipv4, mgmt_gw, hostname, admin_password, root_password):
    default_user_data = textwrap.dedent(
        f"""\
        #cloud-config
        write_files:
          - path: /config/onboarding/day0.sh
            permissions: '0755'
            owner: root:root
            content: |
              #!/bin/sh
              set -eux
              tmsh modify sys global-settings gui-setup disabled
              tmsh modify sys global-settings mgmt-dhcp disabled
              tmsh modify sys global-settings hostname {hostname}
        """
    )

    if mgmt_ipv4 and mgmt_ipv4 != "dhcp":
        default_user_data += f"      tmsh modify sys management-ip {mgmt_ipv4}\n"
    if mgmt_gw:
        default_user_data += (
            f"      tmsh delete sys management-route default || true\n"
            f"      tmsh create sys management-route default gateway {mgmt_gw} || tmsh modify sys management-route default gateway {mgmt_gw}\n"
        )

    default_user_data += textwrap.dedent(
        f"""\
              tmsh modify auth user admin password {admin_password}
              tmsh modify auth user root password {root_password}
              tmsh save sys config
        runcmd:
          - nohup sh -c '/config/onboarding/day0.sh' &
        """
    )

    user_data = os.environ.get("USER_DATA", default_user_data)
    meta_data = os.environ.get(
        "META_DATA",
        textwrap.dedent(
            f"""\
            instance-id: bigip-ve
            local-hostname: {hostname}
            """
        ),
    )

    with open(os.path.join(seed_dir, "user-data"), "w") as f:
        f.write(user_data)
    with open(os.path.join(seed_dir, "meta-data"), "w") as f:
        f.write(meta_data)

    iso = os.path.join(seed_dir, "cidata.iso")
    subprocess.check_call(
        [
            "genisoimage",
            "-quiet",
            "-output",
            iso,
            "-volid",
            "cidata",
            "-joliet",
            "-rock",
            os.path.join(seed_dir, "user-data"),
            os.path.join(seed_dir, "meta-data"),
        ]
    )
    return iso


class F5BigIPVM(vrnetlab.VM):
    def __init__(
        self,
        username,
        password,
        disk_image,
        nics,
        conn_mode,
        ram,
        cpu,
        smp,
        mgmt_passthrough,
        hostname,
        admin_password,
        root_password,
    ):
        super().__init__(
            username=username,
            password=password,
            disk_image=disk_image,
            ram=ram,
            cpu=cpu,
            smp=smp,
            mgmt_passthrough=mgmt_passthrough,
        )

        self.num_nics = nics
        normalized_mode = conn_mode.lower()
        if normalized_mode in ("tc-mirred", "tc-mirror", "tc-mirrored"):
            normalized_mode = "tc"
        self.conn_mode = normalized_mode
        self.hostname = hostname
        self.admin_password = admin_password
        self.root_password = root_password

        self.mgmt_nic_type = "e1000"
        self.data_nic_type = "virtio-net-pci"
        self.nic_type = self.data_nic_type
        self.wait_pattern = "login:"

        mgmt_ip = None
        mgmt_gw = None
        try:
            mgmt_ip = self.mgmt_address_ipv4
            mgmt_gw = self.mgmt_gw_ipv4
            if mgmt_ip and mgmt_ip != "dhcp":
                try:
                    mgmt_ip = str(ipaddress.IPv4Interface(mgmt_ip))
                except ValueError:
                    self.logger.warning(f"Invalid management IPv4 address: {mgmt_ip}")
                    mgmt_ip = None
        except Exception as e:
            self.logger.warning(f"Could not determine management addressing: {e}")

        seed_dir = tempfile.mkdtemp(prefix="bigip-seed-")
        self.cidata_iso = make_cidata_iso(
            seed_dir,
            mgmt_ip,
            mgmt_gw,
            hostname,
            admin_password,
            root_password,
        )
        self.qemu_args.extend(
            [
                "-drive",
                f"file={self.cidata_iso},if=virtio,media=cdrom,format=raw,readonly=on",
            ]
        )

    def gen_mgmt(self):
        current = self.nic_type
        self.nic_type = self.mgmt_nic_type
        res = super().gen_mgmt()
        self.nic_type = current
        return res

    def bootstrap_spin(self):
        if self.spins > 7200:
            self.logger.debug("Too many spins -> restart")
            self.stop()
            self.start()
            return

        (ridx, match, res) = self.tn.expect(
            [b"login: ", b"localhost login:", b"bigip login:"], 1
        )
        if match:
            self.logger.info("Login prompt detected; marking VM as running")
            self.running = True
            try:
                self.tn.close()
            except Exception:
                pass
            startup_time = datetime.datetime.now() - self.start_time
            self.logger.info("Startup complete in: %s", startup_time)
            return

        if res != b"":
            try:
                self.logger.trace("OUTPUT: %s" % res.decode(errors="ignore"))
            except Exception:
                pass
            self.spins = 0
        self.spins += 1


class F5BigIP(vrnetlab.VR):
    def __init__(
        self,
        hostname,
        username,
        password,
        root_password,
        disk_image,
        nics,
        conn_mode,
        ram,
        cpu,
        smp,
        mgmt_passthrough,
    ):
        super().__init__(username, password, mgmt_passthrough=mgmt_passthrough)
        self.vms = [
            F5BigIPVM(
                username=username,
                password=password,
                disk_image=disk_image,
                nics=nics,
                conn_mode=conn_mode,
                ram=ram,
                cpu=cpu,
                smp=smp,
                mgmt_passthrough=self.mgmt_passthrough,
                hostname=hostname,
                admin_password=password,
                root_password=root_password,
            )
        ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="F5 BIG-IP VE for vrnetlab")
    parser.add_argument("--trace", action="store_true", help="enable trace logging")
    parser.add_argument("--hostname", default=os.environ.get("F5_HOSTNAME", "bigip-ve"))
    parser.add_argument("--username", default=os.environ.get("USERNAME", "admin"))
    parser.add_argument("--password", default=os.environ.get("PASSWORD", "admin"))
    parser.add_argument(
        "--root-password", dest="root_password", default=os.environ.get("ROOT_PASSWORD", "default")
    )
    parser.add_argument("--disk", default=os.environ.get("VM_DISK", "/disk/flash.qcow2"))
    parser.add_argument("--ram", type=int, default=env_int("QEMU_MEMORY", 8192))
    parser.add_argument("--cpu", default=os.environ.get("QEMU_CPU", "host"))
    parser.add_argument("--smp", default=os.environ.get("QEMU_SMP", "4"))
    parser.add_argument("--nics", type=int, default=env_int("CLAB_INTFS", 3))
    parser.add_argument(
        "--connection-mode",
        default=os.environ.get("CONNECTION_MODE", "tc"),
        help="tc|bridge|ovs-bridge|macvtap",
    )
    args = parser.parse_args()

    LOG_FORMAT = "%(asctime)s: %(module)-10s %(levelname)-8s %(message)s"
    logging.basicConfig(format=LOG_FORMAT)
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    if args.trace:
        logger.setLevel(TRACE_LEVEL_NUM)

    vr = F5BigIP(
        hostname=args.hostname,
        username=args.username,
        password=args.password,
        root_password=args.root_password,
        disk_image=args.disk,
        nics=args.nics,
        conn_mode=args.connection_mode,
        ram=args.ram,
        cpu=args.cpu,
        smp=args.smp,
        mgmt_passthrough=True,
    )
    vr.start()
