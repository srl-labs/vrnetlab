"""Management bootstrap ordering for the Dell SONiC launcher.

Runs without KVM, a SONiC image, network access or Docker:
- launch.py is imported with a stub vrnetlab module and a mocked serial console.
- The generated guest shell snippets run against stub executables on PATH.

Run with: python3 -m unittest discover -s dell/dell_sonic/tests
"""

import importlib.util
import logging
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

DOCKER_DIR = Path(__file__).resolve().parents[1] / "docker"


def load_launch():
    """Import launch.py with a stub vrnetlab module (no scrapli/QEMU needed)."""
    stub = types.ModuleType("vrnetlab")
    stub.VM = type("VM", (), {})
    stub.VR = type("VR", (), {})
    # launch.py installs process-wide signal handlers on import; its SIGCHLD
    # reaper would steal child exit codes from subprocess.run in these tests.
    saved = {
        s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGCHLD)
    }
    try:
        with patch.dict(sys.modules, {"vrnetlab": stub}):
            spec = importlib.util.spec_from_file_location(
                "dell_sonic_launch_mgmt", DOCKER_DIR / "launch.py"
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)
    return module


launch = load_launch()


def make_vm(ready=True, **mgmt):
    vm = launch.Dell_Sonic_VM.__new__(launch.Dell_Sonic_VM)
    vm.username = "admin"
    vm.password = "secret"
    vm.hostname = "leaf1"
    vm.mgmt_address_ipv4 = mgmt.get("v4", "10.0.0.15/24")
    vm.mgmt_address_ipv6 = mgmt.get("v6", "2001:db8::2/64")
    vm.mgmt_gw_ipv4 = mgmt.get("gw4", "10.0.0.2")
    vm.mgmt_gw_ipv6 = mgmt.get("gw6", "2001:db8::1")
    vm.logger = logging.getLogger("dell_sonic_mgmt_test")
    vm.wait_write = MagicMock()
    vm.tn = MagicMock()
    marker = b"MGMT_READY" if ready else b"MGMT_TIMEOUT"
    vm.tn.expect.return_value = (0 if ready else 1, re.search(marker, marker), marker)
    vm.tn.read_until.return_value = b"root@sonic:~# "
    return vm


def sent(vm):
    return [c.args[0] for c in vm.wait_write.call_args_list]


def index_of(cmds, needle):
    for i, cmd in enumerate(cmds):
        if needle in cmd:
            return i
    raise AssertionError(f"{needle!r} not sent; sent: {cmds}")


class BootstrapSequenceTests(unittest.TestCase):
    def test_addresses_and_routes_are_idempotent(self):
        vm = make_vm()
        vm.bootstrap_config()
        cmds = sent(vm)
        self.assertIn("ip address replace 10.0.0.15/24 dev eth0", " ".join(cmds))
        self.assertIn("ip -6 address replace 2001:db8::2/64 dev eth0", " ".join(cmds))
        self.assertIn("ip route replace default via 10.0.0.2 dev eth0", " ".join(cmds))
        self.assertIn(
            "ip -6 route replace default via 2001:db8::1 dev eth0", " ".join(cmds)
        )
        self.assertFalse(any(" address add " in c or " route add " in c for c in cmds))

    def test_link_up_before_routes_and_wait_before_logout(self):
        vm = make_vm()
        vm.bootstrap_config()
        cmds = sent(vm)
        link = index_of(cmds, "ip link set eth0 up")
        route = index_of(cmds, "route replace default")
        sshd = index_of(cmds, launch.SSHD_DROPIN)
        wait = index_of(cmds, "until ")
        logout = index_of(cmds, "logout")
        self.assertLess(index_of(cmds, "address replace 10.0.0.15"), link)
        self.assertLess(link, route)
        self.assertLess(route, wait)
        self.assertLess(sshd, wait)
        self.assertLess(wait, logout)
        patterns = vm.tn.expect.call_args.args[0]
        self.assertIn(b"MGMT_READY", patterns)
        # The console echoes the command; the marker must not appear verbatim in it.
        self.assertNotIn("MGMT_READY", cmds[wait])

    def test_dhcp_management_skips_static_address_and_routes(self):
        vm = make_vm(v4="dhcp", v6="dhcp", gw4="dhcp", gw6="dhcp")
        vm.bootstrap_config()
        cmds = " ".join(sent(vm))
        self.assertNotIn("address replace", cmds)
        self.assertNotIn("route replace", cmds)
        self.assertIn("ip link set eth0 up", cmds)

    def test_readiness_timeout_logs_warning_and_continues(self):
        vm = make_vm(ready=False)
        with self.assertLogs(vm.logger, level="WARNING") as logs:
            vm.bootstrap_config()
        self.assertIn("management", "\n".join(logs.output).lower())
        self.assertIn("logout", sent(vm)[-1])

    def test_no_fixed_sleep_added(self):
        vm = make_vm()
        vm.bootstrap_config()
        new = [c for c in sent(vm) if c.startswith("sleep")]
        # Only the pre-existing post-passwd "sleep 1" remains.
        self.assertEqual(new, ["sleep 1"])


