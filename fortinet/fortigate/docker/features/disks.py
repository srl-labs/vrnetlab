"""Additional disk formatting feature."""

import os
import re

from cli_commands import CommandSequence, CommandSpec, SessionLossAction
from common import FOSCliState

from .base import Feature


class FormatDisks(Feature):
    """Format configured non-log disks without replaying a confirmed format."""

    def __init__(self, vm, commander):
        super().__init__(vm, commander, "disk-format")
        self._count = max(0, len(os.getenv("FOS_DISK_SPECS", "").split(",")) - 1)
        self._disk_number = 2
        self._phase = "list"

    def activate(self):
        if not self._count:
            self.commander.feature_complete(self)
            return
        self._submit_list()

    def _submit_list(self):
        self.commander.submit_block(self, CommandSequence("disk-list", [
            CommandSpec("exe disk list", capture_output=True),
        ]))

    def on_command_executed(self, command, state):
        if self._phase == "list":
            disk_ref = self._disk_ref(bytes(command.output))
            self.commander.submit_block(self, CommandSequence("disk-format", [
                CommandSpec(f"exe disk format {disk_ref}", completion_states=(FOSCliState.CONFIRMATION,)),
            ]))
            self._phase = "format"
        elif self._phase == "format" and state == FOSCliState.CONFIRMATION:
            self.commander.submit_block(self, CommandSequence("disk-confirm", [
                CommandSpec("y", completion_states=(FOSCliState.REBOOTING,),
                            session_loss=SessionLossAction.CONTINUE),
            ]))
            self._phase = "reboot"

    def _disk_ref(self, output):
        names = (f"Virtual-Disk{self._disk_number}".encode(), f"HD{self._disk_number}".encode())
        match = re.search(
            rb"(?m)^Disk\s+(?:" + rb"|".join(re.escape(name) for name in names) + rb")\s+ref:\s+(\d+)\b",
            output,
        )
        if not match:
            raise RuntimeError(f"Could not find configured disk #{self._disk_number}")
        return match.group(1).decode()

    def on_block_complete(self):
        if self._phase == "restart-list":
            self._phase = "list"
            self._submit_list()
            return
        if self._phase == "reboot":
            self._count -= 1
            self._disk_number += 1
            if self._count == 0:
                self.commander.feature_complete(self)
                return
            self._phase = "list"
            self._submit_list()

    def on_session_loss(self, attempt):
        if self._phase == "reboot":
            self._count -= 1
            self._disk_number += 1
            self._phase = "restart-list"
        return attempt.spec.session_loss
