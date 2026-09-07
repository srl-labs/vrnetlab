"""FortiGuard test-license bootstrap feature."""

import os

from cli_commands import CommandSequence, CommandSpec, ConfigBlock

from .base import StaticFeature


def tst_license_enabled():
    return os.getenv("FOS_TST_LICENSE", "false").lower() == "true"


class ConfigureTestLicenseFortiGuard(StaticFeature):
    def __init__(self, vm, commander):
        blocks = []
        if tst_license_enabled():
            blocks = [
                CommandSequence("low-crypto-guard", [
                    CommandSpec(
                        "fnsysctl sh -c 'echo 0 > /tmp/init/if_low_crypto_forced'",
                    ),
                ]),
                ConfigBlock("system fortiguard", [
                    CommandSpec("set fortiguard-anycast disable"),
                    CommandSpec("set fortiguard-server-location automatic"),
                ]),
            ]
        super().__init__(vm, commander, "tst-license", blocks)
