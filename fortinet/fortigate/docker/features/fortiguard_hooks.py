"""FortiGuard bootstrap hooks feature."""

import os
import re

from cli_commands import CommandSequence, CommandSpec, ConfigBlock

from .base import Feature, StaticFeature


def fortiguard_hooks_enabled():
    return os.getenv("FOS_FORTIGUARD_HOOKS", "false").lower() == "true"


class ConfigureFortiGuardHooks(StaticFeature):
    _GUARD_COMMAND_ENV = "DIAG_2"
    _GUARD_FALLBACK_COMMAND_ENV = "DIAG_1"
    _COMMAND_FAILURE = re.compile(
        rb"(?mi)Unknown action|command (?:parse )?error|Command fail"
    )

    def __init__(self, vm, commander):
        self._enabled = fortiguard_hooks_enabled()
        self._guard_command = os.getenv(self._GUARD_COMMAND_ENV)
        self._guard_fallback_command = os.getenv(self._GUARD_FALLBACK_COMMAND_ENV)
        super().__init__(vm, commander, "fortiguard-hooks", ())

    def activate(self):
        if not self._enabled:
            self.commander.feature_complete(self)
            return
        self._blocks = []
        if self._guard_command:
            self._blocks.append(
                CommandSequence("hooks-guard", [
                    CommandSpec(
                        self._guard_command,
                        capture_output=True,
                        suppress_output=True,
                    ),
                ]),
            )
        self._blocks.append(
            self.fortiguard_block(getattr(self.vm, "fos_version", None))
        )
        self._submit_next()

    @staticmethod
    def fortiguard_block(version):
        update_server_location = (
            "any" if getattr(version, "major", None) == 6 else "automatic"
        )
        return ConfigBlock("system fortiguard", [
            "set fortiguard-anycast disable",
            "unset sdns-server-ip",
            "set fortiguard-server-location automatic",
            f"set update-server-location {update_server_location}",
        ])

    def on_command_executed(self, command, state):
        if not self._guard_command or command.spec.line != self._guard_command:
            return
        failed = self._COMMAND_FAILURE.search(bytes(command.output)) is not None
        if failed and self._guard_fallback_command:
            self.commander.submit_block(self, CommandSequence(
                "hooks-guard-fallback",
                [CommandSpec(
                    self._guard_fallback_command,
                    capture_output=True,
                    suppress_output=True,
                )],
            ))


class ReapplyFortiGuardHooks(Feature):
    """Reapply FortiGuard hook settings after the license reboot."""

    def __init__(self, vm, commander):
        super().__init__(vm, commander, "fortiguard-hooks-after-license")

    def activate(self):
        if not fortiguard_hooks_enabled():
            self.commander.feature_complete(self)
            return
        self.commander.submit_block(
            self,
            CommandSequence("fortiguard-hooks-after-license", [
                ConfigureFortiGuardHooks.fortiguard_block(
                    getattr(self.vm, "fos_version", None)
                ),
                CommandSpec("execute update-now"),
            ]),
        )
