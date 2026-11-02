"""Generated end-to-end boundary cases for the FortiOS serial parser stack."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from fortinet.common.cli_commands import CommandSpec, SessionLossAction  # noqa: E402
from fortinet.common.common import Credentials, FOSCliState  # noqa: E402
from fortinet.common.features.base import Feature  # noqa: E402
from fortinet.common.fos_cli_driver import FOSCliDriver  # noqa: E402
from fortinet.common.fos_commander import FOSCommander  # noqa: E402
from fortinet.common.terminal import Terminal  # noqa: E402
from fortinet.tests.test_common_stack_parsing import (  # noqa: E402
    SerialConnection,
)


class BoundaryFeature(Feature):
    def __init__(self, commander, spec):
        super().__init__(None, commander, "boundary-probe")
        self.spec = spec
        self.outputs = []
        self.dispatched = []

    def activate(self):
        self.commander.submit_block(self, self.spec)

    def on_command_dispatched(self, attempt):
        super().on_command_dispatched(attempt)
        self.dispatched.append(attempt)

    def on_command_executed(self, attempt, state):
        self.outputs.append((bytes(attempt.output), state))

    def on_session_loss(self, attempt):
        return SessionLossAction.CONTINUE


def make_stack(spec, response, *, allow_recovery=False, initial_prompt=b"fw #"):
    logger = MagicMock()
    connection = SerialConnection({
        b"admin\r": b"Password:",
        b"secret\r": b"Welcome!\r\n" + initial_prompt + b"\r\n",
        b"probe\r": response,
    })
    terminal = Terminal(connection, logger, default_wait="fw #")
    commander = FOSCommander(terminal, logger)
    feature = BoundaryFeature(commander, spec)
    commander.start([feature])
    driver = FOSCliDriver(
        terminal,
        commander,
        Credentials("admin", "secret"),
        logger,
        "Bootstrap1!",
        MagicMock(),
        MagicMock(),
        allow_credential_recovery=allow_recovery,
    )
    if allow_recovery:
        driver.remember_current_credentials()
    states = []
    original_on_state = commander.on_state
    def record_state(state, output):
        states.append(state)
        return original_on_state(state, output)
    commander.on_state = record_state
    original_echo = commander.on_prompt_echo
    def record_echo(output):
        states.append(FOSCliState.CMD_PROMPT_ECHO)
        return original_echo(output)
    commander.on_prompt_echo = record_echo
    return connection, terminal, commander, driver, feature, states

def prompt_case(hostname, context, marker, ending):
    prompt = hostname + context + " " + marker
    response = (
        (prompt + " probe" + ending + "captured diagnostic" + ending + prompt + ending)
        .encode()
    )
    spec = CommandSpec("probe", capture_output=True)
    connection, terminal, commander, driver, feature, states = make_stack(
        spec, response, initial_prompt=prompt.encode()
    )
    try:
        with patch("fortinet.common.fos_cli_driver.EXPECT_TIMEOUT_SECONDS", 0.05), \
                patch("fortinet.common.fos_cli_driver.PROCESS_SPIN_SECONDS", 1):
            driver.process_state()
        assert commander.ready
        assert feature.completed
        assert feature.outputs[0][1] == FOSCliState.CMD_PROMPT
        assert b"captured diagnostic" in feature.outputs[0][0]
        assert FOSCliState.CMD_PROMPT_ECHO in states
    finally:
        if not connection.closed:
            terminal.close()


def state_case(state, sample, suffix=b"", *, recover=False):
    response = sample + suffix
    if state in (
        FOSCliState.CREDENTIAL_REJECTED,
        FOSCliState.PROVIDE_USERNAME,
        FOSCliState.CREDENTIAL_ACCEPTED,
        FOSCliState.SESSION_LOST,
        FOSCliState.CMD_PROMPT_ECHO,
    ):
        completion = (FOSCliState.CMD_PROMPT,)
    else:
        completion = (state,)
    connection, terminal, commander, driver, feature, states = make_stack(
        CommandSpec("probe", completion_states=completion, capture_output=True),
        response,
        allow_recovery=recover,
        initial_prompt=b"fw (global) #" if state == FOSCliState.CMD_PROMPT_ECHO else b"fw #",
    )
    try:
        with patch("fortinet.common.fos_cli_driver.EXPECT_TIMEOUT_SECONDS", 0.03), \
                patch("fortinet.common.fos_cli_driver.PROCESS_SPIN_SECONDS", 1):
            driver.process_state()
        assert state in states
        assert commander.ready
        assert feature.completed
    finally:
        if not connection.closed:
            terminal.close()


class EndToEndBoundaryCases(unittest.TestCase):
    def test_expected_completion_prompt_mismatch_raises_from_serial_result(self):
        response = b"Do you want to continue?\r\n"
        connection, terminal, _commander, driver, _feature, _states = make_stack(
            CommandSpec(
                "probe",
                completion_states=(FOSCliState.CONFIRMATION, FOSCliState.CMD_PROMPT),
                expected_completion_state=FOSCliState.CMD_PROMPT,
            ),
            response,
        )
        try:
            with patch("fortinet.common.fos_cli_driver.EXPECT_TIMEOUT_SECONDS", 0.05), \
                    patch("fortinet.common.fos_cli_driver.PROCESS_SPIN_SECONDS", 1):
                with self.assertRaisesRegex(RuntimeError, "expected CMD_PROMPT, received CONFIRMATION"):
                    driver.process_state()
        finally:
            terminal.close()

    def test_error_text_is_captured_when_fail_on_error_is_disabled(self):
        response = b"Command fail. Return code -542\r\nfw #\r\n"
        connection, terminal, commander, driver, feature, _states = make_stack(
            CommandSpec("probe", capture_output=True, fail_on_error=False), response
        )
        try:
            with patch("fortinet.common.fos_cli_driver.EXPECT_TIMEOUT_SECONDS", 0.05), \
                    patch("fortinet.common.fos_cli_driver.PROCESS_SPIN_SECONDS", 1):
                driver.process_state()
            self.assertTrue(commander.ready)
            self.assertTrue(feature.completed)
            self.assertIn(b"Command fail. Return code -542", feature.outputs[0][0])
        finally:
            if not connection.closed:
                terminal.close()

    def test_noise_without_a_pattern_reaches_unknown_then_timeout(self):
        connection, terminal, commander, driver, feature, states = make_stack(
            CommandSpec("probe", capture_output=True), b"\xff\x00garbage\r\nnot-a-prompt\r\n"
        )
        try:
            with patch("fortinet.common.fos_cli_driver.EXPECT_TIMEOUT_SECONDS", 0.01), \
                    patch("fortinet.common.fos_cli_driver.PROCESS_SPIN_SECONDS", 0.06):
                driver.process_state()
            self.assertIn(FOSCliState.UNKNOWN, states)
            self.assertIn(FOSCliState.TN_TIMEOUT, states)
            self.assertFalse(commander.ready)
            self.assertFalse(feature.completed)
            self.assertIn(b"garbage", bytes(feature.current_attempt.output))
        finally:
            terminal.close()

    def test_idle_read_exposes_retained_prompt_newline(self):
        connection, terminal, commander, driver, feature, states = make_stack(
            CommandSpec("probe", capture_output=True), None
        )
        try:
            with patch("fortinet.common.fos_cli_driver.EXPECT_TIMEOUT_SECONDS", 0.01), \
                    patch("fortinet.common.fos_cli_driver.PROCESS_SPIN_SECONDS", 0.04):
                driver.process_state()
            self.assertIn(FOSCliState.TN_TIMEOUT, states)
            # The login response ended with a newline after the matched CLI
            # prompt. Full-buffer timeout inspection exposes that real byte.
            self.assertEqual(bytes(feature.current_attempt.output), b"\n")
            self.assertTrue(commander.busy)
        finally:
            terminal.close()

    def test_partial_prompt_and_command_echo_are_buffered_across_reads(self):
        command_echo = b"fw (global) # probe\r\n"
        payload = b"config system dns\r\n set primary 1.1.1.1\r\nend\r\nfw (global) #\r\n"
        response = tuple([command_echo[:3], command_echo[3:12], command_echo[12:] + payload[:11], payload[11:29], payload[29:]])
        connection, terminal, commander, driver, feature, states = make_stack(
            CommandSpec("probe", capture_output=True), response,
            initial_prompt=b"fw (global) #",
        )
        try:
            with patch("fortinet.common.fos_cli_driver.EXPECT_TIMEOUT_SECONDS", 0.05), \
                    patch("fortinet.common.fos_cli_driver.PROCESS_SPIN_SECONDS", 1):
                driver.process_state()
            self.assertTrue(commander.ready)
            self.assertIn(FOSCliState.CMD_PROMPT_ECHO, states)
            self.assertIn(b"set primary 1.1.1.1", feature.outputs[0][0])
        finally:
            if not connection.closed:
                terminal.close()


# Exercise every pattern-backed state through the real terminal, driver, and
# commander. States that represent a login transition include the follow-up
# prompt needed for the real scheduler to safely finish the feature.
STATE_CASES = [
    ("username_prompt", FOSCliState.PROVIDE_USERNAME, b"fw login:\r\n", b"Password:\r\nWelcome!\r\nfw #\r\n", False),
    ("password_prompt", FOSCliState.PROVIDE_PASSWORD, b"Password:", b"", False),
    ("new_password_prompt", FOSCliState.CHANGE_PASSWORD, b"New Password:", b"", False),
    ("credential_rejected", FOSCliState.CREDENTIAL_REJECTED, b"Login incorrect\r\n", b"fw login:\r\nPassword:\r\nWelcome!\r\nfw #\r\n", True),
    ("credential_accepted", FOSCliState.CREDENTIAL_ACCEPTED, b"Welcome!\r\n", b"fw #\r\n", False),
    ("license_failure_notice", FOSCliState.LIC_FAIL, b"VM license install failed.\r\n", b"", False),
    ("command_prompt", FOSCliState.CMD_PROMPT, b"fw #\r\n", b"", False),
    ("command_prompt_echo", FOSCliState.CMD_PROMPT_ECHO, b"fw (global) # probe\r\n", b"fw (global) #\r\n", False),
    ("shutdown_notice", FOSCliState.SHUTTING_DOWN, b"system is going down\r\n", b"", False),
    ("reboot_notice", FOSCliState.REBOOTING, b"stand by while rebooting\r\n", b"", False),
    ("session_lost_notice", FOSCliState.SESSION_LOST, b"*ATTENTION*: Admin sessions removed because license registration status changed\r\n", b"Welcome!\r\nfw #\r\n", False),
    ("current_password_prompt", FOSCliState.CURRENT_PASSWORD, b"Current Password:\r\n", b"", False),
    ("confirmation_prompt", FOSCliState.CONFIRMATION, b"Do you want to continue?\r\n", b"", False),
    ("confirm_new_password_prompt", FOSCliState.CHANGE_PASSWORD_CONFIRM, b"Confirm Password:\r\n", b"", False),
    ("pager_prompt", FOSCliState.MORE_PROMPT, b"--More--", b"", False),
    ("multiline_prompt", FOSCliState.MULTILINE_PROMPT, b">\r\n", b"", False),
]


def _make_state_test(case):
    name, state, sample, suffix, recover = case
    def test(self):
        with self.subTest(state=state.name):
            state_case(state, sample, suffix, recover=recover)
    test.__name__ = f"test_state_{name}_through_full_stack"
    test.__doc__ = f"Reach {state.name} from connection bytes and observe commander handling."
    return test


for _case in STATE_CASES:
    setattr(EndToEndBoundaryCases, f"test_state_{_case[0]}_through_full_stack", _make_state_test(_case))


# Hostname, prompt context, prompt glyph, and line ending are independent
# parser boundaries. The Cartesian set creates one discoverable test per wire
# shape so failures point to the exact prompt form.
PROMPT_CASES = [
    (host, context, marker, ending)
    for host in ("fw", "edge-01", "FortiGate-VM64-KVM", "FGVMABC123")
    for context in ("", " (global)", " (root) (vdom blue)")
    for marker in ("#", "$")
    for ending in ("\n", "\r\n")
]


def _make_prompt_test(case):
    hostname, context, marker, ending = case
    label = f"{hostname}_{context or 'plain'}_{marker}_{'crlf' if ending == chr(13)+chr(10) else 'lf'}"
    def test(self):
        prompt_case(hostname, context, marker, ending)
    test.__name__ = "test_prompt_" + "_".join(part.replace(" ", "").replace("(", "").replace(")", "") for part in label.split("_"))
    test.__doc__ = f"Parse command echo and prompt {hostname}{context} {marker} with {ending!r}."
    return test


for _case in PROMPT_CASES:
    setattr(EndToEndBoundaryCases, _make_prompt_test(_case).__name__, _make_prompt_test(_case))


# Distinct failure payloads ensure the command failure parser handles signed
# and unsigned return codes and optional whitespace over the serial boundary.
ERROR_CASES = [b"0", b"1", b"-1", b"-5", b"-542", b"255", b"0007", b"-214"]


def _make_failure_test(return_code):
    def test(self):
        response = b"Command fail. Return code " + return_code + b"\r\nfw #\r\n"
        connection, terminal, _commander, driver, _feature, _states = make_stack(
            CommandSpec("probe", capture_output=True, fail_on_error=True), response
        )
        try:
            with patch("fortinet.common.fos_cli_driver.EXPECT_TIMEOUT_SECONDS", 0.05), \
                    patch("fortinet.common.fos_cli_driver.PROCESS_SPIN_SECONDS", 1):
                with self.assertRaisesRegex(RuntimeError, f"return code {return_code.decode()}"):
                    driver.process_state()
        finally:
            terminal.close()
    test.__name__ = f"test_command_error_code_{return_code.decode().replace('-', 'negative_')}"
    test.__doc__ = f"Pass FortiOS return code {return_code.decode()} through the full stack."
    return test


for _code in ERROR_CASES:
    setattr(EndToEndBoundaryCases, _make_failure_test(_code).__name__, _make_failure_test(_code))


CAPTURE_CASES = [
    b"one line\n",
    b"multiple\nlines\nwith trailing newline\n",
    b"carriage return\rwithout line feed",
    b"\xff\xfe\x00binary-ish bytes",
    b"\x1b[31mcolored output\x1b[0m",
    b"tabs\tand    repeated spaces",
    b"# metadata-looking output",
    b"prompt-like value fw (global) # inside data",
    b"empty-looking\n\nline",
    b"unicode utf-8: \xe2\x98\x83",
    b"long" * 2048,
    b"return code text: Command fail. Return code -7",
]


def _make_capture_test(index, payload):
    def test(self):
        ending = b"\r\n"
        response = payload + ending + b"fw #\r\n"
        connection, terminal, commander, driver, feature, _states = make_stack(
            CommandSpec("probe", capture_output=True), response
        )
        try:
            with patch("fortinet.common.fos_cli_driver.EXPECT_TIMEOUT_SECONDS", 0.05), \
                    patch("fortinet.common.fos_cli_driver.PROCESS_SPIN_SECONDS", 1):
                driver.process_state()
            self.assertTrue(commander.ready)
            self.assertEqual(feature.outputs[0][1], FOSCliState.CMD_PROMPT)
            self.assertIn(payload, feature.outputs[0][0])
        finally:
            if not connection.closed:
                terminal.close()
    test.__name__ = f"test_capture_payload_{index:02d}"
    test.__doc__ = f"Capture serial output payload case {index}."
    return test


for _index, _payload in enumerate(CAPTURE_CASES):
    setattr(EndToEndBoundaryCases, f"test_capture_payload_{_index:02d}", _make_capture_test(_index, _payload))


if __name__ == "__main__":
    unittest.main()
