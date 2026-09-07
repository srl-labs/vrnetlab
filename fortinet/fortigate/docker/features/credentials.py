"""Administrator bootstrap feature."""

from cli_commands import CommandSpec, ConfigBlock, EditBlock, SessionLossAction

from .base import StaticFeature


class CredentialsFeature(StaticFeature):
    """Configure the administrator and change credentials after password loss."""

    def __init__(self, vm, commander):
        self._activate_after_admin_commit = False
        self._desired_credentials_activated = False
        credentials = vm.desired_credentials
        admin_children = ["set accprofile super_admin"]
        if credentials.password:
            admin_children.append(CommandSpec(
                f"set password {credentials.password}",
                session_loss=SessionLossAction.CONTINUE,
            ))
        elif credentials.username == "admin":
            admin_children.append(CommandSpec(
                "unset password", session_loss=SessionLossAction.CONTINUE,
            ))
        super().__init__(vm, commander, "admin", [
            ConfigBlock("system password-policy", ["set status disable"]),
            ConfigBlock("system admin", [EditBlock(credentials.username, admin_children)]),
        ], on_complete=lambda: self._activate_desired_credentials())

    def on_session_loss(self, attempt):
        if self._is_password_command(attempt.spec.line):
            self._activate_desired_credentials()
            self._activate_after_admin_commit = False
        return super().on_session_loss(attempt)

    def on_command_executed(self, command, state):
        line = command.spec.line
        if self._is_password_command(line):
            self._activate_after_admin_commit = True
        elif self._activate_after_admin_commit and line == "next":
            self._activate_desired_credentials()
            self._activate_after_admin_commit = False

    @staticmethod
    def _is_password_command(line):
        return line.startswith("set password ") or line == "unset password"

    def _activate_desired_credentials(self):
        if self._desired_credentials_activated:
            return
        self.vm.activate_desired_credentials()
        self._desired_credentials_activated = True
