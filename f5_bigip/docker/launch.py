#!/usr/bin/env python3

import argparse
import datetime
import ipaddress
import logging
import os
import signal
import subprocess
import sys
import telnetlib
import tempfile
import textwrap
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
    if self.isEnabledFor(TRACE_LEVEL_NUM):
        self._log(TRACE_LEVEL_NUM, message, args, **kws)


logging.Logger.trace = trace


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def make_cidata_iso(seed_dir, mgmt_ipv4, mgmt_gw, hostname, admin_password, root_password):
    """Build cloud-init seed ISO used for day0 config (mgmt IP/route + creds)."""
    default_user_data = textwrap.dedent(
        f"""\
        #cloud-config
        write_files:
          - path: /config/onboarding/day0.sh
            permissions: '0755'
            owner: root:root
            content: |
              #!/bin/sh
              set -euo pipefail

              log() {{
                echo "$(date -Ins) $*"
              }}

              wait_for_mcpd() {{
                for i in $(seq 1 120); do
                  if tmsh -a show sys mcp-state field-fmt 2>/dev/null | grep -q running; then
                    return 0
                  fi
                  log "mcpd not ready (try $i), sleeping 5s"
                  sleep 5
                done
                return 1
              }}

              log "waiting for mcpd to become ready"
              wait_for_mcpd || exit 1

              log "applying base system settings"
              tmsh modify sys global-settings gui-setup disabled
              tmsh modify sys global-settings mgmt-dhcp disabled
              tmsh modify sys global-settings hostname {hostname}
        """
    )

    if mgmt_ipv4 and mgmt_ipv4 != "dhcp":
        default_user_data += f"              log \"setting management ip to {mgmt_ipv4}\"\n"
        default_user_data += (
            f"              tmsh create sys management-ip {mgmt_ipv4} || tmsh modify sys management-ip {mgmt_ipv4}\n"
        )
    if mgmt_gw:
        default_user_data += textwrap.dedent(
            f"""\
              log "setting management default route to {mgmt_gw}"
              tmsh delete sys management-route default || true
              tmsh create sys management-route default gateway {mgmt_gw} || tmsh modify sys management-route default gateway {mgmt_gw}
            """
        )

    default_user_data += textwrap.dedent(
        f"""\
              log "setting credentials and saving config"
              tmsh modify auth user admin password {admin_password}
              tmsh modify auth user root password {root_password}
              tmsh save sys config
        runcmd:
          - [ "/bin/sh", "-c", "/config/onboarding/day0.sh > /var/log/onboard.log 2>&1" ]
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
        self.console_provisioned = False

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

    def _read_until(self, tn, marker: bytes, timeout: int = 30):
        """Backward-compatible wrapper around telnet.read_until for reuse in expect flows."""
        try:
            return tn.read_until(marker, timeout)
        except Exception as e:
            self.logger.warning(f"Telnet read_until({marker}) failed: {e}")
            return b""

    def _expect(self, tn, patterns, timeout: int = 20):
        """Wrapper around telnet expect that logs failures and always returns tuple."""
        try:
            return tn.expect(patterns, timeout)
        except Exception as e:
            self.logger.warning(f"Telnet expect failed: {e}")
            return (-1, None, b"")

    def _wait_for_mcpd(self, tn, attempts=30, sleep_s=5):
        """Wait until mcpd reports running via tmsh."""
        for i in range(attempts):
            self.logger.info("Waiting for mcpd to be running (try %s/%s)", i + 1, attempts)
            tn.write(b"tmsh -c 'show sys mcp-state field-fmt'\n")
            idx, match, buf = self._expect(tn, [b"[>#] ", b"(tmos)# "], 15)
            out = buf.decode(errors="ignore")
            if "running" in out.lower():
                self.logger.info("mcpd is running")
                return True
            time.sleep(sleep_s)
        self.logger.warning("mcpd did not reach running state after %s attempts", attempts)
        return False

    def configure_mgmt_via_console(self):
        """Drive console once to handle forced password change and apply mgmt IP/route if cloud-init could not."""
        if self.console_provisioned:
            return
        if not self.mgmt_address_ipv4 or self.mgmt_address_ipv4 == "dhcp":
            self.logger.info("Skipping console mgmt provisioning; mgmt IP is unset or DHCP")
            return

        new_password = os.environ.get("F5_NEW_PASSWORD", "Labl@b!234")
        tn = getattr(self, "tn", None)
        if tn is None:
            self.logger.warning("Telnet console not available for mgmt provisioning")
            return

        self.logger.info("Applying mgmt IP via console (handling forced password change if present)")
        try:
            pwd_candidates = []
            if self.root_password:
                pwd_candidates.append(self.root_password)
            if new_password not in pwd_candidates:
                pwd_candidates.append(new_password)

            logged_in = False
            for pwd in pwd_candidates:
                # Kick the login prompt
                tn.write(b"\n")
                self._expect(tn, [b"login: ", b"localhost login:", b"bigip login:"], 10)
                tn.write(b"root\n")
                self._expect(tn, [b"Password:"], 20)
                tn.write((pwd + "\n").encode())

                # Handle forced password change dialog
                for _ in range(8):
                    idx, match, buf = self._expect(
                        tn,
                        [
                            br"\(current\) BIG-IP password:",
                            b"New BIG-IP password:",
                            b"Retype new BIG-IP password:",
                            b"[>#] ",
                            b"login: ",
                        ],
                        20,
                    )
                    if idx == 0:
                        tn.write((pwd + "\n").encode())
                    elif idx == 1:
                        tn.write((new_password + "\n").encode())
                    elif idx == 2:
                        tn.write((new_password + "\n").encode())
                        # consume any informational messages
                        time.sleep(1)
                        tn.read_very_eager()
                        # Update stored passwords so subsequent logins use the new secret
                        self.root_password = new_password
                        self.admin_password = new_password
                    elif idx == 3:
                        logged_in = True
                        break  # have a shell prompt
                    elif idx == 4:
                        self.logger.warning(
                            "Console provisioning: login prompt reappeared; trying next password candidate"
                        )
                        break
                    else:
                        self.logger.warning("Console provisioning timed out waiting for prompts")
                        break

                if logged_in:
                    break

            if not logged_in:
                self.logger.warning("Console provisioning: could not log in with provided passwords")
                return

            def send_cmd(cmd, timeout=30):
                self.logger.info("console apply: %s", cmd)
                tn.write((cmd + "\n").encode())
                self._expect(tn, [b"[>#] ", b"(tmos)# "], timeout)

            # Ensure we have a prompt
            send_cmd("echo READY")

            if not self._wait_for_mcpd(tn):
                return

            cmds = [
                "tmsh -c 'modify sys global-settings mgmt-dhcp disabled'",
                f"tmsh -c 'create sys management-ip {self.mgmt_address_ipv4}'",
            ]
            if self.mgmt_gw_ipv4:
                cmds.extend(
                    [
                        "tmsh -c 'delete sys management-route default'",
                        f"tmsh -c 'modify sys management-route default gateway {self.mgmt_gw_ipv4}'",
                        f"tmsh -c 'create sys management-route default gateway {self.mgmt_gw_ipv4}'",
                    ]
                )
            # Align admin (GUI) password with root password to avoid first-login prompts
            if self.admin_password:
                cmds.append(f"tmsh -c 'modify auth user admin password {self.admin_password}'")
            cmds.append("tmsh -c 'save sys config'")

            for cmd in cmds:
                send_cmd(cmd, 60)

            self.console_provisioned = True
            self.logger.info("Console mgmt provisioning complete")
        except Exception as e:
            self.logger.warning(f"Console mgmt provisioning failed: {e}")

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
                self.configure_mgmt_via_console()
            except Exception as e:
                self.logger.warning(f"Failed to auto-configure mgmt via console: {e}")
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
