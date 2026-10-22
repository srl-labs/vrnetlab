"""Event-driven command scheduler for the FortiOS CLI."""

import datetime
import re
from collections import deque
from contextlib import ExitStack
from dataclasses import dataclass

from .cli_commands import (
    CleanupAction,
    CommandAttempt,
    CommandSequence,
    CommandSpec,
    ConfigBlock,
    ContextLossAction,
    SessionLossAction,
    flatten_commands,
)
from .terminal import Data
from .common import BOOTSTRAP_HOSTNAME_REGEX, FOSCliState, TRACE_LEVEL

DISPATCHABLE_COMPLETION_STATES = {
    FOSCliState.CMD_PROMPT,
    FOSCliState.CONFIRMATION,
    FOSCliState.MULTILINE_PROMPT,
}
COMMAND_FAILURE_PATTERN = re.compile(
    rb"(?mi)^\s*Command fail\.\s*Return code\s+(-?\d+)"
)
CLI_PROMPT_CONTEXT_PATTERN = re.compile(
    rb"(?m)^[ \t]*" + BOOTSTRAP_HOSTNAME_REGEX
    + rb"(?P<context>(?:[ \t]+\([^)]+\))*)[ \t]*[#$][ \t]*\r?$"
)


@dataclass
class _StandardOutputContext:
    feature: object
    callback: object
    phase: str = "inspect"
    restore: bool = False


