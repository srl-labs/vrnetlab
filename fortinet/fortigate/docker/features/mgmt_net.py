"""Management interface, route, and VRF features."""

import os
import re

from cli_commands import CommandSpec, ConfigBlock, EditBlock

from .base import Feature, StaticFeature


class ConfigureMgmtNetwork(StaticFeature):
    """Configure port1 and its management route when static management is used."""

    def __init__(self, vm, commander, name="management"):
        super().__init__(vm, commander, name, self._blocks_for(vm))

    @staticmethod
    def _blocks_for(vm):
        if vm.mgmt_address_ipv4 == "dhcp":
            return []
        port_children = [
            "set mode static",
            f"set ip {vm.mgmt_address_ipv4}",
            "set allowaccess ping https ssh http",
        ]
        if vm.mgmt_address_ipv6:
            port_children.append(ConfigBlock("ipv6", [
                "set ip6-mode static",
                f"set ip6-address {vm.mgmt_address_ipv6}",
                "set ip6-allowaccess ping https ssh http",
            ]))
        return [
            ConfigBlock("system interface", [EditBlock("port1", port_children)]),
            ConfigBlock("router static", [EditBlock("9999", [
                f"set gateway {vm.mgmt_gw_ipv4}",
                "set device port1",
            ])]),
        ]


class ReconfigureMgmtNetwork(Feature):
    """Restore management networking after license restore, before validation."""

    def __init__(self, vm, commander):
        super().__init__(vm, commander, "management-after-license")
        self._network_blocks = ConfigureMgmtNetwork._blocks_for(vm)

    def activate(self):
        if not os.path.exists("/tftpboot/appliance.lic") or self.vm.mgmt_address_ipv4 == "dhcp":
            self.commander.feature_complete(self)
            return
        self._submit_next_network_block()

    def _submit_next_network_block(self):
        if self._network_blocks:
            self.commander.submit_block(self, self._network_blocks.pop(0))
            return
        self.commander.feature_complete(self)

    def on_block_complete(self):
        self._submit_next_network_block()


class MoveMgmtToVrf1(Feature):
    """Configure the management VRF only after license validation.

    FortiProxy does not support interface VRFs.  In that case the management
    route is narrowed to the management gateway's network rather than
    installing a conflicting default route.
    """

    def __init__(self, vm, commander):
        super().__init__(vm, commander, "management-vrf")
        self._phase = "interface"
        self._vrf_unsupported = False
        self._route_fallback = False

    def activate(self):
        if not os.path.exists("/tftpboot/appliance.lic") or self.vm.mgmt_address_ipv4 == "dhcp":
            self.commander.feature_complete(self)
            return
        self.commander.submit_block(self, ConfigBlock("system interface", [EditBlock("port1", [
            CommandSpec("set vrf 1", capture_output=True),
        ])]))

    def on_command_executed(self, command, state):
        if command.spec.line == "set vrf 1" and re.search(
            rb"(?mi)command (?:parse )?error|Command fail", bytes(command.output)
        ):
            self._vrf_unsupported = True

    def on_block_complete(self):
        if self._phase == "interface":
            self._phase = "route"
            route = ["set vrf 1"]
            if self._vrf_unsupported:
                self._route_fallback = True
                route = [self._management_route_destination()]
            self.commander.submit_block(self, ConfigBlock("router static", [EditBlock("9999", route)]))
            return
        if self._phase == "route" and self._vrf_unsupported and not self._route_fallback:
            self._phase = "fallback"
            self.commander.submit_block(self, ConfigBlock("router static", [EditBlock("9999", [
                self._management_route_destination(),
            ])]))
            return
        self.commander.feature_complete(self)

    def _management_route_destination(self):
        destination = self.vm.mgmt_gateway_destination(
            self.vm.mgmt_gw_ipv4,
            self.vm.mgmt_address_ipv4,
        )
        return f"set dst {destination}"
