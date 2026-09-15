"""User startup configuration feature."""

import os

from cli_commands import ConfigBlock, EditBlock
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
        "FOS_FORTIGUARD_HOOKS" in os.environ
        and (
            "FOS_MGMT_DNS_PRIMARY" in os.environ
            or "FOS_MGMT_DNS_SECONDARY" in os.environ
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
            "FOS_FORTIGUARD_HOOKS is present with FOS_MGMT_DNS_PRIMARY or "
            "FOS_MGMT_DNS_SECONDARY"
        )
    return filtered


def parse_startup_config(path):
    roots = []
    stack = []
    with open(path) as config:
        for raw_line in config:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
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
