"""Scope commit failures are mandatory errors, including best-effort commands."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fortinet.common.cli_commands import (  # noqa: E402
    CleanupAction, CommandSequence, CommandSpec, ConfigBlock, EditBlock,
)
from fortinet.common.common import FOSCliState  # noqa: E402
from fortinet.common.features.base import Feature  # noqa: E402
from fortinet.common.fos_commander import FOSCommander  # noqa: E402
from fortinet.common.terminal import Data  # noqa: E402


class RecordingFeature(Feature):
    def __init__(self, commander, name, commands):
        super().__init__(None, commander, name)
        self.commands = commands
        self.executed = []

    def activate(self):
        self.commander.submit_block(self, CommandSequence(self.name, self.commands))

    def on_command_executed(self, attempt, state):
        self.executed.append(attempt.spec.line)


class ScopeCommitFailureTests(unittest.TestCase):
    @staticmethod
    def prompt(commander, context=None, output=b""):
        scope = f" ({context})" if context else ""
        commander.on_state(FOSCliState.CMD_PROMPT, Data(
            output + f"fw{scope} #".encode()
        ))

    @staticmethod
    def writes(terminal):
        return [call.args[0] for call in terminal.write.call_args_list]

    def test_closing_failure_cannot_be_disabled_or_replayed(self):
        for close in ("next", "end"):
            for capture in (False, True):
                for best_effort in (False, True):
                    with self.subTest(close=close, capture=capture, best_effort=best_effort):
                        terminal = MagicMock()
                        commander = FOSCommander(terminal, MagicMock(), best_effort)
                        feature = RecordingFeature(commander, "commit", [
                            CommandSpec(close, capture_output=capture, fail_on_error=False),
                            "set must-not-run 1",
                        ])
                        feature.on_context_loss = MagicMock()
                        following = RecordingFeature(commander, "following", ["get status"])
                        commander.start([feature, following])
                        self.prompt(commander, "port1")
                        # The failure code can span multiple serial reads.
                        for chunk in (b"Command fa", b"il. Return co", b"de -542.\r\n"):
                            data = Data(chunk)
                            commander.on_output(data)
                            self.assertTrue(data.discarded)
                        if best_effort:
                            self.prompt(commander, "interface")
                            self.assertEqual(self.writes(terminal), [close + "\r", "get status\r"])
                            self.assertEqual(commander.feature_errors[0][0], "commit")
                            self.assertIn("return code -542", commander.feature_errors[0][1])
                        else:
                            with self.assertRaisesRegex(RuntimeError, "return code -542"):
                                self.prompt(commander, "interface")
                            self.assertEqual(self.writes(terminal), [close + "\r"])
                        feature.on_context_loss.assert_not_called()
                        self.assertEqual(feature.executed, [])
                        self.assertFalse(feature.completed)

    def test_closing_failure_with_same_prompt_still_ejects(self):
        for code in ("0", "7", "-61"):
            for suffix in ("", "."):
                with self.subTest(code=code, suffix=suffix):
                    terminal = MagicMock()
                    commander = FOSCommander(terminal, MagicMock())
                    feature = RecordingFeature(commander, "commit", ["next", "end"])
                    commander.start([feature])
                    self.prompt(commander, "port1")
                    with self.assertRaisesRegex(RuntimeError, f"return code {code}"):
                        self.prompt(
                            commander, "port1",
                            f"  Command fail. Return code {code}{suffix}\r\n".encode(),
                        )
                    self.assertEqual(self.writes(terminal), ["next\r"])

    def test_tolerated_set_failure_does_not_poison_successful_scope_close(self):
        for capture in (False, True):
            with self.subTest(capture=capture):
                terminal = MagicMock()
                commander = FOSCommander(terminal, MagicMock())
                feature = RecordingFeature(commander, "configure", [
                    ConfigBlock("system interface", [EditBlock("port1", [
                        CommandSpec("set unsupported 1", capture_output=capture),
                    ])]),
                ])
                commander.start([feature])
                self.prompt(commander)
                self.prompt(commander, "interface")
                self.prompt(commander, "port1")
                self.prompt(commander, "port1", b"Command fail. Return code -61.\r\n")
                self.assertEqual(self.writes(terminal)[-1], "next\r")
                self.prompt(commander, "interface")
                self.prompt(commander)
                self.assertTrue(feature.completed)
                self.assertTrue(commander.ready)
                self.assertEqual(commander.feature_errors, ())

    def test_cleanup_closing_failure_uses_bootstrap_error_policy(self):
        for best_effort in (False, True):
            with self.subTest(best_effort=best_effort):
                terminal = MagicMock()
                commander = FOSCommander(terminal, MagicMock(), best_effort)
                feature = RecordingFeature(commander, "work", ["get initial"])
                following = RecordingFeature(commander, "following", ["get status"])
                commander.register_cleanup(feature, CleanupAction(
                    "close", CommandSequence("close", ["end"]),
                ))
                commander.start([feature, following])
                self.prompt(commander)
                self.prompt(commander)
                self.assertEqual(self.writes(terminal)[-1], "end\r")
                if best_effort:
                    self.prompt(commander, output=b"Command fail. Return code -5.\r\n")
                    self.assertEqual(commander.feature_errors[0][0], "cleanup")
                    self.assertEqual(self.writes(terminal)[-1], "get status\r")
                else:
                    with self.assertRaisesRegex(RuntimeError, "return code -5"):
                        self.prompt(commander, output=b"Command fail. Return code -5.\r\n")
                    self.assertNotIn("get status\r", self.writes(terminal))

    def test_unusable_completion_prompt_cannot_enter_best_effort_wait(self):
        terminal = MagicMock()
        commander = FOSCommander(terminal, MagicMock(), continue_on_error=True)
        feature = RecordingFeature(commander, "commit", [CommandSpec(
            "end", completion_states=(FOSCliState.CONFIRMATION,),
            expected_completion_state=FOSCliState.CMD_PROMPT,
        )])
        commander.start([feature])
        self.prompt(commander, "interface")
        with self.assertRaisesRegex(RuntimeError, "unexpected prompt"):
            commander.on_state(FOSCliState.CONFIRMATION, Data(b"Do you want to continue?"))
        self.assertEqual(self.writes(terminal), ["end\r"])
        self.assertFalse(feature.completed)


if __name__ == "__main__":
    unittest.main()
