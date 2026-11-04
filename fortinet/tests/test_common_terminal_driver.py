"""Serial terminal buffering and FortiOS CLI state-machine tests."""

import logging
import re
import sys
import threading
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from fortinet.common.cli_commands import CommandAttempt, CommandSpec  # noqa: E402
from fortinet.common.common import Credentials, FOSCliState, TRACE_LEVEL  # noqa: E402
from fortinet.common.fos_cli_driver import (  # noqa: E402
    FOSCliDriver,
    KernelPanicError,
)
from fortinet.common.terminal import Data, Terminal, _ByteQueue, terminal_buffer_limit_bytes  # noqa: E402


class ByteQueueTests(unittest.TestCase):
    def test_put_get_close_timeout_and_failure(self):
        queue = _ByteQueue(3)
        queue.put(b"abc")
        self.assertEqual(queue.get_one(), b"abc")
        queue.put(b"def")
        self.assertEqual(queue.get(), b"def")
        self.assertEqual(queue.get(0), b"")
        queue.close()
        queue.put(b"ignored")
        self.assertEqual(queue.get(), b"")
        failed = _ByteQueue(5)
        failed.fail(ValueError("read failed"))
        with self.assertRaisesRegex(ValueError, "read failed"):
            failed.get(0)

    def test_blocked_get_wakes_when_data_arrives(self):
        queue = _ByteQueue(10)
        result = []
        reader = threading.Thread(target=lambda: result.append(queue.get(1)))
        reader.start()
        queue.put(b"ready")
        reader.join(1)
        self.assertEqual(result, [b"ready"])


