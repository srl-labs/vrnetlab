"""VM license installation feature."""

import os
import re
import time

from ..cli_commands import CommandSequence, CommandSpec, SessionLossAction
from ..common import BOOTSTRAP_HOSTNAME_REGEX, FOSCliState

from .base import Feature


DEFAULT_LICENSE_STATUS_TIMEOUT_SECONDS = 2 * 60
LICENSE_STATUS_POLL_INTERVAL_SECONDS = 2
LICENSE_SETTLE_SECONDS = 3
LICENSE_WAIT_STATUSES = {"pending"}
LICENSE_GRACE_PERIOD_STATUS = "grace period"
LOG_LICENSE_STATUS_OUTPUT_ENV = "FOS_LOG_LICENSE_STATUS_OUTPUT"

# FortiOS does not print a single success message for "execute restore
# vmlicense"; the only failure signature is "license install failed"
# (e.g. "VM license install failed."). Everything else is success.
LICENSE_FAILURE_PATTERN = re.compile(rb"(?mi)license install failed")


def license_status_timeout_seconds():
    value = os.getenv("FOS_LICENSE_STATUS_TIMEOUT_SECONDS")
    if not value:
        return DEFAULT_LICENSE_STATUS_TIMEOUT_SECONDS
    return int(value)


def wait_for_valid_license():
    return os.getenv("FOS_WAIT_FOR_VALID_LICENSE", "false").strip().lower() == "true"


class SetLicense(Feature):
    """Install a license without waiting for online validation.

    FortiOS license restore reboots the VM and can reset parts of management
    networking.  Online validation must be polled only after post-license
    management repair has run.
    """

    def __init__(self, vm, commander):
        super().__init__(vm, commander, "setup-license")
        self._enabled = os.path.exists("/tftpboot/appliance.lic")
        self._tftp_server_ip = vm.mgmt_gw_ipv4
        self._phase = "restore"
        self._wait_for_prompt = False

    def activate(self):
        if not self._enabled:
            self.commander.feature_complete(self)
            return
        self.vm.driver.set_prompt_patterns(BOOTSTRAP_HOSTNAME_REGEX)
        self._submit_restore()

    def _submit_restore(self):
        # CMD_PROMPT must complete the restore too: when the install fails
        # FortiOS prints the failure and returns to the prompt without ever
        # asking for confirmation, which would otherwise wedge the block.
        self.commander.submit_block(self, CommandSequence("restore-license", [
            CommandSpec(
                f"exe restore vmlicense tftp appliance.lic {self._tftp_server_ip}",
                completion_states=(FOSCliState.CONFIRMATION, FOSCliState.CMD_PROMPT),
                capture_output=True,
                session_loss=SessionLossAction.CONTINUE,
            ),
        ]))

    def on_command_executed(self, command, state):
        self._check_for_failure(command)
        if self._phase == "restore" and state == FOSCliState.CONFIRMATION:
            self._phase = "restore-confirmed"
            self.commander.submit_block(self, CommandSequence("confirm-license", [
                CommandSpec(
                    "y",
                    completion_states=(FOSCliState.REBOOTING,),
                    session_loss=SessionLossAction.CONTINUE,
                ),
            ]))
            return
        if self._phase == "restore-confirmed" and state == FOSCliState.REBOOTING:
            self._phase = "wait-prompt"
            self._wait_for_prompt = True
            return
        # The restore completed somewhere other than the confirmation
        # prompt (a plain prompt instead): the install failed before
        # confirmation. The failure check above already raised on a "fail"
        # word, so fall through to wait-prompt rather than wedging the
        # block.
        self._phase = "wait-prompt"
        self._wait_for_prompt = True

    def on_output(self, output):
        """Raise on a failure reported while a license command runs.

        The confirmation is completed by the reboot, not by the command
        prompt that returns right after "y" and before the reboot starts, so
        a failed install would otherwise only surface once the command
        completed. Watch the streaming output instead.
        """
        if self._phase in ("restore", "restore-confirmed"):
            output = bytes(output)
            if LICENSE_FAILURE_PATTERN.search(output):
                raise RuntimeError(
                    f"VM license install failed: {output!r}"
                )
        return False

    def _check_for_failure(self, command):
        output = bytes(command.output)
        if LICENSE_FAILURE_PATTERN.search(output):
            raise RuntimeError(
                f"VM license install failed: {output!r}"
            )

    def on_block_complete(self):
        if self._phase in ("restore", "restore-confirmed"):
            return
        if self._phase == "wait-prompt":
            if self._wait_for_prompt:
                self._wait_for_prompt = False
                return
            self._phase = "done"
        if self._phase == "done":
            self.commander.feature_complete(self)

    def on_session_loss(self, attempt):
        if attempt.spec.session_loss == SessionLossAction.CONTINUE:
            self._phase = "wait-prompt"
            self._wait_for_prompt = True
        return attempt.spec.session_loss


