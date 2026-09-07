"""VM license installation feature."""

import os
import re
import time

from cli_commands import CommandSequence, CommandSpec, SessionLossAction
from common import BOOTSTRAP_HOSTNAME_REGEX, FOSCliState

from .base import Feature


DEFAULT_LICENSE_STATUS_TIMEOUT_SECONDS = 2 * 60
LICENSE_STATUS_POLL_INTERVAL_SECONDS = 2


def license_status_timeout_seconds():
    value = os.getenv("FOS_LICENSE_STATUS_TIMEOUT_SECONDS")
    if not value:
        return DEFAULT_LICENSE_STATUS_TIMEOUT_SECONDS
    return int(value)


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
        self.commander.submit_block(self, CommandSequence("restore-license", [
            CommandSpec(
                f"exe restore vmlicense tftp appliance.lic {self._tftp_server_ip}",
                completion_states=(FOSCliState.CONFIRMATION,),
                session_loss=SessionLossAction.CONTINUE,
            ),
        ]))

    def on_command_executed(self, command, state):
        if self._phase == "restore" and state == FOSCliState.CONFIRMATION:
            self._phase = "restore-confirmed"
            self.commander.submit_block(self, CommandSequence("confirm-license", [
                CommandSpec(
                    "y",
                    completion_states=(FOSCliState.REBOOTING,),
                    session_loss=SessionLossAction.CONTINUE,
                ),
            ]))
        elif self._phase == "restore-confirmed" and state == FOSCliState.REBOOTING:
            self._phase = "wait-prompt"
            self._wait_for_prompt = True

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
        self._standard_output_active = False

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
        status = self._license_status(bytes(command.output))
        if not status or status.lower() == "pending":
            self._status = status
            self._next_poll = time.monotonic() + LICENSE_STATUS_POLL_INTERVAL_SECONDS
            return
        self._status = status
        self._logger.info(f"License status changed to {status}")
        self._phase = "done"

    @staticmethod
    def _license_status(output):
        match = re.search(rb"(?mi)^License Status:\s*(.+?)\s*\r?$", output)
        if not match:
            match = re.search(rb"(?mi)^License:\s*(.+?)\s*\r?$", output)
        return match.group(1).decode(errors="replace").strip() if match else None

    def on_block_complete(self):
        if self._phase == "done":
            self.commander.feature_complete(self)

    def tick(self):
        if self._phase != "polling" or self._next_poll is None:
            return
        if time.monotonic() >= self._deadline:
            self._logger.warning("License status remained Pending.")
            self._phase = "done"
            self.commander.feature_complete(self)
            return
        if time.monotonic() >= self._next_poll and not self.commander.busy:
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