class TerminalTests(unittest.TestCase):
    def terminal_with_data(self, data, limit=100):
        terminal = Terminal(MagicMock(), MagicMock())
        terminal._buffer_limit = limit
        terminal._read_queue = _ByteQueue(limit)
        terminal._read_queue.put(data)
        terminal._ensure_reader = lambda: None
        return terminal

    def test_data_wrapper_and_expect_earliest_match_preserves_tail(self):
        view = Data(b"hello")
        self.assertTrue(view)
        self.assertEqual(len(view), 5)
        self.assertIn(b"ell", view)
        self.assertEqual(view.decode(), "hello")
        self.assertTrue(view.startswith(b"he"))
        self.assertEqual(view, b"hello")
        terminal = self.terminal_with_data(b"prefix PROMPT trailing")
        index, match, output = terminal.expect([b"trailing", b"PROMPT"], timeout=0.1)
        self.assertEqual(index, 1)
        self.assertEqual(match.group(), b"PROMPT")
        self.assertEqual(bytes(output), b"prefix PROMPT")
        self.assertEqual(bytes(terminal._buffer), b" trailing")
        no_match = self.terminal_with_data(b"abc")
        result = no_match.expect([b"never"], timeout=0)
        self.assertEqual(result[0:2], (-1, None))
        self.assertEqual(bytes(result[2]), b"")

    def test_discard_retained_prefix_and_buffer_limit_error(self):
        terminal = self.terminal_with_data(b"output prompt", limit=20)
        _, _, _ = terminal.expect([b"prompt"], timeout=0.1)
        data = Data(b"output", terminal._discard_prefix)
        terminal._buffer = bytearray(b"output trailing")
        data.discard(b"output")
        self.assertTrue(data.discarded)
        self.assertEqual(bytes(terminal._buffer), b" trailing")
        data.discard(b"wrong")
        terminal._buffer = bytearray(b"123456")
        terminal._buffer_limit = 5
        with self.assertRaisesRegex(RuntimeError, "exceeded 5"):
            terminal._enforce_buffer_limit()
        terminal._logger.error.assert_called_once()

    def test_timeout_preserves_partial_token_for_a_later_read(self):
        terminal = self.terminal_with_data(b"Login incor")
        pattern = rb"(?m)^Login incorrect\r?$"
        index, match, output = terminal.expect([pattern], timeout=0.001)
        self.assertEqual((index, match), (-1, None))
        self.assertEqual(bytes(output), b"Login incor")
        # Inspection without discard keeps the partial token, including when
        # another wait expires without receiving any more bytes.
        _, _, output = terminal.expect([pattern], timeout=0)
        self.assertEqual(bytes(output), b"Login incor")
        terminal._read_queue.put(b"rect\n")
        index, match, output = terminal.expect([pattern], timeout=0.1)
        self.assertEqual(index, 0)
        self.assertEqual(match.group(), b"Login incorrect")
        self.assertEqual(bytes(output), b"Login incorrect")
        self.assertEqual(bytes(terminal._buffer), b"\n")

    def test_timeout_inspection_sees_a_token_completed_by_new_input(self):
        terminal = self.terminal_with_data(b"Verifying pass")
        _, _, output = terminal.expect([b"fw #"], timeout=0.001)
        self.assertIsNone(re.search(rb"Verifying password", bytes(output)))

        terminal._read_queue.put(b"word...\n")
        _, match, output = terminal.expect([b"fw #"], timeout=0.001)
        self.assertIsNone(match)
        self.assertIsNotNone(re.search(rb"Verifying password", bytes(output)))
        self.assertEqual(bytes(terminal._buffer), b"Verifying password...\n")
        output.discard()
        self.assertEqual(bytes(terminal._buffer), b"")

    def test_timeout_capture_consumes_retained_prefix_and_new_bytes(self):
        prefix = b"\nVerifying password...\n"
        chunk = b"admin\nadmin\n"
        terminal = self.terminal_with_data(chunk)
        terminal._buffer.extend(prefix)

        _, _, output = terminal.expect([b"fw #"], timeout=0.001)
        self.assertEqual(bytes(output), prefix + chunk)
        attempt = CommandAttempt(CommandSpec("end"), 1, 0)
        attempt.on_output(output)

        self.assertEqual(bytes(attempt.output), prefix + chunk)
        self.assertEqual(bytes(terminal._buffer), b"")
        terminal._logger.warning.assert_not_called()

    def test_suppression_is_nested_and_restores_context(self):
        @contextmanager
        def context():
            events.append("enter")
            try:
                yield
            finally:
                events.append("exit")
        events = []
        connection = MagicMock()
        connection.suppress_output.return_value = context()
        terminal = Terminal(connection, MagicMock())
        with terminal.suppress_output():
            with terminal.suppress_output():
                self.assertEqual(terminal._output_suppression_depth, 2)
        self.assertEqual(events, ["enter", "exit"])
        self.assertIsNone(terminal._output_suppression_context)

    def test_write_and_close_flushes_writer(self):
        connection = MagicMock()
        terminal = Terminal(connection, MagicMock())
        terminal.write("hello")
        terminal.write(b" world")
        terminal.close()
        written = b"".join(call.args[0] for call in connection.write.call_args_list)
        self.assertEqual(written, b"hello world")
        connection.close.assert_called_once()

    def test_reader_failure_propagates_and_terminal_buffer_setting(self):
        reader = MagicMock()
        reader.read_very_eager.side_effect = OSError("serial failed")
        logger = MagicMock()
        terminal = Terminal(reader, logger)
        stop = threading.Event()
        queue = _ByteQueue(10)
        terminal._read_forever(stop, queue)
        with self.assertRaisesRegex(OSError, "serial failed"):
            queue.get(0)
        logger.exception.assert_called_once()
        with patch.dict("os.environ", {"FOS_TERMINAL_BUFFER_LIMIT_BYTES": "32"}):
            self.assertEqual(terminal_buffer_limit_bytes(), 32)
        with patch.dict("os.environ", {"FTNT_TERMINAL_BUFFER_LIMIT_BYTES": "64", "FOS_TERMINAL_BUFFER_LIMIT_BYTES": "32"}, clear=True):
            self.assertEqual(terminal_buffer_limit_bytes(), 64)
        with patch.dict("os.environ", {"FTNT_TERMINAL_BUFFER_LIMIT_BYTES": "0"}, clear=True):
            with self.assertRaises(ValueError):
                terminal_buffer_limit_bytes()

    def test_raw_serial_log_uses_ftnt_name_and_legacy_fos_alias(self):
        for env_name in ("FTNT_LOG_RAW_SERIAL", "FOS_LOG_RAW_SERIAL"):
            with self.subTest(env_name=env_name):
                terminal = self.terminal_with_data(b"matched")
                with patch.dict("os.environ", {env_name: "true"}, clear=True):
                    terminal.expect([b"matched"], timeout=0.1)
                terminal._logger.log.assert_called_once_with(
                    TRACE_LEVEL,
                    "buffer: bytearray(b'matched')",
                )


