"""Temporary DNS bootstrap feature."""

import re

from cli_commands import CommandSequence, CommandSpec, ConfigBlock

from .base import Feature


class ConfigureMgmtDns(Feature):
    def __init__(self, vm, commander):
        super().__init__(vm, commander, "bootstrap-dns")
        self._phase = "inspect"
        self._previous_protocol = None

    def activate(self):
        self.commander.with_standard_output(self, lambda: self.commander.submit_block(
            self,
            CommandSequence("dns-protocol-inspect", [
                CommandSpec("show full-configuration system dns", capture_output=True, suppress_output=True),
            ]),
        ))

    def on_command_executed(self, command, state):
        if self._phase != "inspect":
            return
        self._previous_protocol = self._protocol_from(bytes(command.output))
        if self._previous_protocol is None:
            self.commander.logger.warning("Could not determine current DNS protocol; undo will unset it")

    def on_block_complete(self):
        if self._phase == "inspect":
            self._phase = "apply"
            self.commander.submit_block(self, ConfigBlock("system dns", [
                "set protocol cleartext",
                f"set primary {self.vm.mgmt_dns_primary}",
                f"set secondary {self.vm.mgmt_dns_secondary}",
            ]))
            return
        self.commander.feature_complete(self)

    @staticmethod
    def _protocol_from(output):
        match = re.search(rb"(?mi)^\s*set protocol\s+(\S+)\s*\r?$", output)
        return match.group(1).decode(errors="replace") if match else None

    def reversal_blocks(self):
        protocol_restore = (
            f"set protocol {self._previous_protocol}"
            if self._previous_protocol else "unset protocol"
        )
        return [ConfigBlock("system dns", [
            protocol_restore,
            "unset primary",
            "unset secondary",
        ])]
