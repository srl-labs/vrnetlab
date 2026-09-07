import logging
import re
import time
from common import (
    FOSCliState,
    FOS_CLI_STATE_PATTERNS,
    BOOTSTRAP_HOSTNAME_REGEX,
    PROMPT_CONTEXT_REGEX,
    TRACE_LEVEL,
)


PROCESS_SPIN_SECONDS = 10
EXPECT_TIMEOUT_SECONDS = 1
UNKNOWN_STALL_SECONDS = 30
UNKNOWN_STALL_NEWLINE_LIMIT = 3


class FOSCliDriver:
    """Owns serial reads and delegates command dispatch to the commander."""

    def __init__(
        self,
        terminal,
        commander,
        credentials,
        logger,
        bootstrap_password,
        activate_blank_credentials,
        activate_bootstrap_credentials,
    ):
        self._terminal = terminal
        self._commander = commander
        self._logger = logger
        self._state_patterns = FOS_CLI_STATE_PATTERNS.copy()
        self._last_known_state = FOSCliState.UNKNOWN
        self._last_logged_state = None
        self._idle_spins = 0
        self._credentials = credentials
        self._bootstrap_password = bootstrap_password
        self._activate_blank_credentials = activate_blank_credentials
        self._activate_bootstrap_credentials = activate_bootstrap_credentials
        self._blank_fallback_available = True
        self._send_blank_password = False
        self._initial_login_complete = False
        self._pending_bootstrap_activation = False
        self._unknown_started_at = None
        self._unknown_newlines = 0
        self._state_handlers = {
            FOSCliState.PROVIDE_USERNAME: self._provide_username,
            FOSCliState.PROVIDE_PASSWORD: self._provide_password,
            FOSCliState.CURRENT_PASSWORD: self._provide_current_password,
            FOSCliState.CHANGE_PASSWORD: self._provide_new_password,
            FOSCliState.CHANGE_PASSWORD_CONFIRM: self._confirm_new_password,
            FOSCliState.MORE_PROMPT: self._continue_pager,
            FOSCliState.CREDENTIAL_ACCEPTED: self._credential_accepted,
            FOSCliState.CMD_PROMPT: self._command_prompt,
            FOSCliState.CMD_PROMPT_ECHO: None,
            FOSCliState.CREDENTIAL_REJECTED: self._credential_rejected,
            FOSCliState.LIC_FAIL: self._license_failed,
            FOSCliState.TN_TIMEOUT: self._timeout,
            FOSCliState.UNKNOWN: self._unknown,
        }

    @property
    def state_patterns(self):
        return self._state_patterns

    @property
    def ready(self):
        return self._commander.ready

    def process_state(self):
        spin_start = time.time()
        while not self.ready and time.time() < spin_start + PROCESS_SPIN_SECONDS:
            state, output, matched = self._next_state()
            if output and not matched:
                self._commander.on_output(output)
                if state == FOSCliState.UNKNOWN:
                    self._note_unknown_output()
                output = type(output)(b"")
            if state.value < FOSCliState.UNKNOWN.value:
                self._idle_spins = 0
                self._last_known_state = state
                self._clear_unknown_stall()
                self._log_state(state)
            self._process_state(state, output)

    def _log_state(self, state):
        if state == FOSCliState.CMD_PROMPT and self._last_logged_state == state:
            return
        self._last_logged_state = state
        self._logger.debug(f"ST: {state.name}")

    def _process_state(self, state, output):
        """Handle a CLI state and then let the commander observe it.

        A password confirmation can be followed immediately by a command
        prompt, without ``Welcome!``.  Apply that credential transition
        before the commander dispatches the command associated with the
        prompt.
        """
        if state == FOSCliState.CMD_PROMPT:
            self._handle_state(state)
        if state == FOSCliState.CMD_PROMPT_ECHO:
            self._commander.on_prompt_echo(output)
        else:
            self._commander.on_state(state, output)
        if state != FOSCliState.CMD_PROMPT:
            self._handle_state(state)
        if state == FOSCliState.CMD_PROMPT:
            self._commander.tick()
        elif state == FOSCliState.TN_TIMEOUT and self._last_known_state == FOSCliState.CMD_PROMPT:
            self._commander.tick()
            self._commander.on_idle_prompt()

    def _next_state(self):
        index, match, output = self._terminal.expect(self._state_patterns, EXPECT_TIMEOUT_SECONDS)
        if match:
            return FOSCliState(index), output, True
        return (FOSCliState.TN_TIMEOUT if not output else FOSCliState.UNKNOWN), output, False

    def _handle_state(self, state):
        handler = self._state_handlers.get(state)
        if handler:
            try:
                handler()
            except Exception:
                self._logger.exception("CLI state handler failed for %s", state.name)
                raise

    def _provide_username(self):
        self._respond(FOSCliState.PROVIDE_USERNAME, self._credentials.username)

    def _provide_password(self):
        if self._send_blank_password:
            # The fallback is consumed as soon as it is sent. A rejection of
            # this attempt, or any later login rejection, is fatal.
            self._send_blank_password = False
            self._blank_fallback_available = False
            self._activate_blank_credentials()
            self._respond(FOSCliState.PROVIDE_PASSWORD, b"")
            return
        self._respond(FOSCliState.PROVIDE_PASSWORD, self._credentials.password)

    def _provide_current_password(self):
        self._respond(FOSCliState.CURRENT_PASSWORD, self._credentials.password)

    def _provide_new_password(self):
        self._respond(FOSCliState.CHANGE_PASSWORD, self._bootstrap_password)

    def _confirm_new_password(self):
        self._respond(FOSCliState.CHANGE_PASSWORD_CONFIRM, self._bootstrap_password)
        # Do not update active credentials until FortiOS accepts the
        # confirmation. A rejected confirmation leaves the active password
        # unchanged so that subsequent prompts use the right value.
        self._pending_bootstrap_activation = True

    def _continue_pager(self):
        self._terminal.write(b" ")

    def _credential_accepted(self):
        self._initial_login_complete = True
        self._activate_pending_bootstrap_credentials()

    def _command_prompt(self):
        """Accept password confirmation on versions that skip ``Welcome!``."""
        self._activate_pending_bootstrap_credentials()

    def _activate_pending_bootstrap_credentials(self):
        if self._pending_bootstrap_activation:
            self._activate_bootstrap_credentials()
            self._pending_bootstrap_activation = False

    @staticmethod
    def _license_failed():
        raise RuntimeError("License setup failed")

    def _timeout(self):
        self._idle_spins += 1
        self._recover_unknown_stall()

    def _unknown(self):
        if self._last_known_state == FOSCliState.REBOOTING:
            self._idle_spins += 1

    def _note_unknown_output(self):
        self._unknown_started_at = time.monotonic()

    def _recover_unknown_stall(self):
        if self._unknown_started_at is None:
            return
        if time.monotonic() - self._unknown_started_at < UNKNOWN_STALL_SECONDS:
            return
        if self._unknown_newlines >= UNKNOWN_STALL_NEWLINE_LIMIT:
            raise RuntimeError("CLI remained in unknown state")
        self._unknown_newlines += 1
        self._unknown_started_at = time.monotonic()
        self._logger.warning(
            "CLI stuck in unknown state; sending newline "
            f"({self._unknown_newlines}/{UNKNOWN_STALL_NEWLINE_LIMIT})"
        )
        self._terminal.write(b"\r")

    def _clear_unknown_stall(self):
        self._unknown_started_at = None
        self._unknown_newlines = 0

    def set_prompt_patterns(self, name_pattern):
        if isinstance(name_pattern, str):
            name_pattern = re.escape(name_pattern.encode())
        self._state_patterns[FOSCliState.PROVIDE_USERNAME.value] = rb"(?m)^[ \t]*" + name_pattern + rb"[ \t]+login:[ \t]*"
        self._state_patterns[FOSCliState.CMD_PROMPT.value] = rb"(?m)^[ \t]*" + name_pattern + PROMPT_CONTEXT_REGEX + rb"[ \t]*[#$][ \t]*$"
        self._state_patterns[FOSCliState.CMD_PROMPT_ECHO.value] = rb"(?m)^[ \t]*" + name_pattern + PROMPT_CONTEXT_REGEX + rb"[ \t]*[#$][ \t]*[^\r\n]+(?:\r?\n)?"

    def _credential_rejected(self):
        # A rejected interaction cannot establish the newly selected
        # bootstrap password.  Do not activate it on a later unrelated
        # acceptance or prompt.
        self._pending_bootstrap_activation = False
        if not self._initial_login_complete and self._blank_fallback_available:
            self._send_blank_password = True
            return
        raise RuntimeError("Credential rejected")

    def _respond(self, state, response):
        """Write an interactive response to the CLI."""
        if isinstance(response, str):
            response = response.encode()
        self._logger.log(
            TRACE_LEVEL,
            "Responding to CLI state %s with '%s'",
            state.name,
            response,
        )
        self._terminal.write(bytes(response) + b"\r")