def write_exe(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class GuestSnippetTests(unittest.TestCase):
    """Run the generated guest shell snippets against stub commands."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "calls.log"
        self.dropin = self.tmp / "sshd_config.d" / "vrnetlab-ipv4.conf"
        self.dropin.parent.mkdir()
        self.main = self.tmp / "sshd_config"
        self.main.write_text("ListenAddress ::\nX11Forwarding yes\n")
        # Stub sshd -T: reports [::]:22 plus 0.0.0.0:22 once the drop-in exists.
        write_exe(
            self.bin / "sshd",
            f"""#!/bin/sh
echo "listenaddress [::]:22"
[ -f "{self.dropin}" ] && echo "listenaddress 0.0.0.0:22"
echo "port 22"
""",
        )
        write_exe(
            self.bin / "systemctl", f'#!/bin/sh\necho "systemctl $*" >> "{self.log}"\n'
        )
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}")

    def run_sh(self, cmd):
        return subprocess.run(
            ["sh", "-c", cmd],
            check=False,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def calls(self):
        return self.log.read_text() if self.log.exists() else ""

    def test_sshd_ipv4_listener_is_idempotent(self):
        cmd = launch.sshd_ipv4_listener_cmd(str(self.dropin))
        before = self.main.read_text()
        self.assertEqual(self.run_sh(cmd).returncode, 0)
        self.assertEqual(self.dropin.read_text(), "ListenAddress 0.0.0.0\n")
        self.assertEqual(self.calls().count("try-reload-or-restart ssh"), 1)
        self.assertEqual(self.run_sh(cmd).returncode, 0)
        self.assertEqual(self.calls().count("try-reload-or-restart ssh"), 1)
        self.assertEqual(self.main.read_text(), before)

    def test_sshd_already_listening_on_ipv4_is_untouched(self):
        write_exe(self.bin / "sshd", '#!/bin/sh\necho "listenaddress 0.0.0.0:22"\n')
        self.assertEqual(
            self.run_sh(launch.sshd_ipv4_listener_cmd(str(self.dropin))).returncode, 0
        )
        self.assertFalse(self.dropin.exists())
        self.assertEqual(self.calls(), "")

    def stub_state(self, link, listener, config_setup, db_init, hostname_cfg=""):
        write_exe(
            self.bin / "ip",
            f'#!/bin/sh\necho "2: eth0: <UP> mtu 1500 state {link} mode DEFAULT"\n',
        )
        ss_line = "LISTEN 0 128 0.0.0.0:22 0.0.0.0:*" if listener else ""
        write_exe(
            self.bin / "ss",
            f'#!/bin/sh\necho "LISTEN 0 128 [::]:22 [::]:*"\necho "{ss_line}"\n',
        )
        # Stub systemctl: only "is-active ... config-setup" reports config_setup.
        write_exe(
            self.bin / "systemctl",
            f"""#!/bin/sh
case "$*" in
    is-active*config-setup*) [ "{config_setup}" = active ] && exit 0; exit 3 ;;
    list-jobs*) [ "{hostname_cfg}" = queued ] && echo "42 hostname-config.service start waiting"; exit 0 ;;
    show*hostname-config*) [ "{hostname_cfg}" = activating ] && echo activating || echo inactive; exit 0 ;;
esac
exit 1
""",
        )
        # Stub sonic-db-cli: only "CONFIG_DB GET CONFIG_DB_INITIALIZED" is answered.
        write_exe(
            self.bin / "sonic-db-cli",
            f"""#!/bin/sh
[ "$*" = "CONFIG_DB GET CONFIG_DB_INITIALIZED" ] && echo "{db_init}"
exit 0
""",
        )
        write_exe(self.bin / "sleep", "#!/bin/sh\nexit 0\n")

    def test_wait_reports_ready_when_guest_ready(self):
        self.stub_state("UP", True, "active", "1")
        res = self.run_sh(launch.mgmt_ready_cmd(timeout=5))
        self.assertEqual(res.stdout.strip(), "MGMT_READY")

    def test_wait_reports_timeout_until_every_condition_holds(self):
        for state in (
            ("UP", False, "active", "1"),
            ("DOWN", True, "active", "1"),
            # config-setup still reloading CONFIG_DB (observed race on 4.6)
            ("UP", True, "activating", "1"),
            ("UP", True, "active", "0"),
            ("UP", True, "active", ""),
            # hostname-config.sh still pending or running: it would append
            # a second /etc/hosts entry after the launcher's (validation #3)
            ("UP", True, "active", "1", "queued"),
            ("UP", True, "active", "1", "activating"),
        ):
            with self.subTest(state=state):
                self.stub_state(*state)
                res = self.run_sh(launch.mgmt_ready_cmd(timeout=1))
                self.assertEqual(res.stdout.strip(), "MGMT_TIMEOUT")


if __name__ == "__main__":
    unittest.main()
