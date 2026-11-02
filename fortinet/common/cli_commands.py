"""Command and scope data used by the FortiOS CLI scheduler."""

from dataclasses import dataclass, field
from enum import Enum, auto

from .common import FOSCliState


class SessionLossAction(Enum):
    RESTART_BLOCK = auto()
    COMPLETE_BLOCK = auto()
    CONTINUE = auto()
    VALIDATE = auto()
    FAIL = auto()


class ContextLossAction(Enum):
    RESTART_BLOCK = auto()
    FAIL = auto()


@dataclass(frozen=True, init=False)
class CommandSpec:
    line: str
    completion_states: tuple = ()
    capture_output: bool = False
    suppress_output: bool = False
    session_loss: SessionLossAction = SessionLossAction.RESTART_BLOCK
    expected_completion_state: object = None
    fail_on_error: bool = False
    context_loss: ContextLossAction = ContextLossAction.RESTART_BLOCK
    continuation: bool = False

    def __init__(self, line, completion_states=(), capture_output=False,
                 suppress_output=False, session_loss=SessionLossAction.RESTART_BLOCK,
                 expected_completion_state=None, fail_on_error=False,
                 context_loss=None, continuation=False):
        object.__setattr__(self, "line", line)
        object.__setattr__(self, "completion_states", completion_states)
        object.__setattr__(self, "capture_output", capture_output)
        object.__setattr__(self, "suppress_output", suppress_output)
        object.__setattr__(self, "session_loss", session_loss)
        object.__setattr__(self, "expected_completion_state", expected_completion_state)
        object.__setattr__(self, "fail_on_error", fail_on_error)
        object.__setattr__(self, "continuation", continuation)
        if context_loss is None:
            # Commands that must not replay after logout must not replay after
            # an unexpected scope exit either. Features can override this policy.
            context_loss = (
                ContextLossAction.RESTART_BLOCK
                if session_loss == SessionLossAction.RESTART_BLOCK
                else ContextLossAction.FAIL
            )
        object.__setattr__(self, "context_loss", context_loss)
        self.__post_init__()

    def __post_init__(self):
        if "\r" in self.line or "\n" in self.line:
            raise ValueError("CLI commands must contain exactly one line")

    @property
    def closes_scope(self):
        return not self.continuation and self.line.strip().lower() in {"next", "end"}


@dataclass
class CommandAttempt:
    spec: CommandSpec
    number: int
    session_epoch: int
    output: bytearray = field(default_factory=bytearray)
    prompt_context_before: tuple | None = None
    prompt_context_after: tuple | None = None
    block_index: int | None = None
    retry_count: int = 0

    def on_output(self, output):
        if not (self.spec.capture_output or self.spec.closes_scope):
            return False
        data = bytes(output)
        self.output.extend(data)
        output.discard(data)
        return True


class Scope:
    def __init__(self, value, children=()):
        self.value = value
        self.children = list(children)

    @property
    def open_line(self):
        raise NotImplementedError

    @property
    def close_line(self):
        raise NotImplementedError

    def flatten(self):
        commands = flatten_commands(self.open_line)
        for child in self.children:
            commands.extend(flatten_commands(child))
        commands.append(CommandSpec(self.close_line))
        return commands


class ConfigBlock(Scope):
    @property
    def open_line(self):
        return f"config {self.value}"

    @property
    def close_line(self):
        return "end"


class EditBlock(Scope):
    @property
    def open_line(self):
        return f"edit {self.value}"

    @property
    def close_line(self):
        return "next"


class SetValue:
    """A quoted ``set`` command supporting FortiOS continuation prompts."""

    def __init__(self, field, value, fail_on_error=True, validate_prompt=True):
        self.field = field
        self.value = value
        self.fail_on_error = fail_on_error
        self.validate_prompt = validate_prompt

    def flatten(self):
        lines = str(self.value).strip().splitlines() or [""]
        lines[0] = f'set {self.field} "{lines[0]}'
        lines[-1] += '"'
        return _flatten_quoted_lines(
            lines, fail_on_error=self.fail_on_error,
            validate_prompt=self.validate_prompt,
        )


def _flatten_quoted_lines(lines, *, fail_on_error=False, validate_prompt=True):
    """Send a logical command one line per continuation prompt, without re-quoting."""
    commands = []
    for index, line in enumerate(lines):
        last_line = index == len(lines) - 1
        expected = (
            FOSCliState.CMD_PROMPT if last_line
            else FOSCliState.MULTILINE_PROMPT
        )
        completion_states = ()
        if len(lines) > 1:
            completion_states = (
                (FOSCliState.CMD_PROMPT, FOSCliState.MULTILINE_PROMPT)
                if last_line else
                (FOSCliState.MULTILINE_PROMPT, FOSCliState.CMD_PROMPT)
            )
        commands.append(CommandSpec(
            line,
            completion_states=completion_states,
            capture_output=True,
            expected_completion_state=expected if validate_prompt else None,
            fail_on_error=fail_on_error,
            continuation=index > 0,
        ))
    return commands


class CommandSequence:
    """An unscoped workflow container; it does not emit CLI framing."""

    def __init__(self, name, children=()):
        self.name = name
        self.children = list(children)

    def flatten(self):
        commands = []
        for child in self.children:
            commands.extend(flatten_commands(child))
        return commands


def flatten_commands(value):
    """Flatten command containers and wrap plain CLI lines with defaults."""
    if isinstance(value, str):
        if "\n" in value:
            return _flatten_quoted_lines(value.split("\n"))
        return [CommandSpec(value)]
    if isinstance(value, CommandSpec):
        return [value]
    if isinstance(value, (Scope, CommandSequence, SetValue)):
        return value.flatten()
    raise TypeError(
        "Commands must be strings, CommandSpec instances, or command containers"
    )


@dataclass(frozen=True)
class CleanupAction:
    name: str
    block: object
    on_completion: bool = True
    on_interruption: bool = True
