"""Wait for FortiTokens to be provisioned after license validation."""

import re
import time

from ..cli_commands import CommandSequence, CommandSpec

from .base import Feature


FORTITOKEN_TIMEOUT_SECONDS = 15
FORTITOKEN_POLL_INTERVAL_SECONDS = 2


class WaitForFortiTokens(Feature):
    """Wait briefly for license-provisioned FortiTokens to appear.

    A valid VM license can be reported before FortiOS has created the
    associated FortiToken object.  The object is asynchronous, so absence in
    one ``show`` response is not a bootstrap failure; we poll until the
    bounded deadline and then continue with a warning.
    """

    _TOKEN_CONFIG_PATTERN = re.compile(
        rb"(?mis)^\s*config\s+user\s+fortitoken\s*$.*?"
        rb"^\s*edit\s+[^\r\n]+"
    )

    def __init__(self, vm, commander, license_feature):
        super().__init__(vm, commander, "fortitoken-provisioning")
        self._license_feature = license_feature
        self._logger = commander.logger
        self._deadline = None
        self._next_poll = None
        self._token_found = False

    def activate(self):
        if not getattr(self._license_feature, "valid", False):
            self.commander.feature_complete(self)
            return

        now = time.monotonic()
        self._deadline = now + FORTITOKEN_TIMEOUT_SECONDS
        self._next_poll = now
        self._submit_poll()

    def _submit_poll(self):
        self._next_poll = None
        self.commander.submit_block(
            self,
            CommandSequence("fortitoken-provisioning", [
                CommandSpec(
                    "show user fortitoken",
                    capture_output=True,
                    suppress_output=True,
                ),
            ]),
        )

    @classmethod
    def _tokens_provisioned(cls, output):
        return cls._TOKEN_CONFIG_PATTERN.search(output) is not None

    def on_command_executed(self, command, state):
        if self._tokens_provisioned(bytes(command.output)):
            self._token_found = True
            return
        self._next_poll = time.monotonic() + FORTITOKEN_POLL_INTERVAL_SECONDS

    def on_block_complete(self):
        if self._token_found:
            self._logger.info("FortiTokens provisioned after license validation")
            self.commander.feature_complete(self)

    def tick(self):
        if self.completed or self._deadline is None:
            return

        now = time.monotonic()
        if now >= self._deadline:
            if self.commander.busy:
                return
            self._logger.warning(
                "FortiTokens were not provisioned within %d seconds",
                FORTITOKEN_TIMEOUT_SECONDS,
            )
            self.commander.feature_complete(self)
            return

        if (
            self._next_poll is not None
            and now >= self._next_poll
            and not self.commander.busy
        ):
            self._submit_poll()
