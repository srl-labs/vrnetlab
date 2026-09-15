"""Polling file-event delivery for long-lived features."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileSnapshot:
    exists: bool
    signature: tuple | None = None


class FeatureFileWatcher:
    """Deliver file existence and metadata transitions to declaring features."""

    def __init__(self, features, logger):
        self._logger = logger
        self._features = {}
        for feature in features:
            if feature.file_path is None:
                continue
            path = Path(feature.file_path)
            self._features.setdefault(path, []).append(feature)
        # Deliberately start absent: a trigger present at runtime start is detected.
        self._snapshots = {path: FileSnapshot(False) for path in self._features}

    def poll(self):
        for path, features in self._features.items():
            previous = self._snapshots[path]
            current = self._snapshot(path)
            self._snapshots[path] = current
            event = self._event(previous, current)
            if event is None:
                continue
            for feature in features:
                self._notify(feature, event, path)

    @staticmethod
    def _snapshot(path):
        try:
            stat = path.stat()
        except FileNotFoundError:
            return FileSnapshot(False)
        except OSError:
            return FileSnapshot(False)
        return FileSnapshot(True, (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size))

    @staticmethod
    def _event(previous, current):
        if not previous.exists and current.exists:
            return "detected"
        if previous.exists and not current.exists:
            return "deleted"
        if previous.exists and current.exists and previous.signature != current.signature:
            return "modified"
        return None

    def _notify(self, feature, event, path):
        callbacks = {
            "detected": feature.on_file_detected,
            "deleted": feature.on_file_deleted,
            "modified": feature.on_file_modified,
        }
        try:
            callbacks[event](path)
        except Exception:
            self._logger.exception(
                f"File event {event} for {path} failed in feature {feature.name}"
            )
