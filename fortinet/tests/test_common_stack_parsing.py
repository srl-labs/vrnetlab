"""End-to-end parsing from a serial connection through the FortiOS commander."""

import queue
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from fortinet.common.cli_commands import (  # noqa: E402
    CommandSequence, CommandSpec, ConfigBlock, EditBlock, SetValue,
)
from fortinet.common.common import Credentials, FOSCliState  # noqa: E402
from fortinet.common.features.base import Feature  # noqa: E402
from fortinet.common.fos_cli_driver import FOSCliDriver  # noqa: E402
from fortinet.common.fos_commander import FOSCommander  # noqa: E402
from fortinet.common.terminal import Terminal  # noqa: E402


class SerialConnection:
    """A serial-like connection that answers each write with a byte stream."""

    def __init__(self, responses):
        self._incoming = queue.Queue()
        self._responses = responses
        self.writes = []
        self.closed = False
        self._incoming.put(b"fw login:\r\n")

    def read_very_eager(self):
        try:
            return self._incoming.get_nowait()
        except queue.Empty:
            return b""

    def write(self, data):
        data = bytes(data)
        self.writes.append(data)
        response = self._responses.get(data)
        if callable(response):
            response = response()
        if response:
            chunks = response if isinstance(response, (tuple, list)) else (response,)
            for chunk in chunks:
                if chunk:
                    self._incoming.put(bytes(chunk))

    def close(self):
        self.closed = True

    def suppress_output(self):
        from contextlib import nullcontext
        return nullcontext()


class RecordingFeature(Feature):
    def __init__(self, commander, name, commands):
        super().__init__(None, commander, name)
        self.commands = commands
        self.executed = []
        self.dispatched = []

    def activate(self):
        self.commander.submit_block(self, CommandSequence(self.name, self.commands))

    def on_command_dispatched(self, attempt):
        super().on_command_dispatched(attempt)
        self.dispatched.append(attempt)

    def on_command_executed(self, attempt, state):
        self.executed.append((attempt.spec.line, bytes(attempt.output), state))


