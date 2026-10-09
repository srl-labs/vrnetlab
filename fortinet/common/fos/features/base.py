"""Shared feature lifecycle primitives."""


class Feature:
    def __init__(self, vm, commander, name):
        self.vm = vm
        self.commander = commander
        self.name = name
        self._completed = False
        self._current_attempt = None

    @property
    def completed(self):
        return self._completed

    def mark_completed(self):
        self._completed = True

    @property
    def completion_message(self):
        return None

    def begin_activation(self):
        """Prepare this feature instance for an initial or later command stage."""
        self._completed = False
        self._current_attempt = None

    @property
    def current_attempt(self):
        return self._current_attempt

    @property
    def current_command(self):
        return self._current_attempt.spec if self._current_attempt else None

    @property
    def command_output(self):
        return bytes(self._current_attempt.output) if self._current_attempt else b""

    def activate(self):
        self.commander.feature_complete(self)

    def on_command_dispatched(self, attempt):
        self._current_attempt = attempt

    def on_output(self, output):
        return False

    def on_command_executed(self, command, state):
        pass

    def on_block_complete(self):
        self.commander.feature_complete(self)

    def on_session_loss(self, attempt):
        return attempt.spec.session_loss

    def reversal_blocks(self):
        """Return command blocks that completely reverse this feature's work."""
        return []

    def undo(self):
        """Return a feature stage that reverses this completed feature."""
        return _FeatureUndo(self.vm, self.commander, self)

    @property
    def file_path(self):
        """Return the optional file path watched for this feature."""
        return None

    def on_file_detected(self, path):
        pass

    def on_file_deleted(self, path):
        pass

    def on_file_modified(self, path):
        pass


class StaticFeature(Feature):
    """A feature whose command blocks are known when it is constructed."""

    def __init__(self, vm, commander, name, blocks, on_complete=None):
        super().__init__(vm, commander, name)
        self._blocks = list(blocks)
        self._on_complete = on_complete

    def activate(self):
        self._submit_next()

    def _submit_next(self):
        if self._blocks:
            self.commander.submit_block(self, self._blocks.pop(0))
            return
        if self._on_complete:
            self._on_complete()
        self.commander.feature_complete(self)

    def on_block_complete(self):
        self._submit_next()


class _FeatureUndo(StaticFeature):
    """A scheduled reversal stage created by ``Feature.undo``."""

    def __init__(self, vm, commander, target):
        super().__init__(vm, commander, f"undo-{target.name}", ())
        self._target = target

    def activate(self):
        if not self._target.completed:
            raise RuntimeError(f"Cannot undo incomplete feature {self._target.name}")
        self._blocks = list(self._target.reversal_blocks())
        super().activate()