class DriverTests(unittest.TestCase):
    def make_driver(self, *, allow_recovery=False):
        terminal, commander, logger = MagicMock(), MagicMock(), MagicMock()
        commander.ready = False
        creds = Credentials("admin", "old")
        blank = MagicMock()
        bootstrap = MagicMock()
        driver = FOSCliDriver(terminal, commander, creds, logger, "new", blank, bootstrap, allow_recovery)
        return driver, terminal, commander, logger, blank, bootstrap

    def test_prompt_patterns_responses_and_state_callbacks(self):
        driver, terminal, commander, logger, blank, bootstrap = self.make_driver()
        driver.set_prompt_patterns("lab[1]")
        self.assertIsNotNone(re.search(driver.state_patterns[FOSCliState.CMD_PROMPT.value], b"lab[1] #"))
        self.assertIsNotNone(re.search(driver.state_patterns[FOSCliState.CMD_PROMPT.value], b"lab[1] #\r"))
        driver._provide_username()
        driver._provide_password()
        driver._provide_current_password()
        driver._provide_new_password()
        driver._confirm_new_password()
        driver._continue_pager()
        self.assertEqual(terminal.write.call_args_list[0].args[0], b"admin\r")
        self.assertEqual(terminal.write.call_args_list[-1].args[0], b" ")
        self.assertTrue(driver._pending_bootstrap_activation)
        driver._credential_accepted()
        bootstrap.assert_called_once()
        driver._process_state(FOSCliState.CMD_PROMPT_ECHO, Data(b"hostname # get"))
        commander.on_prompt_echo.assert_called_once()
        self.assertEqual(commander.on_state.call_count, 0)
        driver._process_state(FOSCliState.CMD_PROMPT, Data(b"hostname #"))
        commander.tick.assert_called()
        driver._last_known_state = FOSCliState.CMD_PROMPT
        driver._current_state = FOSCliState.TN_TIMEOUT
        driver._process_state(FOSCliState.TN_TIMEOUT, Data(b""))
        commander.on_idle_prompt.assert_called_once()
        self.assertTrue(driver.at_command_prompt)

    def test_blank_fallback_and_previous_credential_recovery(self):
        driver, terminal, commander, logger, blank, bootstrap = self.make_driver(allow_recovery=True)
        driver._credential_rejected()
        driver._provide_password()
        blank.assert_called_once()
        self.assertFalse(driver._blank_fallback_available)
        self.assertEqual(terminal.write.call_args.args[0], b"\r")
        driver.remember_current_credentials()
        driver._credential_rejected()
        driver._provide_username()
        driver._provide_password()
        self.assertEqual(terminal.write.call_args_list[-2].args[0], b"admin\r")
        self.assertEqual(terminal.write.call_args_list[-1].args[0], b"old\r")
        driver._credential_accepted()
        commander.record_feature_error.assert_called_once()
        driver._credential_accepted()
        commander.resolve_feature_error.assert_called_once()

    def test_invalid_credential_retry_timeout_is_rejected(self):
        for value in ("-1", "nan", "inf", "-inf", "invalid"):
            with self.subTest(value=value), patch.dict("os.environ", {
                "FTNT_NEW_CREDENTIAL_REJECTED_RETRY_TIMEOUT": value,
            }, clear=True):
                with self.assertRaises(ValueError):
                    self.make_driver()

    def test_rejected_and_recovery_credential_branches(self):
        driver, terminal, commander, logger, blank, bootstrap = self.make_driver()
        driver._initial_login_complete = True
        with self.assertRaisesRegex(RuntimeError, "Credential rejected"):
            driver._credential_rejected()
        driver._license_failed()
        driver._last_known_state = FOSCliState.REBOOTING
        driver._unknown()
        self.assertEqual(driver._idle_spins, 1)
        driver._unknown_started_at = 0
        with patch("fortinet.common.fos_cli_driver.time.monotonic", side_effect=[31, 32, 64, 65, 96, 97]):
            driver._recover_unknown_stall()
            driver._recover_unknown_stall()
            driver._recover_unknown_stall()
        self.assertEqual(terminal.write.call_count, 3)
        driver._unknown_newlines = 3
        driver._unknown_started_at = 1
        with patch("fortinet.common.fos_cli_driver.time.monotonic", return_value=40):
            with self.assertRaisesRegex(RuntimeError, "unknown state"):
                driver._recover_unknown_stall()
        driver._clear_unknown_stall()
        self.assertIsNone(driver._unknown_started_at)

    def test_state_selection_and_timeout_processing(self):
        driver, terminal, commander, logger, *_ = self.make_driver()
        terminal.expect.return_value = (FOSCliState.PROVIDE_USERNAME.value, re.search(b"login", b"login"), Data(b"login"))
        state, output, matched = driver._next_state()
        self.assertEqual(state, FOSCliState.PROVIDE_USERNAME)
        self.assertTrue(matched)
        terminal.expect.return_value = (-1, None, Data(b"noise"))
        self.assertEqual(driver._next_state()[0], FOSCliState.UNKNOWN)
        terminal.expect.return_value = (-1, None, Data(b""))
        self.assertEqual(driver._next_state()[0], FOSCliState.TN_TIMEOUT)
        driver._handle_state(FOSCliState.CREDENTIAL_REJECTED)

    def test_retained_timeout_output_does_not_count_as_fresh_input(self):
        driver, _, _, logger, *_ = self.make_driver()
        terminal = Terminal(MagicMock(), logger)
        terminal._ensure_reader = lambda: None
        terminal._read_queue.put(b"partial token")
        driver._terminal = terminal

        with patch("fortinet.common.fos_cli_driver.EXPECT_TIMEOUT_SECONDS", 0.001):
            state, output, matched = driver._next_state()
            self.assertEqual(state, FOSCliState.UNKNOWN)
            self.assertEqual(bytes(output), b"partial token")
            self.assertFalse(matched)
            state, output, matched = driver._next_state()
            self.assertEqual(state, FOSCliState.TN_TIMEOUT)
            self.assertEqual(bytes(output), b"partial token")
            self.assertFalse(matched)

    def test_kernel_panic_requires_kernel_diagnostic_and_raises(self):
        driver, terminal, commander, *_ = self.make_driver()
        pattern = driver.state_patterns[FOSCliState.KERNEL_PANIC.value]
        panic = (
            b"Kernel panic - not syncing: VFS: Unable to mount root fs\r\n"
            b"CPU: 0 PID: 1 Comm: swapper/0 Not tainted 4.19.13 #1\r\n"
        )

        self.assertIsNone(
            re.search(
                pattern,
                b"Kernel panic - not syncing: VFS: Unable to mount root fs",
            )
        )
        match = re.search(pattern, panic)
        self.assertIsNotNone(match)
        terminal.expect.return_value = (
            FOSCliState.KERNEL_PANIC.value,
            match,
            Data(panic),
        )

        with self.assertRaisesRegex(KernelPanicError, "Kernel panic detected"):
            driver.process_state()

        commander.on_state.assert_called_once_with(FOSCliState.KERNEL_PANIC, Data(panic))


if __name__ == "__main__":
    unittest.main()
