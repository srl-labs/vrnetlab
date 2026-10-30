"""Startup config failure propagation for backup.sh and launch.py.

Runs without KVM, a SONiC image, network access or Docker:
- backup.sh is executed with stub sshpass/sleep executables on PATH.
- launch.py is imported with a stub vrnetlab module and a mocked subprocess.

Run with: python3 -m unittest discover -s dell/dell_sonic/tests
"""

import importlib.util
import logging
import os
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
BACKUP_SH = DOCKER_DIR / "backup.sh"
PASSWORD = "s3cr3t-pa55"

# Stub sshpass: logs its arguments, then fails depending on the step under test.
SSHPASS_STUB = """#!/bin/bash
echo "$*" >> "$STUB_LOG"
case "$*" in
    *ConnectTimeout*) [ "$FAIL_STEP" = ssh ] && exit 255 ;;
    *scp*) [ "$FAIL_STEP" = scp ] && exit 1 ;;
    *"config save"*)
        [ "$FAIL_STEP" = load ] && exit 1
        [ "$FAIL_STEP" = save ] && exit 1 ;;
esac
exit 0
"""


class BackupRestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        bindir = self.tmp / "bin"
        bindir.mkdir()
        for name, body in (("sshpass", SSHPASS_STUB), ("sleep", "#!/bin/sh\nexit 0\n")):
            path = bindir / name
            path.write_text(body)
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        self.config = self.tmp / "config_db.json"
        self.config.write_text("{}\n")
        self.log = self.tmp / "calls.log"
        self.env = dict(
            os.environ,
            PATH=f"{bindir}:{os.environ['PATH']}",
            STUB_LOG=str(self.log),
            BACKUP_FILE=str(self.config),
        )

    def restore(self, fail_step=""):
        env = dict(self.env, FAIL_STEP=fail_step)
        return subprocess.run(
            ["bash", str(BACKUP_SH), "-u", "admin", "-p", PASSWORD, "restore"],
            check=False,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def calls(self):
        return self.log.read_text() if self.log.exists() else ""

    def test_success(self):
        res = self.restore()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("config load -y", self.calls())

    def test_ssh_timeout_fails(self):
        res = self.restore("ssh")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Config copy operation aborted", res.stdout)
        self.assertEqual(self.calls().count("ConnectTimeout"), 30)
        self.assertNotIn("config load", self.calls())

    def test_scp_failure_fails(self):
        res = self.restore("scp")
        self.assertNotEqual(res.returncode, 0)
        self.assertNotIn("config load", self.calls())

    def test_load_failure_fails(self):
        self.assertNotEqual(self.restore("load").returncode, 0)

    def test_save_failure_fails(self):
        self.assertNotEqual(self.restore("save").returncode, 0)

    def test_no_startup_config_is_ok(self):
        self.config.unlink()
        res = self.restore()
        self.assertEqual(res.returncode, 0)
        self.assertIn("not found. Nothing to push", res.stdout)
        self.assertEqual(self.calls(), "")


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
                "dell_sonic_launch", DOCKER_DIR / "launch.py"
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)
    return module


launch = load_launch()


class LauncherStartupConfigTests(unittest.TestCase):
    def make_vm(self):
        vm = launch.Dell_Sonic_VM.__new__(launch.Dell_Sonic_VM)
        vm.username = "admin"
        vm.password = PASSWORD
        vm.hostname = "leaf1"
        vm.running = False
        vm.spins = 0
        vm.logger = logging.getLogger("dell_sonic_test")
        vm.logger.trace = MagicMock()
        vm.start_time = launch.datetime.datetime.now()
        vm.tn = MagicMock()
        vm.tn.expect.return_value = (0, True, b"login:")
        vm.wait_write = MagicMock()
        vm.bootstrap_config = MagicMock()
        return vm

    def spin(self, vm, exists, run_side_effect=None):
        with (
            patch.object(launch.os.path, "exists", return_value=exists),
            patch.object(launch.subprocess, "run", side_effect=run_side_effect) as run,
        ):
            vm.bootstrap_spin()
        return run

    def test_restore_success_marks_running(self):
        vm = self.make_vm()
        run = self.spin(vm, exists=True)
        run.assert_called_once()
        self.assertIn("restore", run.call_args.args[0])
        self.assertTrue(vm.running)

    def test_no_config_skips_restore_and_marks_running(self):
        vm = self.make_vm()
        run = self.spin(vm, exists=False)
        run.assert_not_called()
        self.assertTrue(vm.running)

    def test_restore_failure_logs_command_and_exits_nonzero(self):
        vm = self.make_vm()
        err = subprocess.CalledProcessError(
            1, f"/backup.sh -u admin -p {PASSWORD} restore"
        )
        with (
            self.assertLogs(vm.logger, level="ERROR") as logs,
            self.assertRaises(SystemExit) as cm,
        ):
            self.spin(vm, exists=True, run_side_effect=err)
        self.assertNotEqual(cm.exception.code, 0)
        self.assertFalse(vm.running)
        output = "\n".join(logs.output)
        self.assertIn("startup config", output.lower())
        self.assertIn(f"/backup.sh -u admin -p {PASSWORD} restore", output)


if __name__ == "__main__":
    unittest.main()