class FOSCommander:
    MAX_COMMAND_RECOVERY_RETRIES = 1
    _CONSOLE_OUTPUT_PATTERN = re.compile(rb"(?mi)^\s*set output (more|standard)\s*\r?$")
    _CONSOLE_INSPECT_LINE = "show full-configuration system console"
    _CONSOLE_STANDARD_LINE = "set output standard"

    def __init__(self, terminal, logger, continue_on_error=False):
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
        self._feature_errors = []
        self._authenticated_once = False
        self._completed_messages = deque()
        self._start_time = datetime.datetime.now()
        self._continue_on_error = continue_on_error
        self._pending_feature_error = None
        self._current_state = None
        self._callback_failed = False
        self._prompt_context = None
        self._block_prompt_context = None
        self._recovery_retries = {}
        self._cleanup_retries = {}
        self._replay_context_target = None
        self._context_reset_attempted = False
        self._in_context_reset = False

    def _call_feature(self, callback, *args):
        """Run a feature callback and apply best-effort policy at this boundary."""
        try:
            return callback(*args)
        except Exception as error:
            self._handle_work_error(error, from_callback=True)
            return None

    def _handle_work_error(self, error, *, from_callback=False):
        """Apply the configured failure policy after feature/recovery decisions."""
        if not self._continue_on_error:
            raise error
        if from_callback:
            self.logger.exception("Bootstrap feature callback failed")
        else:
            self.logger.error("Bootstrap work failed: %s", error, exc_info=error)
        self._pending_feature_error = error
        self._callback_failed = True

    def _recover_feature_error(self, *, at_idle_prompt=False):
        error = self._pending_feature_error
        if error is None or (
            self._current_state != FOSCliState.CMD_PROMPT and not at_idle_prompt
        ):
            return False
        failed_work = self.continue_after_error(error, at_command_prompt=True)
        if failed_work is None:
            return False
        self._pending_feature_error = None
        self.logger.error(
            "Bootstrap work %s failed; continuing with remaining features "
            "because FOS_EXIT_ON_BOOTSTRAP_ERROR=false",
            failed_work,
        )
        self._dispatch_next()
        return True

    @property
    def ready(self):
        return self._ready

    @property
    def busy(self):
        return self._inflight is not None or bool(self._pending) or bool(self._cleanup)

    @property
    def startup_complete(self):
        """Whether the initial feature queue has drained, with or without errors."""
        return self._startup_complete

    @property
    def feature_errors(self):
        """Bootstrap feature errors recorded while best-effort mode is active."""
        return tuple(self._feature_errors)

    def continue_after_error(self, error, at_command_prompt):
        """Skip failed work only when the CLI is back at a usable prompt.

        A login rejection after the driver's credential recovery attempts, or
        an error during an interactive prompt, cannot be recovered by sending
        the next feature's commands, so automatic bootstrap must stop.
        """
        if not at_command_prompt or self._ready:
            return None
        if self._active_feature is None and not self._in_cleanup:
            return None

        if self._active_feature is not None:
            failed_name = self._active_feature.name
            self._pending.clear()
            self._active_commands = ()
            self._inflight = None
            self._suppression.close()
            self._suppression = ExitStack()
            self._finish_standard_output_context(self._active_feature)
            self._schedule_feature_cleanup(self._active_feature, "interruption")
            self._active_feature = None
        else:
            failed_name = "cleanup"
            self._inflight = None
            self._suppression.close()
            self._suppression = ExitStack()

        self._in_cleanup = False
        self._replay_context_target = None
        self._in_context_reset = False
        self._cleanup_retries.clear()
        self._feature_errors.append((failed_name, str(error)))
        return failed_name

    def record_feature_error(self, name, error):
        """Record a recoverable bootstrap problem without stopping the queue."""
        self._feature_errors.append((name, str(error)))
        self.logger.info("Using recovery for %s: %s", name, error)

    def resolve_feature_error(self, name, resolution):
        """Clear a recoverable feature error after its condition is resolved."""
        remaining = [
            (feature_name, error)
            for feature_name, error in self._feature_errors
            if feature_name != name
        ]
        resolved_count = len(self._feature_errors) - len(remaining)
        if resolved_count:
            self._feature_errors = remaining
            self.logger.info(
                "Resolved %s bootstrap recovery after %s",
                name,
                resolution,
            )

    def start(self, features):
        self._features.extend(features)

    def tick(self):
        if self._pending_feature_error is not None:
            return
        if self._active_feature and hasattr(self._active_feature, "tick"):
            self._call_feature(self._active_feature.tick)

    def submit_block(self, feature, block):
        if feature is not self._active_feature:
            raise RuntimeError("Inactive feature attempted to submit commands")
        if self.busy:
            raise RuntimeError("Cannot replace an active command block")
        commands = tuple(flatten_commands(block))
        self._active_commands = commands
        self._pending = deque(commands)
        self._block_prompt_context = self._prompt_context
        self._recovery_retries = {}
        self._replay_context_target = None
        self._context_reset_attempted = False

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
            if self._call_feature(self._inflight.on_output, output):
                return output
            if self._active_feature and self._call_feature(self._active_feature.on_output, output):
                return output
        self._inspect_output(output)
        return output

    def on_state(self, state, output):
        """Called by the driver for every recognized serial state."""
        self._current_state = state
        self._callback_failed = False
        if state == FOSCliState.CMD_PROMPT:
            context = self._extract_prompt_context(output)
            if context is not None:
                self._prompt_context = context
            # Some releases return directly to a usable prompt after login,
            # without a Welcome banner. The prompt also ends session recovery.
            self._authenticated_once = True
            self._recovering = False
        if self._pending_feature_error is not None:
            if state == FOSCliState.CMD_PROMPT:
                self._recover_feature_error()
            elif state == FOSCliState.SESSION_LOST:
                self._session_epoch += 1
                self._recovering = True
            elif state == FOSCliState.CREDENTIAL_ACCEPTED:
                self._authenticated_once = True
                self._recovering = False
            return
        self.on_output(output)
        if self._callback_failed:
            if state == FOSCliState.CMD_PROMPT:
                self._recover_feature_error()
            return
        if self._pending_feature_error is not None:
            if state == FOSCliState.CMD_PROMPT:
                self._recover_feature_error()
            return

        if state == FOSCliState.SESSION_LOST:
            self._handle_session_loss()
            return
        if state == FOSCliState.PROVIDE_USERNAME and self._authenticated_once:
            # Password updates can return to login without the explicit
            # SESSION_LOST banner. Requeue the active command block so it is
            # applied after the driver restores an accepted login.
            self._handle_session_loss()
            return
        if state == FOSCliState.CREDENTIAL_ACCEPTED:
            self._authenticated_once = True
            self._recovering = False
            return
        if self._inflight and state in (
            self._inflight.spec.completion_states or (FOSCliState.CMD_PROMPT,)
        ):
            self._complete_inflight(state)
            return

        if state == FOSCliState.CMD_PROMPT and not self._recovering:
            self._dispatch_next()
        if self._callback_failed and state == FOSCliState.CMD_PROMPT:
            self._recover_feature_error()

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
        """Recover errors or dispatch work while idle at a known prompt."""
        if self._inflight or self._recovering:
            return
        if self._pending_feature_error is not None:
            # The driver calls this after a read timeout at a known CLI prompt.
            # on_state has since recorded TN_TIMEOUT, so recover explicitly.
            self._recover_feature_error(at_idle_prompt=True)
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
                if self._feature_errors:
                    failed = ", ".join(name for name, _error in self._feature_errors)
                    self.logger.error(
                        "Bootstrap reached the end with %d feature error(s) in %s; "
                        "failed work: %s",
                        len(self._feature_errors),
                        elapsed,
                        failed,
                    )
                else:
                    self.logger.info(f"Startup complete in {elapsed}")
            else:
                while self._completed_messages:
                    self.logger.info(self._completed_messages.popleft())
            return
        self._active_feature = self._features.popleft()
        self.logger.info(f"Activating feature {self._active_feature.name}")
        self._call_feature(self._active_feature.begin_activation)
        if self._pending_feature_error is None:
            self._call_feature(self._active_feature.activate)

    def _dispatch_next(self):
        if self._inflight or self._recovering or self._pending_feature_error is not None:
            return
        if not self._restore_replay_context():
            return
        if not self._cleanup and self._active_feature is None:
            self._activate_next_feature()
            if self._active_feature is None:
                return
        if self._cleanup:
            spec = self._cleanup.popleft()
            self._in_cleanup = True
            block_index = None
        elif not self._pending:
            self._call_feature(self._active_feature.on_block_complete)
            return
        else:
            spec = self._pending.popleft()
            self._in_cleanup = False
            block_index = len(self._active_commands) - len(self._pending) - 1
        self._dispatch_command(spec, block_index)

    def _dispatch_command(self, spec, block_index=None):
        self._attempt_number += 1
        self._inflight = CommandAttempt(spec, self._attempt_number, self._session_epoch)
        self._inflight.prompt_context_before = self._prompt_context
        self._inflight.block_index = block_index
        self._inflight.retry_count = (
            self._cleanup_retries.get(id(spec), 0)
            if self._in_cleanup else self._recovery_retries.get(block_index, 0)
        )
        if self._active_feature and not self._in_context_reset:
            self._call_feature(self._active_feature.on_command_dispatched, self._inflight)
            if self._pending_feature_error is not None:
                return
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
        attempt.prompt_context_after = self._prompt_context
        if self._in_context_reset:
            self._in_context_reset = False
            self._dispatch_next()
            return
        if state == FOSCliState.CMD_PROMPT and self._prompt_context_changed(attempt):
            self._handle_context_loss(attempt)
            if self._pending_feature_error is not None:
                self._recover_feature_error()
            else:
                self._dispatch_next()
            return
        try:
            self._validate_completion(attempt, state)
        except RuntimeError as error:
            if state != FOSCliState.CMD_PROMPT:
                # An interactive prompt cannot safely advance to another feature.
                raise
            self._handle_work_error(error)
            if self._pending_feature_error is not None:
                self._recover_feature_error()
            return
        if self._in_cleanup:
            self._cleanup_retries.pop(id(attempt.spec), None)
            self._in_cleanup = False
            self._dispatch_next()
            return
        if self._is_standard_output_command(attempt.spec):
            self._complete_standard_output_command(attempt)
            if self._pending_feature_error is not None:
                return
            if not self._recovering:
                self._dispatch_next()
            return
        completed_feature = self._active_feature
        self._call_feature(completed_feature.on_command_executed, attempt, state)
        if self._pending_feature_error is not None:
            return
        # The callback may have installed another block (confirmation/query path).
        if not self.busy and self._active_feature is completed_feature:
            self._call_feature(completed_feature.on_block_complete)
        if not self._recovering and state in DISPATCHABLE_COMPLETION_STATES:
            self._dispatch_next()

    @staticmethod
    def _validate_completion(attempt, state):
        if (attempt.spec.closes_scope or attempt.spec.fail_on_error) and (
            failure := COMMAND_FAILURE_PATTERN.search(bytes(attempt.output))
        ):
            raise RuntimeError(
                f"FortiOS command {attempt.spec.line!r} failed "
                f"(return code {failure.group(1).decode()})"
            )
        expected = attempt.spec.expected_completion_state
        if expected is not None and state != expected:
            raise RuntimeError(
                "CLI command completed at unexpected prompt: "
                f"expected {expected.name}, received {state.name}"
            )

    def _prompt_context_changed(self, attempt):
        before = attempt.prompt_context_before
        after = attempt.prompt_context_after
        return (
            before is not None
            and after is not None
            and before != after
            and not self._command_may_change_context(attempt.spec.line)
        )

    def _handle_context_loss(self, attempt):
        reason = (
            "CLI context changed from "
            f"{self._format_prompt_context(attempt.prompt_context_before)} to "
            f"{self._format_prompt_context(attempt.prompt_context_after)}"
        )
        if self._in_cleanup:
            self._handle_work_error(RuntimeError(
                f"{reason} during cleanup {attempt.spec.line!r}; no feature block to replay"
            ))
            return
        action = self._call_feature(self._active_feature.on_context_loss, attempt)
        if self._pending_feature_error is not None:
            return
        if action == ContextLossAction.RESTART_BLOCK:
            self._restart_active_block(attempt, reason)
        elif action == ContextLossAction.FAIL:
            self._handle_work_error(RuntimeError(
                f"{reason} during {attempt.spec.line!r}; feature declined replay"
            ))
        else:
            self._handle_work_error(ValueError(f"Unsupported context-loss action: {action!r}"))

    def _restore_replay_context(self):
        """Unwind a surviving partial scope once before replaying its openers."""
        target = self._replay_context_target
        if target is None:
            return True
        if self._prompt_context == target:
            self._replay_context_target = None
            return True
        if self._context_reset_attempted:
            self._handle_work_error(RuntimeError(
                "Unable to restore CLI context before replay: expected "
                f"{self._format_prompt_context(target)}, received "
                f"{self._format_prompt_context(self._prompt_context or ())}"
            ))
            if self._pending_feature_error is not None:
                self._recover_feature_error()
            return False
        self._context_reset_attempted = True
        self._in_context_reset = True
        self._dispatch_command(CommandSpec("abort", suppress_output=True))
        return False

    @staticmethod
    def _command_may_change_context(line):
        command = line.strip().lower()
        return (
            command.startswith(("config ", "edit "))
            or command in {"next", "end", "abort", "exit", "quit"}
            or command == "set vdom-mode multi-vdom"
        )

    @staticmethod
    def _extract_prompt_context(output):
        data = bytes(output)
        matches = list(CLI_PROMPT_CONTEXT_PATTERN.finditer(data))
        if not matches:
            return None
        return tuple(
            item.decode(errors="replace")
            for item in re.findall(rb"\(([^)]*)\)", matches[-1].group("context"))
        )

    @staticmethod
    def _format_prompt_context(context):
        return " ".join(f"({item})" for item in context) or "<top-level>"

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
        self._call_feature(context.callback)

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
        if self._in_context_reset:
            # Login may itself restore the target scope. The pending replay
            # stays bounded by the original command's recovery allowance.
            self._in_context_reset = False
            return
        if self._in_cleanup:
            retries = self._cleanup_retries.get(id(attempt.spec), 0)
            if retries >= self.MAX_COMMAND_RECOVERY_RETRIES:
                self._handle_work_error(RuntimeError(
                    f"Session lost repeatedly during cleanup {attempt.spec.line!r}; "
                    "retry limit reached"
                ))
                return
            self._cleanup_retries[id(attempt.spec)] = retries + 1
            self._cleanup.appendleft(attempt.spec)
            self._in_cleanup = False
            return
        self._schedule_feature_cleanup(self._active_feature, "interruption")
        action = self._call_feature(
            self._active_feature.on_session_loss,
            attempt,
        )
        if self._pending_feature_error is not None:
            return
        self.logger.info(
            f"Session lost during {self._active_feature.name}/{attempt.spec.line!r}; {action.name.lower()}"
        )
        action_handlers = {
            SessionLossAction.RESTART_BLOCK: lambda: self._restart_active_block(
                attempt, "session lost"
            ),
            SessionLossAction.COMPLETE_BLOCK: lambda: self._pending.clear(),
            SessionLossAction.CONTINUE: lambda: self._pending.clear(),
            SessionLossAction.FAIL: lambda: self._handle_work_error(
                RuntimeError(f"Session lost during {attempt.spec.line!r}")
            ),
            SessionLossAction.VALIDATE: lambda: self._pending.clear(),
        }
        action_handlers[action]()

    def _restart_active_block(self, attempt, reason):
        retries = self._recovery_retries.get(attempt.block_index, 0)
        if retries >= self.MAX_COMMAND_RECOVERY_RETRIES:
            self._handle_work_error(RuntimeError(
                f"{reason} repeatedly during {attempt.spec.line!r}; "
                f"retry limit reached ({self.MAX_COMMAND_RECOVERY_RETRIES})"
            ))
            return
        self._recovery_retries[attempt.block_index] = retries + 1
        self._pending = deque(self._active_commands)
        self._replay_context_target = self._block_prompt_context
        self._context_reset_attempted = False
        self.logger.warning(
            "%s during %s/%r; replaying block (command recovery %d/%d)",
            reason, self._active_feature.name, attempt.spec.line,
            retries + 1, self.MAX_COMMAND_RECOVERY_RETRIES,
        )

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
