"""Hostname initialization for the Dell SONiC launcher.

Runs without KVM, a SONiC image, network access or Docker:
- launch.py is imported with a stub vrnetlab module and a mocked serial console.
- The generated guest shell snippet runs against stub executables on PATH.

Run with: python3 -m unittest discover -s dell/dell_sonic/tests
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_management_bootstrap import index_of, launch, make_vm, sent, write_exe


class IntendedHostnameTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.config = self.tmp / "config_db.json"
        patcher = patch.object(launch, "CONFIG_FILE", str(self.config))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.vm = make_vm()

    def write_config(self, data):
        self.config.write_text(data if isinstance(data, str) else json.dumps(data))

    def test_node_name_without_startup_config(self):
        self.assertEqual(self.vm.intended_hostname(), "leaf1")

    def test_startup_config_hostname_wins(self):
        self.write_config({"DEVICE_METADATA": {"localhost": {"hostname": "leaf9"}}})
        self.assertEqual(self.vm.intended_hostname(), "leaf9")

    def test_startup_config_without_hostname_uses_node_name(self):
        self.write_config({"DEVICE_METADATA": {"localhost": {"hwsku": "x"}}})
        self.assertEqual(self.vm.intended_hostname(), "leaf1")
        self.write_config({"PORT": {}})
        self.assertEqual(self.vm.intended_hostname(), "leaf1")

    def test_malformed_startup_config_falls_back_with_warning(self):
        for data in ("{not json", "[]", '{"DEVICE_METADATA": []}'):
            with self.subTest(data=data):
                self.write_config(data)
                with self.assertLogs(self.vm.logger, level="WARNING"):
                    self.assertEqual(self.vm.intended_hostname(), "leaf1")

    def test_invalid_startup_config_hostname_is_not_replaced_silently(self):
        self.write_config({"DEVICE_METADATA": {"localhost": {"hostname": "a b;x"}}})
        with self.assertLogs(self.vm.logger, level="WARNING"):
            self.assertIsNone(self.vm.intended_hostname())

    def test_invalid_node_name_is_skipped_with_warning(self):
        self.vm.hostname = "bad_name$(x)"
        with self.assertLogs(self.vm.logger, level="WARNING"):
            self.assertIsNone(self.vm.intended_hostname())


class BootstrapHostnameTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(launch, "CONFIG_FILE", "/nonexistent/config_db.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_sed_on_config_db_and_hostname_after_readiness(self):
        vm = make_vm()
        vm.bootstrap_config()
        cmds = sent(vm)
        self.assertFalse(any("sed -i" in c and "config_db.json" in c for c in cmds))
        host = index_of(cmds, "hostname -F")
        self.assertLess(index_of(cmds, "until "), host)
        self.assertLess(host, index_of(cmds, "logout"))
        self.assertIn("leaf1", cmds[host])

    def test_invalid_hostname_sends_no_hostname_command(self):
        vm = make_vm()
        vm.hostname = "bad name"
        with self.assertLogs(vm.logger, level="WARNING"):
            vm.bootstrap_config()
        self.assertFalse(any("hostname -F" in c for c in sent(vm)))


class HostnameSnippetTests(unittest.TestCase):
    """Run the generated hostname snippet against stub guest commands."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.kernel = self.tmp / "kernel_hostname"
        self.kernel.write_text("sonic\n")
        self.db = self.tmp / "db.log"
        self.hostname_file = self.tmp / "hostname"
        self.hostname_file.write_text("sonic\n")
        self.hosts = self.tmp / "hosts"
        self.hosts.write_text(
            "127.0.0.1       localhost\n127.0.0.1 sonic\n10.1.1.1 sonic-ntp\n"
        )
        self.config_db = self.tmp / "config_db.json"
        write_exe(
            self.bin / "hostname",
            f"""#!/bin/sh
if [ "$1" = -F ]; then cat "$2" > "{self.kernel}"; else cat "{self.kernel}"; fi
""",
        )
        write_exe(self.bin / "sonic-db-cli", f'#!/bin/sh\necho "$*" >> "{self.db}"\n')
        write_exe(
            self.bin / "sleep", f'#!/bin/sh\necho "$1" >> "{self.tmp}/sleep.log"\n'
        )
        self.hostcfgd = "inactive"
        write_exe(
            self.bin / "systemctl",
            f"""#!/bin/sh
[ "$*" = "is-active --quiet hostcfgd" ] && [ -f "{self.tmp}/hostcfgd" ] && exit 0
exit 3
""",
        )
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}")

    def run_cmd(self, name):
        cmd = launch.hostname_cmd(
            name,
            hostname_file=str(self.hostname_file),
            hosts_file=str(self.hosts),
            config_db=str(self.config_db),
        )
        return subprocess.run(
            ["sh", "-c", cmd],
            check=False,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_applies_hostname_everywhere(self):
        res = self.run_cmd("leaf1-r06")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stderr, "")
        self.assertEqual(self.kernel.read_text().strip(), "leaf1-r06")
        self.assertEqual(self.hostname_file.read_text(), "leaf1-r06\n")
        self.assertEqual(
            self.db.read_text(),
            "CONFIG_DB HSET DEVICE_METADATA|localhost hostname leaf1-r06\n",
        )
        self.assertEqual(
            self.hosts.read_text(),
            "127.0.0.1       localhost\n10.1.1.1 sonic-ntp\n127.0.0.1 leaf1-r06\n",
        )

    def test_repeated_run_is_idempotent(self):
        self.run_cmd("leaf1-r06")
        hosts = self.hosts.read_text()
        self.assertEqual(self.run_cmd("leaf1-r06").returncode, 0)
        self.assertEqual(self.hosts.read_text(), hosts)
        self.assertEqual(self.hostname_file.read_text(), "leaf1-r06\n")

    def test_missing_config_db_file_is_reported_not_an_error(self):
        res = self.run_cmd("leaf1")
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stderr, "")
        self.assertFalse(self.config_db.exists())
        self.assertIn("not persisted", res.stdout)

    def test_existing_config_db_file_is_updated(self):
        self.config_db.write_text(
            json.dumps(
                {"DEVICE_METADATA": {"localhost": {"hostname": "sonic", "mac": "m"}}}
            )
        )
        self.config_db.chmod(0o600)
        self.assertEqual(self.run_cmd("leaf1").returncode, 0)
        data = json.loads(self.config_db.read_text())
        self.assertEqual(
            data["DEVICE_METADATA"]["localhost"], {"hostname": "leaf1", "mac": "m"}
        )
        self.assertEqual(self.config_db.stat().st_mode & 0o777, 0o600)

    def test_localhost_entry_is_kept_when_old_hostname_is_localhost(self):
        self.kernel.write_text("localhost\n")
        self.run_cmd("leaf1")
        self.assertIn("127.0.0.1       localhost\n", self.hosts.read_text())

    def sonic_apply(self, old, new):
        """SONiC's hostname-config.sh / hostcfgd /etc/hosts update."""
        lines = [
            line
            for line in self.hosts.read_text().splitlines()
            if not (line.split()[-1:] == [old] and line != line.split()[-1])
        ]
        lines.append(f"127.0.0.1 {new}")
        self.hosts.write_text("\n".join(lines) + "\n")

    def leaf_entries(self, name):
        return self.hosts.read_text().splitlines().count(f"127.0.0.1 {name}")

    def test_sonic_apply_after_launcher_duplicates_entry(self):
        # Validation #3: hostname-config.sh ran after the launcher's edit,
        # deleted only the old name and appended again. The launcher must
        # therefore run after it (readiness wait), not before.
        self.run_cmd("leaf1")
        self.sonic_apply("sonic", "leaf1")
        self.assertEqual(self.leaf_entries("leaf1"), 2)

    def test_launcher_after_sonic_apply_leaves_one_entry(self):
        self.sonic_apply("sonic", "leaf1")
        self.kernel.write_text("leaf1\n")
        self.run_cmd("leaf1")
        self.assertEqual(self.leaf_entries("leaf1"), 1)
        self.assertNotIn("127.0.0.1 sonic", self.hosts.read_text())

    def hostcfgd_applies_on_hset(self):
        """Make the CONFIG_DB write trigger hostcfgd's handler, as it does
        once hostcfgd has subscribed to DEVICE_METADATA."""
        k, f, h = self.kernel, self.hostname_file, self.hosts
        apply = (
            f'eval "H=\\$$#"; O="$(cat "{k}")"; echo "$H" > "{f}"; '
            f'cp "{f}" "{k}"; sed -i "/\\s$O\\$/d" "{h}"; '
            f'echo "127.0.0.1 $H" >> "{h}"'
        )
        write_exe(
            self.bin / "sonic-db-cli",
            f'#!/bin/sh\necho "$*" >> "{self.db}"\n{apply}\n',
        )

    def test_hostcfgd_applying_hostname_is_left_to_it(self):
        (self.tmp / "hostcfgd").touch()
        self.hostcfgd_applies_on_hset()
        res = self.run_cmd("leaf1")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("hostcfgd applied the hostname", res.stdout)
        self.assertEqual(self.kernel.read_text().strip(), "leaf1")
        self.assertEqual(self.leaf_entries("leaf1"), 1)
        self.assertNotIn("127.0.0.1 sonic", self.hosts.read_text())

    def test_hostcfgd_not_applying_hostname_falls_back_to_live_steps(self):
        # hostcfgd read the new name while initializing (or missed the
        # change), so its handler sees no change and never applies it.
        (self.tmp / "hostcfgd").touch()
        res = self.run_cmd("leaf1")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.kernel.read_text().strip(), "leaf1")
        self.assertEqual(self.hostname_file.read_text(), "leaf1\n")
        self.assertEqual(self.leaf_entries("leaf1"), 1)
        self.assertNotIn("127.0.0.1 sonic", self.hosts.read_text())

    def test_hostcfgd_wait_is_bounded(self):
        (self.tmp / "hostcfgd").touch()
        self.run_cmd("leaf1")
        sleeps = (self.tmp / "sleep.log").read_text().split()
        self.assertEqual(len(sleeps), launch.HOSTCFGD_TIMEOUT)

    def test_no_hostcfgd_wait_without_hostcfgd(self):
        self.run_cmd("leaf1")
        self.assertFalse((self.tmp / "sleep.log").exists())

    def test_snippet_has_no_prompt_character(self):
        # The next prompt wait reads up to "#"; the echoed command must not
        # contain one.
        self.assertNotIn("#", launch.hostname_cmd("leaf1"))

    def test_rejects_unsafe_hostname(self):
        for name in ("a b", "x;rm -rf /", "$(id)", "-lead", "a_b", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                launch.hostname_cmd(name)


if __name__ == "__main__":
    unittest.main()
