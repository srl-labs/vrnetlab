"""The ntpsec-ntpdate if-up hook step of the Dell SONiC launcher.

Run with: python3 -m unittest discover -s dell/dell_sonic/tests
"""

import logging
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_console_prompt import PROMPT, FakeConsole, make_vm
from test_management_bootstrap import launch, write_exe


class HookStepTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(launch, "CONFIG_FILE", "/nonexistent/config_db.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_bootstrap(self, hook_chunks):
        console = FakeConsole([b"MGMT_READY\r\n", PROMPT], hook_chunks)
        vm = make_vm(console)
        vm.logger = logging.getLogger("dell_sonic_hook_test")
        with self.assertLogs(vm.logger, level="INFO") as logs:
            vm.bootstrap_config()
        self.assertEqual(console.writes[-1], "logout")
        self.assertEqual(console.prompts_left(), 1)
        return console.writes, "\n".join(logs.output)

    def test_runs_after_login_and_before_management_setup(self):
        cmds, _ = self.run_bootstrap(None)
        hook = next(i for i, c in enumerate(cmds) if "NTPSEC_HOOK" in c)
        self.assertEqual(cmds[hook - 1], "sudo -i")
        self.assertIn("ip address replace", cmds[hook + 1])
        # The console echoes the command; the markers must not appear in it.
        self.assertNotIn("NTPSEC_HOOK_DISABLED", cmds[hook])
        self.assertNotIn("NTPSEC_HOOK_UNCHANGED", cmds[hook])

    def test_logs_only_when_disabled(self):
        for chunks in (
            [b"NTPSEC_HOOK_DISABLED\r\n" + PROMPT],
            [b"NTPSEC_HOOK_DISABLED\r\n", PROMPT],
        ):
            with self.subTest(chunks=chunks):
                _, logs = self.run_bootstrap(chunks)
                self.assertIn("Disabled " + launch.NTPSEC_HOOK, logs)
        for chunks in (
            [b"NTPSEC_HOOK_UNCHANGED\r\n" + PROMPT],
            [b"NTPSEC_HOOK_UNCHANGED\r\n", PROMPT],
        ):
            with self.subTest(chunks=chunks):
                _, logs = self.run_bootstrap(chunks)
                self.assertNotIn("ntpsec", logs)

    def test_missing_marker_warns_and_continues(self):
        console = FakeConsole([b"MGMT_READY\r\n", PROMPT], [b"\r\n" + PROMPT])
        vm = make_vm(console)
        with self.assertLogs(vm.logger, level="WARNING") as logs:
            vm.bootstrap_config()
        self.assertIn(launch.NTPSEC_HOOK, "\n".join(logs.output))
        self.assertEqual(console.writes[-1], "logout")


class HookSnippetTests(unittest.TestCase):
    """Run the generated guest snippet against a stub pkill."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "calls.log"
        self.hook = self.tmp / "ntpsec-ntpdate"
        write_exe(self.bin / "pkill", f'#!/bin/sh\necho "pkill $*" >> "{self.log}"\n')
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}")

    def run_cmd(self):
        res = subprocess.run(
            ["sh", "-c", launch.ntpsec_hook_cmd(str(self.hook))],
            check=False,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        calls = self.log.read_text() if self.log.exists() else ""
        return res.stdout.strip(), calls

    def test_executable_hook_is_disabled_once(self):
        write_exe(self.hook, "#!/bin/sh\n")
        out, calls = self.run_cmd()
        self.assertEqual(out, "NTPSEC_HOOK_DISABLED")
        self.assertFalse(os.access(self.hook, os.X_OK))
        self.assertEqual(calls, "pkill -x -f systemctl start ntpsec.service\n")
        # Reused disk: already disabled, nothing to do.
        out, calls = self.run_cmd()
        self.assertEqual(out, "NTPSEC_HOOK_UNCHANGED")
        self.assertEqual(calls.count("pkill"), 1)

    def test_absent_hook_is_a_no_op(self):
        out, calls = self.run_cmd()
        self.assertEqual(out, "NTPSEC_HOOK_UNCHANGED")
        self.assertEqual(calls, "")
        self.assertFalse(self.hook.exists())


if __name__ == "__main__":
    unittest.main()
