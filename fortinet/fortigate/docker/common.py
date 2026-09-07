from enum import IntEnum, auto
from functools import wraps
from time import perf_counter


TRACE_LEVEL = 9


class FOSCliState(IntEnum):
    PROVIDE_USERNAME = 0
    PROVIDE_PASSWORD = auto()
    CHANGE_PASSWORD = auto()
    CREDENTIAL_REJECTED = auto()
    CREDENTIAL_ACCEPTED = auto()
    LIC_FAIL = auto()
    CMD_PROMPT = auto()
    CMD_PROMPT_ECHO = auto()
    SHUTTING_DOWN = auto()
    REBOOTING = auto()
    SESSION_LOST = auto()
    CURRENT_PASSWORD = auto()
    CONFIRMATION = auto()
    CHANGE_PASSWORD_CONFIRM = auto()
    MORE_PROMPT = auto()
    UNKNOWN = auto()  # Non-patterns from here on.
    TN_TIMEOUT = auto()


GENERIC_HOSTNAME_REGEX = rb"[A-Za-z0-9_.-]+"
DEFAULT_HOSTNAME_REGEX = rb"[A-Za-z0-9_.-]+(?:-VM64)?-KVM(?:-[A-Za-z0-9]*)?"
LICENSED_HOSTNAME_REGEX = rb"[A-Z][A-Z0-9]{7,}"
BOOTSTRAP_HOSTNAME_REGEX = (
    rb"(?:" + GENERIC_HOSTNAME_REGEX + rb"|" + DEFAULT_HOSTNAME_REGEX + rb"|" + LICENSED_HOSTNAME_REGEX + rb")"
)
PROMPT_CONTEXT_REGEX = rb"(?:[ \t]+\([^)]+\))*"
DEFAULT_HOSTNAME_PROMPT = rb"(?m)^[ \t]*" + BOOTSTRAP_HOSTNAME_REGEX + PROMPT_CONTEXT_REGEX + rb"[ \t]*[#$][ \t]*$"
DEFAULT_HOSTNAME_PROMPT_ECHO = rb"(?m)^[ \t]*" + BOOTSTRAP_HOSTNAME_REGEX + PROMPT_CONTEXT_REGEX + rb"[ \t]*[#$][ \t]*[^\r\n]+(?:\r?\n)?"

FOS_CLI_STATE_PATTERNS = [None] * FOSCliState.UNKNOWN.value
FOS_CLI_STATE_PATTERNS[FOSCliState.PROVIDE_USERNAME.value] = (
        rb"(?m)^[ \t]*" + BOOTSTRAP_HOSTNAME_REGEX +
        rb"(?:\((?:Primary|Secondary)\))?"
        rb"[ \t]+login:[ \t]*"
)
FOS_CLI_STATE_PATTERNS[FOSCliState.CHANGE_PASSWORD.value] = rb"(?m)^New Password:[ \t]*"
FOS_CLI_STATE_PATTERNS[FOSCliState.PROVIDE_PASSWORD.value] = rb"(?m)^Password:[ \t]*"
FOS_CLI_STATE_PATTERNS[FOSCliState.CREDENTIAL_REJECTED.value] = rb"(?m)^Login incorrect\r?$"
FOS_CLI_STATE_PATTERNS[FOSCliState.CREDENTIAL_ACCEPTED.value] = rb"(?m)^Welcome ?!\r?$"
FOS_CLI_STATE_PATTERNS[FOSCliState.LIC_FAIL.value] = rb"(?m)^VM license install failed.\r$"
FOS_CLI_STATE_PATTERNS[FOSCliState.CMD_PROMPT.value] = DEFAULT_HOSTNAME_PROMPT
FOS_CLI_STATE_PATTERNS[FOSCliState.CMD_PROMPT_ECHO.value] = DEFAULT_HOSTNAME_PROMPT_ECHO
FOS_CLI_STATE_PATTERNS[FOSCliState.SHUTTING_DOWN.value] = b"system is going down"
FOS_CLI_STATE_PATTERNS[FOSCliState.REBOOTING.value] = b"stand by while rebooting"
FOS_CLI_STATE_PATTERNS[FOSCliState.SESSION_LOST.value] = (
    rb"\*ATTENTION\*: Admin sessions removed because license registration status changed.*"
)
FOS_CLI_STATE_PATTERNS[FOSCliState.CURRENT_PASSWORD.value] = (
    rb"(?mi)(?:Please enter current administrator password|Current Password):?\s*"
)
FOS_CLI_STATE_PATTERNS[FOSCliState.CONFIRMATION.value] = rb"(?mi)Do you want to continue\?"
FOS_CLI_STATE_PATTERNS[FOSCliState.CHANGE_PASSWORD_CONFIRM.value] = rb"(?mi)^Confirm Password:[ \t]*"
FOS_CLI_STATE_PATTERNS[FOSCliState.MORE_PROMPT.value] = rb"--More--\s*"

DEF_POLICY_COMPLIANT_PASSWORD = "FortinetFOS1!"
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = DEFAULT_USERNAME


class Credentials:
    def __init__(self, username, password) -> None:
        super().__init__()
        self.username = username
        self.password = password


def timed(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        started = perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            print(f"{fn.__qualname__}: {perf_counter() - started:.3f}s")

    return wrapper
