"""Unit tests for the vAOS launcher helpers (no VM, no Docker needed)."""

import importlib.util
import pathlib
import sys
import types

import pytest

LAUNCH_PY = pathlib.Path(__file__).resolve().parents[1] / "docker" / "launch.py"


@pytest.fixture(scope="module")
def launch():
    # launch.py imports the vrnetlab common library at module level; a stub is
    # enough for the static helpers tested here.
    stub = types.ModuleType("vrnetlab")
    stub.VM = object
    stub.VR = object
    saved = sys.modules.get("vrnetlab")
    sys.modules["vrnetlab"] = stub
    try:
        spec = importlib.util.spec_from_file_location("vaos_launch", LAUNCH_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if saved is None:
            del sys.modules["vrnetlab"]
        else:
            sys.modules["vrnetlab"] = saved
    return module


def console(*lines):
    return "\r\n".join(lines).encode()


def test_clean_snapshot(launch):
    buf = console(
        "os1-> show configuration snapshot",
        "! Chassis:",
        'system name "os1"',
        "",
        "! VLAN:",
        "vlan 1 admin-state enable",
        "",
        "os1-> ",
    )
    assert launch.VAOS_vm._extract_snapshot(buf) == (
        '! Chassis:\nsystem name "os1"\n\n! VLAN:\nvlan 1 admin-state enable\n'
    )


def test_async_lines_are_dropped(launch):
    buf = console(
        "-> show configuration snapshot",
        "! VLAN:",
        "+++ Link 1/1/4 operationally up",
        "Sat Oct 31 14:02:19 : vcmNi thread INFO message:",
        "vlan 1 admin-state enable",
        "-> ",
    )
    assert launch.VAOS_vm._extract_snapshot(buf) == "! VLAN:\nvlan 1 admin-state enable\n"


def test_async_message_inside_a_line_is_rejoined(launch):
    buf = console(
        "-> show configuration snapshot",
        "! SPB-ISIS:",
        "spb+++ Link 1/1/1 operationally up",
        " isis admin-state enable",
        "-> ",
    )
    assert launch.VAOS_vm._extract_snapshot(buf) == "! SPB-ISIS:\nspb isis admin-state enable\n"


def test_output_stops_at_the_prompt(launch):
    buf = console(
        "os1-> show configuration snapshot",
        "vlan 1 admin-state enable",
        "os1->",
        "write memory flash-synchro",
    )
    assert launch.VAOS_vm._extract_snapshot(buf) == "vlan 1 admin-state enable\n"