class FullStackParsingTests(unittest.TestCase):
    def make_stack(self, features, responses, *, allow_credential_recovery=False):
        logger = MagicMock()
        connection = SerialConnection(responses)
        terminal = Terminal(connection, logger, default_wait="fw #")
        commander = FOSCommander(terminal, logger)
        commander.start(features(commander))
        driver = FOSCliDriver(
            terminal=terminal,
            commander=commander,
            credentials=Credentials("admin", "secret"),
            logger=logger,
            bootstrap_password="Bootstrap1!",
            activate_blank_credentials=MagicMock(),
            activate_bootstrap_credentials=MagicMock(),
            allow_credential_recovery=allow_credential_recovery,
        )
        return connection, terminal, commander, driver

    def test_login_command_echo_multiline_output_and_continuation(self):
        result = b"".join([
            b"fw # show full-configuration system dns\r\n",
            b"config system dns\r\n",
            b"    set protocol dot\r\n",
            b"    set primary 9.9.9.9\r\n",
            b"end\r\n",
            b"fw #\r\n",
        ])
        responses = {
            b"admin\r": b"Password:",
            b"secret\r": b"Welcome!\r\nfw #\r\n",
            b"show full-configuration system dns\r": result,
            b'set comment "first line\r': b">\r\n",
            b"second line\r": b">\r\n",
            b'third line"\r': b"fw #\r\n",
        }
        features = []

        def configured(commander):
            inspect = RecordingFeature(commander, "inspect", [
                CommandSpec("show full-configuration system dns", capture_output=True),
            ])
            multiline = RecordingFeature(commander, "multiline", [
                SetValue("comment", "first line\nsecond line\nthird line"),
            ])
            features.extend([inspect, multiline])
            return features

        connection, terminal, commander, driver = self.make_stack(configured, responses)
        driver.process_state()

        self.assertTrue(commander.ready)
        self.assertTrue(commander.startup_complete)
        self.assertTrue(all(feature.completed for feature in features))
        self.assertTrue(connection.closed)
        self.assertEqual(connection.writes, [
            b"admin\r",
            b"secret\r",
            b"show full-configuration system dns\r",
            b'set comment "first line\r',
            b"second line\r",
            b'third line"\r',
        ])
        self.assertEqual(len(features[0].executed), 1)
        captured = features[0].executed[0][1]
        self.assertIn(b"set protocol dot", captured)
        self.assertIn(b"set primary 9.9.9.9", captured)
        self.assertEqual(features[0].executed[0][2], FOSCliState.CMD_PROMPT)
        self.assertEqual(
            [entry[2] for entry in features[1].executed],
            [FOSCliState.MULTILINE_PROMPT, FOSCliState.MULTILINE_PROMPT, FOSCliState.CMD_PROMPT],
        )
        self.assertTrue(all(attempt.output for attempt in features[1].dispatched))
        self.assertEqual(bytes(terminal._buffer), b"")

    def test_mixed_prompt_context_and_pager_states_reach_feature_callbacks(self):
        responses = {
            b"admin\r": b"Password:",
            b"secret\r": b"Welcome!\r\nfw (global) #\r\n",
            b"get system status\r": (
                b"fw (global) # get system status\r\n"
                b"Version: FortiGate-VM64 v7.4.2,build2571\r\n"
                b"--More--\r\n"
                b"Serial-Number: FGVMTEST\r\n"
                b"fw (global) #\r\n"
            ),
            b" \r": b"fw (global) #\r\n",
        }
        feature_ref = []

        class PagerFeature(RecordingFeature):
            def on_command_executed(self, attempt, state):
                super().on_command_executed(attempt, state)
                self.commander.feature_complete(self)

        def configured(commander):
            feature = PagerFeature(commander, "status", [
                CommandSpec("get system status", capture_output=True),
            ])
            feature_ref.append(feature)
            return [feature]

        connection, _terminal, commander, driver = self.make_stack(configured, responses)
        driver.process_state()

        feature = feature_ref[0]
        self.assertTrue(commander.ready)
        self.assertTrue(feature.completed)
        self.assertEqual(connection.writes, [
            b"admin\r", b"secret\r", b"get system status\r", b" ",
        ])
        captured = feature.executed[0][1]
        self.assertIn(b"FortiGate-VM64 v7.4.2,build2571", captured)
        self.assertIn(b"Serial-Number: FGVMTEST", captured)
        self.assertEqual(feature.executed[0][2], FOSCliState.CMD_PROMPT)
        self.assertEqual(driver._current_state, FOSCliState.CMD_PROMPT)

    def test_aborted_interface_edit_recovers_through_serial_driver(self):
        ip_responses = iter([
            b"abort\r\nfw (Interim)#\r\n",
            b"fw (port1) (Interim)#\r\n",
        ])
        responses = {
            b"admin\r": b"Password:",
            b"secret\r": b"Welcome!\r\nfw (Interim)#\r\n",
            b"config system interface\r": b"fw (interface) (Interim)#\r\n",
            b"edit port1\r": b"fw (port1) (Interim)#\r\n",
            b"set ip 10.0.52.4/24\r": lambda: next(ip_responses),
            b"set allowaccess ping https ssh http\r": b"fw (port1) (Interim)#\r\n",
            b"next\r": b"fw (interface) (Interim)#\r\n",
            b"end\r": b"fw (Interim)#\r\n",
        }
        feature_ref = []

        def configured(commander):
            feature = RecordingFeature(commander, "management", [
                ConfigBlock("system interface", [EditBlock("port1", [
                    "set ip 10.0.52.4/24", "set allowaccess ping https ssh http",
                ])]),
            ])
            feature_ref.append(feature)
            return [feature]

        connection, terminal, commander, driver = self.make_stack(configured, responses)
        try:
            driver.process_state()
            self.assertTrue(commander.ready)
            self.assertTrue(feature_ref[0].completed)
            self.assertEqual(connection.writes, [
                b"admin\r", b"secret\r",
                b"config system interface\r", b"edit port1\r", b"set ip 10.0.52.4/24\r",
                b"config system interface\r", b"edit port1\r", b"set ip 10.0.52.4/24\r",
                b"set allowaccess ping https ssh http\r", b"next\r", b"end\r",
            ])
        finally:
            terminal.close()

    def test_failed_next_ejects_before_end_through_serial_driver(self):
        responses = {
            b"admin\r": b"Password:",
            b"secret\r": b"Welcome!\r\nfw #\r\n",
            b"config system interface\r": b"fw (interface)#\r\n",
            b"edit port1\r": b"fw (port1)#\r\n",
            b"set unsupported 1\r": b"Command fail. Return code -61.\r\nfw (port1)#\r\n",
            b"next\r": (
                b"Command fa", b"il. Return code -5.\r\nfw (port1)#\r\n",
            ),
        }
        feature_ref = []

        def configured(commander):
            feature = RecordingFeature(commander, "management", [
                ConfigBlock("system interface", [EditBlock("port1", ["set unsupported 1"])]),
            ])
            feature_ref.append(feature)
            return [feature]

        connection, terminal, commander, driver = self.make_stack(configured, responses)
        try:
            with self.assertRaisesRegex(RuntimeError, "'next'.*return code -5"):
                driver.process_state()
            self.assertEqual(connection.writes, [
                b"admin\r", b"secret\r", b"config system interface\r",
                b"edit port1\r", b"set unsupported 1\r", b"next\r",
            ])
            self.assertFalse(feature_ref[0].completed)
            self.assertFalse(commander.ready)
        finally:
            terminal.close()


if __name__ == "__main__":
    unittest.main()
