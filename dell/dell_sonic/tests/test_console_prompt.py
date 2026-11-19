"""Serial-console prompt state around the readiness wait.

vrnetlab's console expect()/read_until() return on a match and drop the rest
of the chunk they read. These tests drive bootstrap_config() against a fake
console with that behaviour and a deterministic chunking of guest output.

Run with: python3 -m unittest discover -s dell/dell_sonic/tests
"""

import logging
import re
import unittest
from unittest.mock import patch

from test_management_bootstrap import launch

PROMPT = b"root@sonic:~# "


class Hang(Exception):
    """An unbounded console wait with no more output: the real launcher hangs."""


class FakeConsole:
    """Scripted guest console with vrnetlab _Console read semantics."""

    def __init__(self, ready_chunks):
        self.ready_chunks = ready_chunks
        self.pending = [b"admin@sonic:~$ "]
        self.writes = []
        self.passwd = 0
        self.lose_prompt_after = None

    def write(self, data):
        cmd = data.decode().rstrip("\r")
        self.writes.append(cmd)
        if self.lose_prompt_after and self.lose_prompt_after in cmd:
            self.pending.append(b"\r\n")
            return
        if cmd.startswith("passwd -q"):
            self.pending.append(b"New password: ")
        elif (
            self.passwd == 0
            and self.writes[-2:-1]
            and self.writes[-2].startswith("passwd -q")
        ):
            self.passwd = 1
            self.pending.append(b"Retype new password: ")
        elif self.passwd == 1:
            self.passwd = 2
            self.pending.append(b"passwd: password updated successfully\r\n" + PROMPT)
        elif "until " in cmd:
            self.pending.extend([cmd.encode() + b"\r\n"] + self.ready_chunks)
        else:
            self.pending.append(cmd.encode() + b"\r\n" + PROMPT)

    def _read(self, timeout):
        if self.pending:
            return self.pending.pop(0)
        if timeout is None:
            raise Hang(self.writes[-1] if self.writes else "")
        return None

    def read_until(self, expected, timeout=None):
        buf = b""
        while expected not in buf:
            data = self._read(timeout)
            if data is None:
                break
            buf += data
        return buf

    def expect(self, regex_list, timeout=None):
        buf = b""
        while True:
            data = self._read(timeout)
            if data is None:
                return -1, None, buf
            buf += data
            for i, pat in enumerate(regex_list):
                match = re.search(pat.decode(), buf.decode(errors="ignore"))
                if match:
                    return i, match, buf

    def prompts_left(self):
        return b"".join(self.pending).count(b"#")


def make_vm(console):
    vm = launch.Dell_Sonic_VM.__new__(launch.Dell_Sonic_VM)
    vm.username = "admin"
    vm.password = "secret"
    vm.hostname = "leaf1"
    vm.mgmt_address_ipv4 = "10.0.0.15/24"
    vm.mgmt_address_ipv6 = None
    vm.mgmt_gw_ipv4 = "10.0.0.2"
    vm.mgmt_gw_ipv6 = None
    vm.logger = logging.getLogger("dell_sonic_console_test")
    vm.wait_pattern = "#"
    vm.tn = console
    vm.qm = None

    def wait_write(cmd, wait="__defaultpattern__", con=None, timeout=None, **_):
        # Mirrors vrnetlab.VM.wait_write: wait (read_until) then write.
        con = con or vm.tn
        if wait:
            wait = vm.wait_pattern if wait == "__defaultpattern__" else wait
            con.read_until(wait.encode(), timeout)
        con.write(f"{cmd}\r".encode())

    vm.wait_write = wait_write
    return vm


class PromptAfterReadinessTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(launch, "CONFIG_FILE", "/nonexistent/config_db.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_bootstrap(self, ready_chunks):
        console = FakeConsole(ready_chunks)
        vm = make_vm(console)
        vm.bootstrap_config()
        cmds = console.writes
        host = next(i for i, c in enumerate(cmds) if "hostname -F" in c)
        self.assertEqual(cmds[-1], "logout")
        self.assertLess(host, len(cmds) - 1)
        # Exactly the logout's own prompt remains: no stale extra prompt.
        self.assertEqual(console.prompts_left(), 1)
        return cmds

    def test_marker_and_prompt_in_one_chunk(self):
        self.run_bootstrap([b"MGMT_READY\r\n" + PROMPT])

    def test_marker_and_prompt_in_separate_chunks(self):
        cmds = self.run_bootstrap([b"MGMT_READY\r\n", PROMPT])
        self.assertNotIn("", cmds)

    def test_timeout_marker_and_prompt_in_one_chunk(self):
        self.run_bootstrap([b"MGMT_TIMEOUT\r\n" + PROMPT])

    def test_lost_prompt_fails_within_bound(self):
        console = FakeConsole([b"MGMT_READY\r\n", PROMPT])
        console.lose_prompt_after = "hostname -F"
        vm = make_vm(console)
        with (
            self.assertLogs(vm.logger, level="ERROR") as logs,
            self.assertRaises(SystemExit) as exit_,
        ):
            vm.bootstrap_config()
        self.assertEqual(exit_.exception.code, 1)
        self.assertIn("prompt", "\n".join(logs.output))
        self.assertNotIn("logout", console.writes)


if __name__ == "__main__":
    unittest.main()
