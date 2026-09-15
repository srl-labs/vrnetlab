"""Detect and expose the running FortiOS version to bootstrap features."""

import re
from dataclasses import dataclass, field

from cli_commands import CommandSequence, CommandSpec

from .base import Feature


VERSION_PATTERN = re.compile(
    rb"(?mi)^Version:\s*"
    rb"(?:(?P<platform>[\w.-]+)\s+)?"
    rb"v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+),"
    rb"build(?P<build>\d+)"
)


@dataclass(frozen=True, order=True)
class FortiOSVersion:
    """Structured FortiOS release information shared through the VM."""

    major: int
    minor: int
    patch: int
    build: int
    platform: str = field(default="", compare=False)

    @classmethod
    def from_system_status(cls, output):
        match = VERSION_PATTERN.search(output)
        if not match:
            return None
        platform = match.group("platform")
        return cls(
            major=int(match.group("major")),
            minor=int(match.group("minor")),
            patch=int(match.group("patch")),
            build=int(match.group("build")),
            platform=platform.decode(errors="replace") if platform else "",
        )

    def __str__(self):
        release = f"v{self.major}.{self.minor}.{self.patch},build{self.build:04d}"
        return f"{self.platform} {release}".strip()


class DetectSystemVersion(Feature):
    """Populate ``vm.fos_version`` once for all later bootstrap features."""

    def __init__(self, vm, commander):
        super().__init__(vm, commander, "system-version")

    def activate(self):
        self.commander.submit_block(self, CommandSequence("system-version", [
            CommandSpec("get system status", capture_output=True, suppress_output=True),
        ]))

    def on_command_executed(self, command, state):
        version = FortiOSVersion.from_system_status(bytes(command.output))
        if version is None:
            raise RuntimeError(
                "Could not determine the FortiOS version and build from "
                "get system status"
            )
        self.vm.fos_version = version
        self.commander.logger.info("Detected FortiOS version %s", version)

    def on_block_complete(self):
        self.commander.feature_complete(self)
