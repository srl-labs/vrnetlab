"""Bounded recovery after a scope exit or an interrupted CLI session."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fortinet.common.cli_commands import (  # noqa: E402
    CleanupAction, CommandSequence, CommandSpec, ConfigBlock, ContextLossAction,
    EditBlock, SessionLossAction,
)
from fortinet.common.common import FOSCliState  # noqa: E402
from fortinet.common.features.base import Feature, StaticFeature  # noqa: E402
from fortinet.common.fos_commander import FOSCommander  # noqa: E402
from fortinet.common.terminal import Data  # noqa: E402


IP_COMMAND = "set ip 10.0.52.4/24"
ACCESS_COMMAND = "set allowaccess ping https ssh http"


class RecordingFeature(Feature):
    def __init__(self, commander, block, context_action=None):
        super().__init__(None, commander, "management")
        self.block = block
        self.context_action = context_action
        self.losses = []
        self.attempts = []
        self.executed = []

    def activate(self):
        self.commander.submit_block(self, self.block)

    def on_command_dispatched(self, attempt):
        super().on_command_dispatched(attempt)
        self.attempts.append(attempt)

    def on_command_executed(self, attempt, state):
        self.executed.append(attempt.spec.line)

    def on_context_loss(self, attempt):
        self.losses.append(attempt)
        if self.context_action is not None:
            return self.context_action
        return super().on_context_loss(attempt)


class RecoveryTests(unittest.TestCase):
    def make_commander(self, *, continue_on_error=False, ip_command=IP_COMMAND,
                       context_action=None, following=False):
        terminal = MagicMock()
        commander = FOSCommander(terminal, MagicMock(), continue_on_error)
        block = ConfigBlock("system interface", [EditBlock("port1", [
            ip_command, ACCESS_COMMAND,
        ])])
        feature = RecordingFeature(commander, block, context_action)
        features = [feature]
        if following:
            features.append(RecordingFeature(
                commander, CommandSequence("following", ["get system status"])
            ))
        commander.start(features)
        return commander, terminal, feature

    def prompt(self, commander, context=None):
        scope = f" ({context})" if context else ""
        commander.on_state(FOSCliState.CMD_PROMPT, Data(
            f"FGT-VM64-KVM-NSVHDM{scope} (Interim)#".encode()
        ))

    def enter_interface(self, commander):
        self.prompt(commander, "interface")
        self.prompt(commander, "port1")

    def finish_interface(self, commander):
        self.prompt(commander, "port1")  # IP succeeds; dispatch allowaccess.
        self.prompt(commander, "port1")  # allowaccess succeeds; dispatch next.
        self.prompt(commander, "interface")  # next succeeds; dispatch end.
        self.prompt(commander)  # end succeeds.

    @staticmethod
    def writes(terminal):
        return [call.args[0] for call in terminal.write.call_args_list]

    def reconnect(self, commander):
        commander.on_state(FOSCliState.CREDENTIAL_ACCEPTED, Data(b"Welcome!"))
        self.prompt(commander)

    def test_scope_exit_replays_openers_before_retrying_the_failed_command(self):
        commander, terminal, feature = self.make_commander()
        self.prompt(commander)
        self.enter_interface(commander)
        self.prompt(commander)  # set ip unexpectedly returns to the VDOM shell.
        self.assertEqual(self.writes(terminal), [
            "config system interface\r", "edit port1\r", IP_COMMAND + "\r",
            "config system interface\r",
        ])
        self.assertNotIn(IP_COMMAND, feature.executed)
        self.assertEqual(feature.losses[0].prompt_context_before, ("port1", "Interim"))
        self.assertEqual(feature.losses[0].prompt_context_after, ("Interim",))
        self.enter_interface(commander)
        self.finish_interface(commander)
        ip_attempts = [item for item in feature.attempts if item.spec.line == IP_COMMAND]
        self.assertEqual([item.retry_count for item in ip_attempts], [0, 1])
        self.assertTrue(feature.completed)
        self.assertTrue(commander.ready)

    def test_repeated_context_loss_obeys_retry_limit_and_bootstrap_error_policy(self):
        for best_effort in (False, True):
            with self.subTest(best_effort=best_effort):
                commander, terminal, feature = self.make_commander(
                    continue_on_error=best_effort, following=True,
                )
                self.prompt(commander)
                self.enter_interface(commander)
                self.prompt(commander)
                self.enter_interface(commander)
                if best_effort:
                    self.prompt(commander)
                    self.assertIn("retry limit", commander.feature_errors[0][1])
                    self.assertEqual(self.writes(terminal)[-1], "get system status\r")
                else:
                    with self.assertRaisesRegex(RuntimeError, "retry limit"):
                        self.prompt(commander)
                self.assertEqual(self.writes(terminal).count(IP_COMMAND + "\r"), 2)
                self.assertNotIn(ACCESS_COMMAND + "\r", self.writes(terminal))
                self.assertFalse(feature.completed)

    def test_session_and_context_loss_share_the_same_command_retry_allowance(self):
        commander, terminal, feature = self.make_commander()
        self.prompt(commander)
        self.enter_interface(commander)
        commander.on_state(FOSCliState.SESSION_LOST, Data(b"session lost"))
        self.reconnect(commander)
        self.enter_interface(commander)
        with self.assertRaisesRegex(RuntimeError, "retry limit"):
            self.prompt(commander)
        self.assertEqual(self.writes(terminal).count(IP_COMMAND + "\r"), 2)
        self.assertEqual(feature.losses[0].retry_count, 1)

    def test_repeated_session_loss_waits_for_login_before_best_effort_skip(self):
        commander, terminal, _feature = self.make_commander(
            continue_on_error=True, following=True,
        )
        self.prompt(commander)
        self.enter_interface(commander)
        commander.on_state(FOSCliState.SESSION_LOST, Data(b"session lost"))
        self.reconnect(commander)
        self.enter_interface(commander)
        commander.on_state(FOSCliState.SESSION_LOST, Data(b"session lost again"))
        self.assertNotIn("get system status\r", self.writes(terminal))
        self.reconnect(commander)
        self.assertEqual(self.writes(terminal)[-1], "get system status\r")
        self.assertIn("retry limit", commander.feature_errors[0][1])

    def test_login_without_welcome_resumes_replay_and_exhaustion_handling(self):
        commander, terminal, _feature = self.make_commander(
            continue_on_error=True, following=True,
        )
        self.prompt(commander)
        self.enter_interface(commander)
        commander.on_state(FOSCliState.SESSION_LOST, Data(b"session lost"))
        self.prompt(commander)  # Successful login returns a prompt, no Welcome.
        self.assertEqual(self.writes(terminal)[-1], "config system interface\r")
        self.enter_interface(commander)
        commander.on_state(FOSCliState.SESSION_LOST, Data(b"session lost again"))
        self.assertNotIn("get system status\r", self.writes(terminal))
        self.prompt(commander)  # Exhaustion is handled at this usable prompt.
        self.assertEqual(self.writes(terminal)[-1], "get system status\r")
        self.assertIn("retry limit", commander.feature_errors[0][1])

    def test_each_command_position_gets_one_recovery(self):
        commander, terminal, feature = self.make_commander()
        self.prompt(commander)
        self.enter_interface(commander)
        self.prompt(commander)  # First loss: IP command.
        self.enter_interface(commander)
        self.prompt(commander, "port1")  # IP succeeds; dispatch allowaccess.
        self.prompt(commander)  # Second loss: allowaccess command.
        self.enter_interface(commander)
        self.finish_interface(commander)
        self.assertTrue(feature.completed)
        self.assertEqual(self.writes(terminal).count(IP_COMMAND + "\r"), 3)
        self.assertEqual(self.writes(terminal).count(ACCESS_COMMAND + "\r"), 2)

    def test_feature_can_decline_replay_without_running_session_loss_callback(self):
        for explicit_policy in (False, True):
            with self.subTest(explicit_policy=explicit_policy):
                spec = CommandSpec(IP_COMMAND, session_loss=SessionLossAction.CONTINUE)
                commander, terminal, feature = self.make_commander(
                    ip_command=IP_COMMAND if explicit_policy else spec,
                    context_action=ContextLossAction.FAIL if explicit_policy else None,
                )
                feature.on_session_loss = MagicMock()
                self.prompt(commander)
                self.enter_interface(commander)
                with self.assertRaisesRegex(RuntimeError, "feature declined replay"):
                    self.prompt(commander)
                feature.on_session_loss.assert_not_called()
                self.assertEqual(self.writes(terminal).count(IP_COMMAND + "\r"), 1)

    def test_explicit_context_policy_can_allow_replay_independently_of_logout_policy(self):
        spec = CommandSpec(
            IP_COMMAND, session_loss=SessionLossAction.CONTINUE,
            context_loss=ContextLossAction.RESTART_BLOCK,
        )
        commander, _terminal, feature = self.make_commander(ip_command=spec)
        feature.on_session_loss = MagicMock()
        self.prompt(commander)
        self.enter_interface(commander)
        self.prompt(commander)
        self.enter_interface(commander)
        self.finish_interface(commander)
        self.assertTrue(feature.completed)
        feature.on_session_loss.assert_not_called()

    def test_partial_scope_is_aborted_once_before_replay(self):
        commander, terminal, feature = self.make_commander()
        self.prompt(commander)
        self.enter_interface(commander)
        self.prompt(commander, "interface")  # Only the edit context disappeared.
        self.assertEqual(self.writes(terminal)[-1], "abort\r")
        self.prompt(commander)
        self.assertEqual(self.writes(terminal)[-1], "config system interface\r")
        self.enter_interface(commander)
        self.finish_interface(commander)
        self.assertTrue(feature.completed)
        self.assertNotIn("abort", feature.executed)
        self.assertNotIn("abort", [item.spec.line for item in feature.attempts])

    def test_failed_context_reset_does_not_loop(self):
        commander, terminal, _feature = self.make_commander()
        self.prompt(commander)
        self.enter_interface(commander)
        self.prompt(commander, "interface")
        with self.assertRaisesRegex(RuntimeError, "Unable to restore CLI context"):
            self.prompt(commander, "interface")
        self.assertEqual(self.writes(terminal).count("abort\r"), 1)
        self.assertEqual(self.writes(terminal).count(IP_COMMAND + "\r"), 1)

    def test_session_loss_during_context_reset_resumes_after_login(self):
        commander, terminal, feature = self.make_commander()
        self.prompt(commander)
        self.enter_interface(commander)
        self.prompt(commander, "interface")
        commander.on_state(FOSCliState.SESSION_LOST, Data(b"session lost"))
        self.reconnect(commander)
        self.enter_interface(commander)
        self.finish_interface(commander)
        self.assertTrue(feature.completed)
        self.assertEqual(self.writes(terminal).count("abort\r"), 1)

    def test_cleanup_session_loss_is_bounded(self):
        terminal = MagicMock()
        commander = FOSCommander(terminal, MagicMock())
        feature = RecordingFeature(commander, CommandSequence("work", ["get status"]))
        commander.register_cleanup(feature, CleanupAction(
            "cleanup", CommandSequence("cleanup", ["get cleanup"]),
        ))
        commander.start([feature])
        self.prompt(commander)
        self.prompt(commander)  # Begin cleanup after the feature completes.
        commander.on_state(FOSCliState.SESSION_LOST, Data(b"session lost"))
        self.reconnect(commander)
        with self.assertRaisesRegex(RuntimeError, "retry limit"):
            commander.on_state(FOSCliState.SESSION_LOST, Data(b"session lost"))
        self.assertEqual(self.writes(terminal).count("get cleanup\r"), 2)

    def test_newly_submitted_block_resets_retry_allowance(self):
        terminal = MagicMock()
        commander = FOSCommander(terminal, MagicMock())
        feature = StaticFeature(None, commander, "two-blocks", [
            ConfigBlock("system global", ["set alias first"]),
            ConfigBlock("system global", ["set alias second"]),
        ])
        commander.start([feature])
        self.prompt(commander)
        for _index in range(2):
            self.prompt(commander, "global")
            self.prompt(commander)  # Lost context: retry this block.
            self.prompt(commander, "global")
            self.prompt(commander, "global")
            self.prompt(commander)  # End this block and start the next one.
        self.assertTrue(feature.completed)
        self.assertEqual(self.writes(terminal).count("set alias first\r"), 2)
        self.assertEqual(self.writes(terminal).count("set alias second\r"), 2)


if __name__ == "__main__":
    unittest.main()
