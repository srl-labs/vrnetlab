"""User startup configuration feature."""

import os

from ..cli_commands import ConfigBlock, EditBlock
from ..env import has_env
from .base import StaticFeature


class ApplyStartupConfig(StaticFeature):
    def __init__(self, vm, commander):
        path = "/config/startup-config.cfg"
        blocks = parse_startup_config(path) if os.path.exists(path) else []
        blocks = filter_startup_config_blocks(blocks, commander.logger)
        super().__init__(vm, commander, "startup-config", blocks)


def filter_startup_config_blocks(blocks, logger):
    """Skip startup DNS configuration that conflicts with FortiGuard hook DNS."""
    if not (
        has_env("FOS_FORTIGUARD_HOOKS")
        and (
            has_env("FOS_MGMT_DNS_PRIMARY", aliases=("FTNT_DNS_PRIMARY",))
            or has_env(
                "FOS_MGMT_DNS_SECONDARY",
                aliases=("FTNT_DNS_SECONDARY",),
                legacy_aliases=("FTNT_MGMG_DNS_SECONDARY", "FOS_MGMG_DNS_SECONDARY"),
            )
            or has_env("FTNT_DNS_PROTOCOL")
        )
    ):
        return blocks

    filtered = [
        block
        for block in blocks
        if not (isinstance(block, ConfigBlock) and block.value == "system dns")
    ]
    if len(filtered) != len(blocks):
        logger.warning(
            "Skipping startup config block 'config system dns' because "
            "FTNT_FORTIGUARD_HOOKS is present with FTNT_DNS_PRIMARY, "
            "FTNT_DNS_SECONDARY, or FTNT_DNS_PROTOCOL"
        )
    return filtered


def _startup_config_commands(config):
    """Yield logical CLI commands while retaining quoted newlines verbatim."""
    pending = []
    quote = None
    start_line = None
    for number, raw_line in enumerate(config, start=1):
        # Text mode normalizes file line endings. Only remove the delimiter;
        # whitespace and comment/scope-looking lines inside quotes are data.
        line = raw_line.removesuffix("\n")
        if not pending:
            line = line.lstrip()
            if not line or line.startswith("#"):
                continue
            start_line = number

        escaped = False
        for character in line:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            elif quote is None and character in {"'", '"'}:
                quote = character

        if quote is not None or escaped:
            pending.append(line)
            continue
        pending.append(line.rstrip())
        yield "\n".join(pending)
        pending.clear()

    if pending:
        reason = "unterminated quoted string" if quote else "unfinished line continuation"
        raise ValueError(
            f"Startup config malformed. {reason} starting on line {start_line}."
        )


def parse_startup_config(path):
    roots = []
    stack = []
    with open(path) as config:
        for line in _startup_config_commands(config):
            if line.startswith("config "):
                node = ConfigBlock(line[7:])
                (stack[-1].children if stack else roots).append(node)
                stack.append(node)
            elif line.startswith("edit "):
                node = EditBlock(line[5:])
                if not stack:
                    raise ValueError("Startup config malformed. edit outside config scope.")
                stack[-1].children.append(node)
                stack.append(node)
            elif line == "next":
                if not stack or not isinstance(stack[-1], EditBlock):
                    raise ValueError("Startup config malformed. next outside edit scope.")
                stack.pop()
            elif line == "end":
                if not stack or not isinstance(stack[-1], ConfigBlock):
                    raise ValueError("Startup config malformed. end outside config scope.")
                stack.pop()
            else:
                if not stack:
                    raise ValueError("Startup config malformed. command outside config scope.")
                stack[-1].children.append(line)
    if stack:
        raise ValueError("Startup config malformed. unmatched config or edit scope.")
    return roots