class WaitForLicenseValidation(Feature):
    """Poll FortiOS until the restored license validates or times out."""

    def __init__(self, vm, commander):
        super().__init__(vm, commander, "license-validation")
        self._enabled = os.path.exists("/tftpboot/appliance.lic")
        self._logger = commander.logger
        self._deadline = None
        self._next_poll = None
        self._phase = "idle"
        self._status = None
        self._status_outputs_logged = set()
        self._standard_output_active = False
        self._settle_until = 0

    @property
    def status(self):
        return self._status

    @property
    def valid(self):
        return self._status is not None and self._status.lower() == "valid"

    def activate(self):
        if not self._enabled:
            self.commander.feature_complete(self)
            return
        self._deadline = time.monotonic() + license_status_timeout_seconds()
        self._phase = "polling"
        self._next_poll = time.monotonic()

    def on_command_executed(self, command, state):
        output = bytes(command.output)
        status = self._license_status(output)
        self._log_status_output(output, status)
        normalized_status = status.lower() if status else None
        if (
            not status
            or normalized_status in LICENSE_WAIT_STATUSES
            or (
                normalized_status == LICENSE_GRACE_PERIOD_STATUS
                and wait_for_valid_license()
            )
        ):
            previous_status = self._status.lower() if self._status else None
            self._status = status
            if normalized_status != previous_status:
                self._logger.info(
                    "License status is %s; continuing validation polls",
                    status or "unavailable",
                )
            if time.monotonic() >= self._deadline:
                raise RuntimeError(
                    "VM license validation timed out with status "
                    f"{status or 'unavailable'}"
                )
            self._next_poll = time.monotonic() + LICENSE_STATUS_POLL_INTERVAL_SECONDS
            return
        self._status = status
        if normalized_status == LICENSE_GRACE_PERIOD_STATUS:
            self._logger.info(
                "Accepting license status %s without waiting for Valid",
                status,
            )
            self._finish_validation()
            return
        if status.lower() != "valid":
            raise RuntimeError(
                f"VM license validation failed: status {status}"
            )
        self._logger.info(f"License status changed to {status}")
        self._finish_validation()

    def _finish_validation(self):
        self._phase = "done"
        self._settle_until = time.monotonic() + LICENSE_SETTLE_SECONDS

    @staticmethod
    def _license_status(output):
        match = re.search(rb"(?mi)^License Status:\s*(.+?)\s*\r?$", output)
        if not match:
            match = re.search(rb"(?mi)^License:\s*(.+?)\s*\r?$", output)
        return match.group(1).decode(errors="replace").strip() if match else None

    def _log_status_output(self, output, status):
        if os.getenv(
            LOG_LICENSE_STATUS_OUTPUT_ENV, "false"
        ).strip().lower() != "true":
            return
        normalized_status = status.lower() if status else None
        if normalized_status in self._status_outputs_logged:
            return
        self._status_outputs_logged.add(normalized_status)
        self._logger.info(
            "Captured get system status output for license status %s:\n%s",
            status or "unavailable",
            output.decode(errors="replace").rstrip(),
        )

    def on_block_complete(self):
        if self._phase != "done":
            return
        if time.monotonic() < self._settle_until:
            return
        self.commander.feature_complete(self)

    def tick(self):
        now = time.monotonic()
        if self._phase == "done":
            if now >= self._settle_until:
                self.commander.feature_complete(self)
            return
        if self._phase != "polling" or self._next_poll is None:
            return
        if now >= self._deadline:
            raise RuntimeError(
                "VM license validation timed out with status "
                f"{self._status or 'unavailable'}"
            )
        if now >= self._next_poll and not self.commander.busy:
            self._next_poll = None
            if self._standard_output_active:
                self._submit_status_poll()
            else:
                self._standard_output_active = True
                self.commander.with_standard_output(self, self._submit_status_poll)

    def _submit_status_poll(self):
        self.commander.submit_block(self, CommandSequence("license-validation", [
            CommandSpec("get system status", capture_output=True, suppress_output=True),
        ]))
