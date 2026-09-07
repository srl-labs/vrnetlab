"""User startup configuration feature."""

import os

from cli_commands import ConfigBlock, EditBlock
from .base import StaticFeature


class ApplyStartupConfig(StaticFeature):
    def __init__(self, vm, commander):
        path = "/config/startup-config.cfg"
        blocks = parse_startup_config(path) if os.path.exists(path) else []
        super().__init__(vm, commander, "startup-config", blocks)


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
