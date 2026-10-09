"""Configuration capture feature."""

import os
import json
import re
import secrets
import stat
import time
import uuid
from pathlib import Path

from ..cli_commands import CommandSequence, CommandSpec
from ..config_diff import diff_config

from .base import Feature


class ConfigSaveFeature(Feature):
    """Capture the bootstrap baseline and a later file-triggered config delta.

    get-config wire protocol (host: ``fclab get-config``):

    * The host writes ``{"id": "<request-id>"}`` atomically to the trigger
      path. Any trigger transition (created or modified) is a new request.
    * This feature writes one JSON status record **per request id** under
      ``<status_dir>/<id>.json``, atomically, with
      ``{"id", "status": pending|busy|success|error, "output"?, "error"?}``.
    * The host polls only its own record and never judges a capture by the
      config file's size, so a stale ``current.conf`` can never satisfy a
      new request.
    """

    BASELINE_PATH = "/tmp/initial.conf"
    CURRENT_PATH = "/config/current.conf"
    TRIGGER_PATH = "/get-config"
    STATUS_DIR = "/config/get-config.status.d"
    INVALID_ID = "invalid"
    # Records a live host still needs are younger than its 60 s deadline;
    # sweep only files older than this so a concurrent request's record is
    # never deleted out from under it.
    STATUS_SWEEP_SECONDS = 120.0
    REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")
    ASYNC_STATUS_PATTERNS = (
        re.compile(r"System file integrity .*check failed!"),
        re.compile(r"\*ATTENTION\*: License registration status changed.*"),
    )

    def __init__(self, vm, commander, baseline_path=BASELINE_PATH,
                 current_path=CURRENT_PATH, trigger_path=TRIGGER_PATH,
                 status_dir=None):
        super().__init__(vm, commander, "capture-config")
        self._baseline_path = baseline_path
        self._current_path = current_path
        self._trigger_path = trigger_path
        self._status_dir = status_dir or (
            Path(self.STATUS_DIR) if str(current_path) == self.CURRENT_PATH
            else Path(current_path).with_name("get-config.status.d")
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
        self._handle_request(path)

    def on_file_modified(self, path):
        # The watcher snapshots before running callbacks, so a trigger the
        # host re-creates while this callback is still in flight arrives as
        # "modified" on the next poll — that is a new request, not a no-op.
        self._handle_request(path)

    def _handle_request(self, path):
        try:
            request_id = self._read_request_id(path)
        except FileNotFoundError:
            return
        except (OSError, ValueError, json.JSONDecodeError) as error:
            # The request id is unknown, so the record goes to a fixed name.
            self._write_status_safely(self.INVALID_ID, "error", str(error))
            self._consume_trigger(path)
            return

        if not self.commander.ready or self.commander.busy:
            self.commander.logger.warning("get-config ignored while the CLI is busy")
            self._write_status_safely(request_id, "busy")
            self._consume_trigger(path, request_id)
            return
        # A fresh request is being accepted: stale records from finished
        # requests can go (age-guarded — see _sweep_status_dir).
        self._sweep_status_dir()
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
            self._consume_trigger(path, request_id)
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
            self._consume_trigger(path, request_id)
            return
        if not self.commander.enqueue_runtime_feature(self):
            self._write_status_safely(
                request_id,
                "error",
                "CLI scheduler became busy",
            )
            self._consume_trigger(path, request_id)
            return
        # Consume only after reconnect and enqueue have both succeeded.
        self._consume_trigger(path, request_id)

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
            return uuid.uuid4().hex
        request = json.loads(raw)
        request_id = request.get("id") if isinstance(request, dict) else None
        if (
            not isinstance(request_id, str)
            or not self.REQUEST_ID_RE.fullmatch(request_id)
            # The id derives a per-id record filename; dot-names escape.
            or request_id in (".", "..")
        ):
            raise ValueError("get-config request must contain a valid id")
        return request_id

    def _status_path(self, request_id):
        """Return the record file for one request id (creating the dir)."""
        directory = Path(self._status_dir)
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{request_id}.json"

    def _write_status(self, request_id, status, error=None):
        if request_id is None:
            request_id = self.INVALID_ID
        document = {"id": request_id, "status": status}
        if status == "success":
            document["output"] = Path(self._current_path).name
        if error:
            document["error"] = error
        self.atomic_write(
            self._status_path(request_id),
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

    def _sweep_status_dir(self):
        """Drop status records older than any live host's deadline.

        Runs only on the accepted path (a busy reply must not prune a
        concurrent request's records) and only removes plain files past
        STATUS_SWEEP_SECONDS, so a record another host is still polling is
        never touched. A missing dir is fine: nothing has been accepted yet
        since the container started.
        """
        directory = Path(self._status_dir)
        try:
            entries = list(directory.iterdir())
        except FileNotFoundError:
            return
        except OSError:
            self.commander.logger.warning(
                "Unable to sweep get-config status dir %s", directory
            )
            return
        cutoff = time.time() - self.STATUS_SWEEP_SECONDS
        for entry in entries:
            try:
                if not entry.is_file():
                    continue
                if entry.stat().st_mtime >= cutoff:
                    continue
                entry.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                self.commander.logger.warning(
                    "Unable to remove stale get-config status record %s", entry
                )

    def _consume_trigger(self, path, request_id=None):
        """Unlink the trigger, but only if it is still *this* request's.

        A stale accepted callback can sit in serial-reconnect retries for up
        to a minute; by the time it gets here the host may already have
        written a fresh trigger for a new request. Compare-before-unlink so
        the new request's trigger survives.
        """
        try:
            if request_id is not None:
                try:
                    raw = path.read_text(encoding="utf-8").strip()
                except FileNotFoundError:
                    raw = ""
                if raw:
                    try:
                        request = json.loads(raw)
                        current = request.get("id") if isinstance(request, dict) else None
                    except json.JSONDecodeError:
                        current = None
                    if current != request_id:
                        self.commander.logger.info(
                            "get-config trigger already replaced; leaving it"
                            " for the newer request"
                        )
                        return
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
