"""Default FortiOS settings feature."""

import os

from cli_commands import ConfigBlock

from .base import StaticFeature


def onboarding_enabled():
    return os.getenv("FOS_ONBOARDING", "false").lower() == "true"


class DefaultConfig(StaticFeature):
    def __init__(self, vm, commander):
        self._hostname_line = f"set hostname {vm.hostname}"
        global_config = ["set admin-scp enable"]
        if onboarding_enabled():
            global_config.extend([
                "set admin-https-redirect disable",
                "set gui-auto-upgrade-setup-warning disable",
            ])
        global_config.append(self._hostname_line)

        super().__init__(vm, commander, "default-config", [
            ConfigBlock("system global", global_config),
            ConfigBlock("system fortiguard", [
                "set auto-join-forticloud disable",
            ])
        ])

    def on_command_executed(self, command, state):
        if command.spec.line == self._hostname_line:
            self.vm.driver.set_prompt_patterns(self.vm.hostname)
