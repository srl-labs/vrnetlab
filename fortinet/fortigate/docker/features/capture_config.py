"""Configuration capture feature."""

import os
import re

from cli_commands import CommandSequence, CommandSpec
from config_diff import diff_config

from .base import Feature


class ConfigSaveFeature(Feature):
    """Capture the bootstrap baseline and a later file-triggered config delta."""

    BASELINE_PATH = "/tmp/initial.conf"
    CURRENT_PATH = "/config/current.conf"
    TRIGGER_PATH = "/get-config"
    ASYNC_STATUS_PATTERNS = (
        re.compile(r"System file integrity .*check failed!"),
        re.compile(r"\*ATTENTION\*: License registration status changed.*"),
    )

    def __init__(self, vm, commander, baseline_path=BASELINE_PATH,
                 current_path=CURRENT_PATH, trigger_path=TRIGGER_PATH):
        super().__init__(vm, commander, "capture-config")
        self._baseline_path = baseline_path
        self._current_path = current_path
        self._trigger_path = trigger_path
        self._stage = "baseline"
        self._completion_message = None

    @property
    def completion_message(self):
        return self._completion_message

    @property
    def file_path(self):
        return self._trigger_path

    def activate(self):
        self._completion_message = None
        self.commander.with_standard_output(self, lambda: self.commander.submit_block(
            self,
            CommandSequence(f"{self.name}-{self._stage}", [
                CommandSpec("show", capture_output=True, suppress_output=True),
            ]),
        ))

    def on_file_detected(self, path):
        try:
            path.unlink()
        except FileNotFoundError:
            return
        except OSError:
            self.commander.logger.exception(f"Unable to consume config capture trigger {path}")
            return

        if not self.commander.ready or self.commander.busy:
            self.commander.logger.warning("get-config ignored while the CLI is busy")
            return
        self._stage = "current"
        try:
            self.vm.connect_serial_console()
        except Exception:
            self.commander.logger.exception("Unable to reconnect serial console for get-config")
            return
        if not self.commander.enqueue_runtime_feature(self):
            self.commander.logger.warning("get-config ignored while the CLI is busy")

    def on_command_executed(self, command, state):
        config = self.clean_show_output(bytes(command.output))
        if self._stage == "baseline":
            self.write_config_file(self._baseline_path, config)
            self._completion_message = f"Baseline config saved to {self._baseline_path}"
        else:
            with open(self._baseline_path) as baseline:
                baseline_config = baseline.read()
            self.write_config_file(
                self._current_path,
                self.config_delta(baseline_config, config),
            )
            self._completion_message = f"Config saved to {self._current_path}"

    def on_block_complete(self):
        self.commander.feature_complete(self)

    @staticmethod
    def clean_show_output(output):
        """Remove terminal artifacts from this feature's FortiOS ``show`` output."""
        config = output.decode(errors="replace") if isinstance(output, bytes) else output
        config = config.replace("\r\n", "\n").replace("\r", "\n").replace("^H", "")
        config = re.sub(r"\x08+", "", config)
        config = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", config)
        lines = [
            line for line in config.splitlines()
            if not ConfigSaveFeature._is_metadata_line(line)
        ]
        if lines and lines[0].strip() == "show":
            lines = lines[1:]
        if lines and re.search(r"(?:\([^)]*\))?\s*[#$]\s*$", lines[-1]):
            lines.pop()
        return "\n".join(lines).strip()

    @staticmethod
    def _is_metadata_line(line):
        stripped = line.strip()
        return (
            stripped.startswith("#")
            or any(pattern.fullmatch(stripped) for pattern in ConfigSaveFeature.ASYNC_STATUS_PATTERNS)
        )

    @staticmethod
    def write_config_file(path, content):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as config_file:
            config_file.write(content)
            if content and not content.endswith("\n"):
                config_file.write("\n")

    @staticmethod
    def config_delta(baseline, current):
        no_enc = os.getenv("FOS_NO_ENC_CONFIG", "false").lower() in (
            "1", "true", "yes", "on"
        )
        return diff_config(baseline, current, track_encrypted_changes=not no_enc)
