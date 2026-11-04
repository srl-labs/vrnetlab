"""Admin credential commits followed by logout and console authentication."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fortinet.common.common import Credentials
from fortinet.common.features.credentials import CredentialsFeature
from fortinet.common.fos_cli_driver import FOSCliDriver
from fortinet.common.fos_commander import FOSCommander
from fortinet.common.terminal import Terminal
from fortinet.tests.test_common_stack_parsing import RecordingFeature, SerialConnection


LOGIN = b"fw login:\r\n"
READY = b"Welcome!\r\nfw #\r\n"
ADMIN_PROMPT = b"fw (admin) #\r\n"


class AdminCredentialsTests(unittest.TestCase):
    def make_stack(self, *, password="newpass", username="admin",
                   auto_logout=None, authenticate=True, reject=False,
                   exit_works=True):
        logger = MagicMock()
        policy_prompt = b"fw (password-policy) #\r\n"
        old_password_responses = [READY]
        if password:
            old_password_responses.append(ADMIN_PROMPT)
        old_password_responses.append(b"Login incorrect\r\n" + LOGIN)
        end_count = 0

        def end_response():
            nonlocal end_count
            end_count += 1
            return LOGIN if auto_logout == "end" and end_count == 2 else b"fw #\r\n"

        login_reply = None if not authenticate else (
            b"Login incorrect\r\n" + LOGIN if reject else READY
        )
        responses = {
            b"admin\r": b"Password:",
            b"oldpass\r": lambda: old_password_responses.pop(0),
            b"config system password-policy\r": policy_prompt,
            b"set status disable\r": policy_prompt,
            b"config system admin\r": ADMIN_PROMPT,
            f"edit {username}\r".encode(): ADMIN_PROMPT,
            b"set accprofile super_admin\r": ADMIN_PROMPT,
            b"next\r": LOGIN if auto_logout == "next" else ADMIN_PROMPT,
            b"end\r": end_response,
            b"exit\r": LOGIN if exit_works else b"fw #\r\n",
            b"probe\r": b"fw #\r\n",
            (password + "\r").encode(): login_reply,
        }
        if username != "admin":
            responses[(username + "\r").encode()] = b"Password:"
        if password:
            responses[f"set password {password}\r".encode()] = (
                b"Please enter current administrator password: "
            )
        else:
            responses[b"unset password\r"] = ADMIN_PROMPT

        connection = SerialConnection(responses)
        terminal = Terminal(connection, logger)
        commander = FOSCommander(terminal, logger)
        credentials = Credentials("admin", "oldpass")
        driver = FOSCliDriver(
            terminal, commander, credentials, logger, "bootstrap",
            MagicMock(), MagicMock(), allow_credential_recovery=True,
        )

        def activate_desired():
            driver.remember_current_credentials()
            credentials.username = username
            credentials.password = password

        vm = SimpleNamespace(
            desired_credentials=Credentials(username, password),
            activate_desired_credentials=activate_desired,
        )
        admin = CredentialsFeature(vm, commander)
        following = RecordingFeature(commander, "following", ["probe"])
        commander.start([admin, following])
        self.addCleanup(lambda: terminal.close() if not connection.closed else None)
        return connection, commander, driver, admin, following

    def run_driver(self, driver):
        with patch("fortinet.common.fos_cli_driver.EXPECT_TIMEOUT_SECONDS", 0.01), \
                patch("fortinet.common.fos_cli_driver.PROCESS_SPIN_SECONDS", 0.15):
            driver.process_state()

    def test_logout_and_relogin_after_committing_credentials(self):
        for username, password in (("admin", "newpass"), ("admin", ""),
                                   ("operator", "newpass")):
            with self.subTest(username=username, password_set=bool(password)):
                conn, cmd, driver, admin, following = self.make_stack(
                    username=username, password=password,
                )
                self.run_driver(driver)
                self.assertTrue(cmd.ready)
                self.assertTrue(admin.completed)
                self.assertTrue(following.completed)
                logout = conn.writes.index(b"exit\r")
                self.assertEqual(conn.writes[logout - 1], b"end\r")
                self.assertEqual(conn.writes[logout + 1:], [
                    (username + "\r").encode(),
                    (password + "\r").encode(),
                    b"probe\r",
                ])
                self.assertEqual(conn.writes.count(b"exit\r"), 1)

    def test_later_features_wait_for_authentication(self):
        conn, cmd, driver, admin, following = self.make_stack(authenticate=False)
        self.run_driver(driver)
        self.assertIn(b"exit\r", conn.writes)
        self.assertIn(b"newpass\r", conn.writes)
        self.assertNotIn(b"probe\r", conn.writes)
        self.assertFalse(admin.completed)
        self.assertFalse(following.completed)
        self.assertFalse(cmd.ready)

    def test_fortios_logout_at_commit_does_not_replay_password_change(self):
        for commit_command in ("next", "end"):
            with self.subTest(commit_command=commit_command):
                conn, cmd, driver, admin, _ = self.make_stack(auto_logout=commit_command)
                self.run_driver(driver)
                self.assertTrue(cmd.ready)
                self.assertTrue(admin.completed)
                self.assertEqual(conn.writes.count(b"set password newpass\r"), 1)
                self.assertEqual(conn.writes.count(b"exit\r"), 1)
                self.assertEqual(conn.writes.count(b"newpass\r"), 2)

    def test_logout_must_return_to_login(self):
        conn, _, driver, admin, _ = self.make_stack(exit_works=False)
        with self.assertRaisesRegex(RuntimeError, "expected PROVIDE_USERNAME"):
            self.run_driver(driver)
        self.assertFalse(admin.completed)
        self.assertNotIn(b"probe\r", conn.writes)

    def test_rejected_credentials_do_not_start_later_features(self):
        conn, _, driver, admin, _ = self.make_stack(reject=True)
        writes_at_wait = []
        with patch("fortinet.common.fos_cli_driver.time.sleep",
                   side_effect=lambda _: writes_at_wait.append(list(conn.writes))) as sleep, \
                self.assertRaisesRegex(RuntimeError, "Credential rejected"):
            self.run_driver(driver)
        sleep.assert_called_once_with(3)
        logout = conn.writes.index(b"exit\r")
        self.assertEqual(writes_at_wait[0][logout + 1:], [
            b"admin\r", b"newpass\r", b"admin\r", b"oldpass\r",
        ])
        self.assertEqual(conn.writes[logout + 1:], [
            b"admin\r", b"newpass\r", b"admin\r", b"oldpass\r",
            b"admin\r", b"newpass\r",
        ])
        self.assertFalse(admin.completed)
        self.assertNotIn(b"probe\r", conn.writes)
        self.assertEqual(conn.writes.count(b"set password newpass\r"), 1)

    def test_retry_desired_credentials_after_previous_credentials_are_rejected(self):
        for username, password, timeout in (
            ("admin", "newpass", None),
            ("admin", "", "0"),
            ("operator", "newpass", "0.25"),
            ("admin", "newpass", "7"),
        ):
            env = {} if timeout is None else {
                "FTNT_NEW_CREDENTIAL_REJECTED_RETRY_TIMEOUT": timeout,
            }
            with self.subTest(username=username, password_set=bool(password), timeout=timeout), \
                    patch.dict("os.environ", env, clear=True):
                conn, cmd, driver, admin, following = self.make_stack(
                    username=username, password=password,
                )
                desired_password = (password + "\r").encode()
                replies = [b"Login incorrect\r\n" + LOGIN, READY]
                conn._responses[desired_password] = lambda: replies.pop(0)
                writes_at_wait = []
                with patch("fortinet.common.fos_cli_driver.time.sleep",
                           side_effect=lambda _: writes_at_wait.append(list(conn.writes))) as sleep:
                    self.run_driver(driver)
                sleep.assert_called_once_with(3 if timeout is None else float(timeout))
                logout = conn.writes.index(b"exit\r")
                desired_username = (username + "\r").encode()
                before_wait = [
                    desired_username, desired_password, b"admin\r", b"oldpass\r",
                ]
                self.assertEqual(writes_at_wait[0][logout + 1:], before_wait)
                self.assertEqual(conn.writes[logout + 1:], before_wait + [
                    desired_username, desired_password, b"probe\r",
                ])
                self.assertTrue(cmd.ready)
                self.assertTrue(admin.completed)
                self.assertTrue(following.completed)
                self.assertFalse(cmd.feature_errors)


if __name__ == "__main__":
    unittest.main()
