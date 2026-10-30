"""Container restart safety for the Dell SONiC launcher.

Run with: python3 -m unittest discover -s dell/dell_sonic/tests
"""

import logging
import unittest
from unittest.mock import MagicMock, patch

from test_console_prompt import FakeConsole as PromptConsole
from test_console_prompt import make_vm
from test_management_bootstrap import launch


class LoginConsole(PromptConsole):
    """Guest login prompt that accepts only the guest's current password."""

    def __init__(self, guest_password, answer=True, split=False, reprompt=True):
        super().__init__([])
        # reprompt=False: no login prompt after "Login incorrect" until the
        # next input line, as login(1) did in a failed 4.6 restart.
        self.reprompt = reprompt
        self.pending = []
        self.split = split
        self.guest_password = guest_password
        self.answer = answer
        self.state = "user"

    def write(self, data):
        cmd = data.decode().rstrip("\r")
        self.writes.append(cmd)
        if cmd == "":
            self.pending.append(
                b"admin@sonic:~$ " if self.state == "shell" else b"sonic login: "
            )
        elif self.state == "user":
            self.state = "password"
            self.pending.append(b"Password: ")
        elif self.state == "password":
            if not self.answer:
                self.state = "silent"
            elif cmd == self.guest_password:
                self.state = "shell"
                self.pending.append(b"\r\nadmin@sonic:~$ ")
            else:
                self.state = "user"
                if not self.reprompt:
                    self.pending.append(b"\r\nLogin incorrect\r\n")
                elif self.split:
                    self.pending += [b"\r\nLogin incorrect\r\n", b"sonic login: "]
                else:
                    self.pending.append(b"\r\nLogin incorrect\r\nsonic login: ")


class ConsoleLoginTests(unittest.TestCase):
    def login(self, guest_password, password="secret", answer=True, split=False, **kw):
        console = LoginConsole(guest_password, answer, split, **kw)
        vm = make_vm(console)
        vm.password = password
        vm.logger = logging.getLogger("dell_sonic_restart_test")
        vm.console_login()
        return console

    def test_fresh_disk_uses_factory_password(self):
        console = self.login(launch.DEFAULT_PASSWORD)
        self.assertEqual(console.writes, ["", "admin", launch.DEFAULT_PASSWORD, ""])
        self.assertEqual(console.state, "shell")

    def test_reused_disk_retries_with_configured_password(self):
        for split in (False, True):
            with self.subTest(split=split):
                console = self.login("secret", split=split)
                # No input is typed ahead of the guest's new login prompt.
                self.assertEqual(
                    console.writes,
                    ["", "admin", launch.DEFAULT_PASSWORD, "admin", "secret", ""],
                )
                self.assertEqual(console.state, "shell")

    def test_missing_login_prompt_is_elicited_with_a_carriage_return(self):
        console = self.login("secret", reprompt=False)
        self.assertEqual(
            console.writes,
            ["", "admin", launch.DEFAULT_PASSWORD, "", "admin", "secret", ""],
        )
        self.assertEqual(console.state, "shell")

    def test_no_login_prompt_after_carriage_return_exits(self):
        console = LoginConsole("secret", reprompt=False)
        vm = make_vm(console)
        vm.logger = logging.getLogger("dell_sonic_restart_test")
        original = console.write

        def write(data):
            # The guest ignores the carriage return too.
            if data.decode().rstrip("\r") == "" and console.writes:
                console.writes.append("")
                return
            original(data)

        console.write = write
        with self.assertRaises(SystemExit), self.assertLogs(level="ERROR"):
            vm.console_login()
        self.assertEqual(console.writes.count("admin"), 1)

    def test_wrong_passwords_exit(self):
        with self.assertRaises(SystemExit) as cm, self.assertLogs(level="ERROR"):
            self.login("other")
        self.assertEqual(cm.exception.code, 1)

    def test_configured_factory_password_is_tried_once(self):
        with self.assertRaises(SystemExit), self.assertLogs(level="ERROR"):
            console = LoginConsole("other")
            vm = make_vm(console)
            vm.password = launch.DEFAULT_PASSWORD
            try:
                vm.console_login()
            finally:
                self.assertEqual(console.writes.count("admin"), 1)

    def test_no_answer_exits_without_retry(self):
        console = LoginConsole("secret", answer=False)
        vm = make_vm(console)
        with self.assertRaises(SystemExit), self.assertLogs(level="ERROR"):
            vm.console_login()
        self.assertEqual(console.writes.count("admin"), 1)


class DiskImageTests(unittest.TestCase):
    def test_uses_base_image_not_overlay(self):
        vm_init = MagicMock(return_value=None)
        find = MagicMock(return_value="/dell-sonic-4.6.0.qcow2")
        with (
            patch.object(launch.vrnetlab, "find_disk_image", find, create=True),
            patch.object(launch.vrnetlab.VM, "__init__", vm_init, create=True),
        ):
            launch.Dell_Sonic_VM("leaf1", "admin", "secret", "tc")
        find.assert_called_once_with()
        self.assertEqual(vm_init.call_args.kwargs["disk_image"], find.return_value)


if __name__ == "__main__":
    unittest.main()
