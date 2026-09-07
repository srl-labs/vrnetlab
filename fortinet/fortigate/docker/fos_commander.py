"""Event-driven command scheduler for the FortiOS CLI."""

import datetime
import re
from collections import deque
from contextlib import ExitStack
from dataclasses import dataclass

from cli_commands import (
    CleanupAction,
    CommandAttempt,
    CommandSequence,
    CommandSpec,
    ConfigBlock,
    SessionLossAction,
    flatten_commands,
)
from terminal import Data
from common import FOSCliState, TRACE_LEVEL

DISPATCHABLE_COMPLETION_STATES = {
    FOSCliState.CMD_PROMPT,
    FOSCliState.CONFIRMATION,
}


@dataclass
class _StandardOutputContext:
    feature: object
    callback: object
    phase: str = "inspect"
    restore: bool = False


class FOSCommander:
    _CONSOLE_OUTPUT_PATTERN = re.compile(rb"(?mi)^\s*set output (more|standard)\s*\r?$")
    _CONSOLE_INSPECT_LINE = "show full-configuration system console"
    _CONSOLE_STANDARD_LINE = "set output standard"

    def __init__(self, terminal, logger):
        self.terminal = terminal
        self.logger = logger
        self._features = deque()
        self._active_feature = None
        self._active_commands = ()
        self._pending = deque()
        self._inflight = None
        self._attempt_number = 0
        self._session_epoch = 0
        self._recovering = False
        self._cleanup = deque()
        self._cleanup_actions = {}
        self._in_cleanup = False
        self._standard_output_context = None
        self._suppression = ExitStack()
        self._ready = False
        self._startup_complete = False
        self._completed_messages = deque()
        self._start_time = datetime.datetime.now()

    @property
    def ready(self):
        return self._ready

    @property
    def busy(self):
        return self._inflight is not None or bool(self._pending) or bool(self._cleanup)

    def start(self, features):
        self._features.extend(features)

    def tick(self):
        if self._active_feature and hasattr(self._active_feature, "tick"):
            self._active_feature.tick()

    def submit_block(self, feature, block):
        if feature is not self._active_feature:
            raise RuntimeError("Inactive feature attempted to submit commands")
        if self.busy:
            raise RuntimeError("Cannot replace an active command block")
        commands = tuple(flatten_commands(block))
        self._active_commands = commands
        self._pending = deque(commands)

    def feature_complete(self, feature):
        if feature is not self._active_feature:
            return
        if self.busy:
            raise RuntimeError("Feature completed while commands are still pending")
        feature.mark_completed()
        if feature.completion_message:
            self._completed_messages.append(feature.completion_message)
        self._finish_standard_output_context(feature)
        self._schedule_feature_cleanup(feature, "completion")
        self._active_feature = None
        self._active_commands = ()
        if not self._cleanup:
            self._activate_next_feature()

    def register_cleanup(self, feature, action):
        if not isinstance(action, CleanupAction):
            raise TypeError("cleanup must be a CleanupAction")
        self._cleanup_actions.setdefault(feature, []).append(action)

    def with_standard_output(self, feature, callback):
        """Run a feature callback after standard console output is available."""
        if feature is not self._active_feature:
            raise RuntimeError("Inactive feature requested standard console output")
        if self.busy or self._standard_output_context:
            raise RuntimeError("Cannot start a standard console output context while busy")
        self._standard_output_context = _StandardOutputContext(feature, callback)
        self.submit_block(feature, CommandSequence("console-output-inspect", [
            CommandSpec(self._CONSOLE_INSPECT_LINE, capture_output=True, suppress_output=True),
        ]))

    def _schedule_feature_cleanup(self, feature, trigger):
        actions = self._cleanup_actions.pop(feature, [])
        for action in actions:
            if (trigger == "completion" and action.on_completion) or (
                trigger == "interruption" and action.on_interruption
            ):
                self.logger.debug(f"Scheduling cleanup {action.name} after {trigger}")
                self._cleanup.extend(action.block.flatten())

    def on_output(self, output):
        if not isinstance(output, Data):
            raise TypeError("commander output must be terminal.Data")
        if not output:
            return output
        if self._inflight:
            if self._inflight.on_output(output):
                return output
            if self._active_feature and self._active_feature.on_output(output):
                return output
        self._inspect_output(output)
        return output

    def on_state(self, state, output):
        """Called by the driver for every recognized serial state."""
        self.on_output(output)

        if state == FOSCliState.SESSION_LOST:
            self._handle_session_loss()
            return
        if state == FOSCliState.CREDENTIAL_ACCEPTED:
            self._recovering = False
            return

        if self._inflight and state in (
            self._inflight.spec.completion_states or (FOSCliState.CMD_PROMPT,)
        ):
            self._complete_inflight(state)
            return

        if state == FOSCliState.CMD_PROMPT and not self._recovering:
            self._dispatch_next()

    def on_prompt_echo(self, output):
        """Handle a prompt line that had trailing text after the prompt token."""
        if self.on_output(output).discarded:
            return
        self.logger.debug("Prompt had unexpected trailing output; requesting a clean prompt")
        self.terminal.write(b"\r")

    def _inspect_output(self, output):
        if self._recovering:
            output.discard()

    def on_idle_prompt(self):
        """Dispatch work queued by tick callbacks while already at a prompt."""
        if self._inflight or self._recovering:
            return
        if self._pending or self._cleanup or self._active_feature is None:
            self._dispatch_next()

    def _activate_next_feature(self):
        if self._ready or self._active_feature or self._recovering:
            return
        if not self._features:
            if self._cleanup:
                return
            self._ready = True
            self.terminal.close()
            if not self._startup_complete:
                self._startup_complete = True
                elapsed = datetime.datetime.now() - self._start_time
                self.logger.info(f"Startup complete in {elapsed}")
            else:
                while self._completed_messages:
                    self.logger.info(self._completed_messages.popleft())
            return
        self._active_feature = self._features.popleft()
        self.logger.info(f"Activating feature {self._active_feature.name}")
        self._active_feature.begin_activation()
        self._active_feature.activate()

    def _dispatch_next(self):
        if self._inflight or self._recovering:
            return
        if not self._cleanup and self._active_feature is None:
            self._activate_next_feature()
            if self._active_feature is None:
                return
        if self._cleanup:
            spec = self._cleanup.popleft()
            self._in_cleanup = True
        elif not self._pending:
            self._active_feature.on_block_complete()
            return
        else:
            spec = self._pending.popleft()
            self._in_cleanup = False
        self._attempt_number += 1
        self._inflight = CommandAttempt(spec, self._attempt_number, self._session_epoch)
        if self._active_feature:
            self._active_feature.on_command_dispatched(self._inflight)
        if spec.suppress_output:
            self._suppression.enter_context(self.terminal.suppress_output())
        self.logger.log(
            TRACE_LEVEL,
            f"Dispatching {(self._active_feature.name if self._active_feature else 'cleanup')}/"
            f"{spec.line!r}"
        )
        self.terminal.write(f"{spec.line}\r")

    def _complete_inflight(self, state):
        attempt = self._inflight
        self._inflight = None
        self._suppression.close()
        self._suppression = ExitStack()
        if self._in_cleanup:
            self._in_cleanup = False
            self._dispatch_next()
            return
        if self._is_standard_output_command(attempt.spec):
            self._complete_standard_output_command(attempt)
            if not self._recovering:
                self._dispatch_next()
            return
        self._active_feature.on_command_executed(attempt, state)
        # The callback may have installed another block (confirmation/query path).
        if not self.busy:
            self._active_feature.on_block_complete()
        if not self._recovering and state in DISPATCHABLE_COMPLETION_STATES:
            self._dispatch_next()

    def _is_standard_output_command(self, spec):
        context = self._standard_output_context
        return context is not None and (
            spec.line == self._CONSOLE_INSPECT_LINE
            or (context.phase == "set-standard" and not self._pending)
        )

    def _complete_standard_output_command(self, attempt):
        context = self._standard_output_context
        if attempt.spec.line == self._CONSOLE_INSPECT_LINE:
            match = self._CONSOLE_OUTPUT_PATTERN.search(bytes(attempt.output))
            if not match or match.group(1) == b"standard":
                if not match:
                    self.logger.warning("Could not determine console output mode; assuming standard output")
                self._start_standard_output_callback(context)
                return
            context.phase = "set-standard"
            self.submit_block(context.feature, ConfigBlock("system console", [
                self._CONSOLE_STANDARD_LINE,
            ]))
            return
        if context.phase == "set-standard":
            context.restore = True
            self._start_standard_output_callback(context)

    def _start_standard_output_callback(self, context):
        context.phase = "active"
        context.callback()

    def _finish_standard_output_context(self, feature):
        context = self._standard_output_context
        if context is None or context.feature is not feature:
            return
        self._standard_output_context = None
        if context.restore:
            self._cleanup.extend(ConfigBlock("system console", [
                "set output more",
            ]).flatten())

    def _handle_session_loss(self):
        self._session_epoch += 1
        self._recovering = True
        if not self._inflight:
            return
        attempt = self._inflight
        self._inflight = None
        self._suppression.close()
        self._suppression = ExitStack()
        if self._in_cleanup:
            self._cleanup.appendleft(attempt.spec)
            self._in_cleanup = False
            return
        self._schedule_feature_cleanup(self._active_feature, "interruption")
        action = self._active_feature.on_session_loss(attempt)
        self.logger.info(
            f"Session lost during {self._active_feature.name}/{attempt.spec.line!r}; {action.name.lower()}"
        )
        action_handlers = {
            SessionLossAction.RESTART_BLOCK: lambda: self._restart_active_block(),
            SessionLossAction.COMPLETE_BLOCK: lambda: self._pending.clear(),
            SessionLossAction.CONTINUE: lambda: self._pending.clear(),
            SessionLossAction.FAIL: lambda: self._raise_session_loss(attempt),
            SessionLossAction.VALIDATE: lambda: self._pending.clear(),
        }
        action_handlers[action]()

    def _restart_active_block(self):
        self._pending = deque(self._active_commands)

    @staticmethod
    def _raise_session_loss(attempt):
        raise RuntimeError(f"Session lost during {attempt.spec.line!r}")

    def enqueue_runtime_feature(self, feature):
        return self.enqueue_runtime_features([feature])

    def enqueue_runtime_features(self, features):
        if not self._ready or self.busy:
            return False
        self._ready = False
        self._completed_messages.clear()
        for feature in reversed(features):
            self._features.appendleft(feature)
        return True
