"""Configuration capture feature."""

import os
import json
import re
import secrets
import stat
import uuid
from pathlib import Path

from cli_commands import CommandSequence, CommandSpec
from config_diff import diff_config

from .base import Feature


class ConfigSaveFeature(Feature):
    """Capture the bootstrap baseline and a later file-triggered config delta."""

    BASELINE_PATH = "/tmp/initial.conf"
    CURRENT_PATH = "/config/current.conf"
    TRIGGER_PATH = "/get-config"
    STATUS_PATH = "/config/get-config.status.json"
    ASYNC_STATUS_PATTERNS = (
        re.compile(r"System file integrity .*check failed!"),
        re.compile(r"\*ATTENTION\*: License registration status changed.*"),
    )

    def __init__(self, vm, commander, baseline_path=BASELINE_PATH,
                 current_path=CURRENT_PATH, trigger_path=TRIGGER_PATH,
                 status_path=None):
        super().__init__(vm, commander, "capture-config")
        self._baseline_path = baseline_path
        self._current_path = current_path
        self._trigger_path = trigger_path
        self._status_path = status_path or (
            self.STATUS_PATH if str(current_path) == self.CURRENT_PATH
            else Path(current_path).with_name("get-config.status.json")
        )
        self._stage = "baseline"
        self._request_id = None
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
            request_id = self._read_request_id(path)
        except FileNotFoundError:
            return
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self._write_status_safely(None, "error", str(error))
            self._consume_trigger(path)
            return

        if not self.commander.ready or self.commander.busy:
            self.commander.logger.warning("get-config ignored while the CLI is busy")
            self._write_status_safely(request_id, "error", "CLI scheduler is busy")
            self._consume_trigger(path)
            return
        self._write_status_safely(request_id, "pending")
        try:
            Path(self._current_path).unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            self._write_status_safely(
                request_id,
                "error",
                f"Unable to remove stale output: {error}",
            )
            self._consume_trigger(path)
            return
        self._stage = "current"
        self._request_id = request_id
        try:
            self.vm.connect_serial_console()
        except Exception as error:
            self.commander.logger.exception("Unable to reconnect serial console for get-config")
            self._write_status_safely(
                request_id,
                "error",
                f"Serial reconnect failed: {error}",
            )
            self._consume_trigger(path)
            return
        if not self.commander.enqueue_runtime_feature(self):
            self._write_status_safely(
                request_id,
                "error",
                "CLI scheduler became busy",
            )
            self._consume_trigger(path)
            return
        # Consume only after reconnect and enqueue have both succeeded.
        self._consume_trigger(path)

    def on_command_executed(self, command, state):
        config = self.clean_show_output(bytes(command.output))
        if self._stage == "baseline":
            self.write_config_file(self._baseline_path, config)
            self._completion_message = f"Baseline config saved to {self._baseline_path}"
        else:
            with open(self._baseline_path) as baseline:
                baseline_config = baseline.read()
            try:
                self.write_config_file(
                    self._current_path,
                    self.config_delta(baseline_config, config),
                )
            except Exception as error:
                if self._request_id is not None:
                    self._write_status_safely(self._request_id, "error", str(error))
                raise
            if self._request_id is not None:
                self._write_status_safely(self._request_id, "success")
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
        if content and not content.endswith("\n"):
            content += "\n"
        ConfigSaveFeature.atomic_write(path, content.encode())

    @staticmethod
    def atomic_write(path, content):
        """Atomically replace *path* with secret-safe permissions and durability."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = 0o600
        try:
            # Preserve an existing stricter mode but never widen it.
            mode = stat.S_IMODE(target.stat().st_mode) & 0o600
        except FileNotFoundError:
            pass

        temporary = None
        try:
            for _attempt in range(100):
                temporary = target.with_name(f".{target.name}.{secrets.token_hex(8)}.tmp")
                try:
                    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    break
                except FileExistsError:
                    continue
            else:
                raise FileExistsError(f"Unable to allocate temporary file for {target.name}")

            with os.fdopen(fd, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
                os.fchmod(output.fileno(), mode)
            os.replace(temporary, target)
            temporary = None
            directory_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass

    def _read_request_id(self, path):
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            # Backward-compatible touch trigger. New clients should send an ID
            # and validate that same ID in the status document.
            return str(uuid.uuid4())
        request = json.loads(raw)
        request_id = request.get("id") if isinstance(request, dict) else None
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", request_id):
            raise ValueError("get-config request must contain a valid id")
        return request_id

    def _write_status(self, request_id, status, error=None):
        document = {"id": request_id, "status": status}
        if status == "success":
            document["output"] = Path(self._current_path).name
        if error:
            document["error"] = error
        self.atomic_write(
            self._status_path,
            (json.dumps(document, sort_keys=True) + "\n").encode(),
        )

    def _write_status_safely(self, request_id, status, error=None):
        """Report status without masking the capture operation itself."""
        try:
            self._write_status(request_id, status, error)
        except OSError as status_error:
            self.commander.logger.warning(
                "Unable to write get-config status for request %s: %s",
                request_id,
                status_error,
            )

    def _consume_trigger(self, path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            self.commander.logger.exception("Unable to consume config capture trigger")

    @staticmethod
    def config_delta(baseline, current):
        no_enc = os.getenv("FOS_NO_ENC_CONFIG", "false").lower() in (
            "1", "true", "yes", "on"
        )
        return diff_config(baseline, current, track_encrypted_changes=not no_enc)
