import vrnetlab


class NetMgmtStrategy:
    mgmt_passthrough = False

    def prep(self):
        pass

    def configure_vm_mgmt(self, vm: vrnetlab.VM):
        """Copy strategy-owned addressing onto a vrnetlab VM."""
        return

    def gen_mgmt(self, vm: vrnetlab.VM):
        if vm.mgmt_host_ip + 1 >= vm.mgmt_guest_ip:
            vm.logger.error(
                "Guest IP (%s) must be at least 2 higher than host IP(%s)",
                vm.mgmt_guest_ip,
                vm.mgmt_host_ip,
            )

        if (
                vm.snapshot_metadata
                and "mac_addresses" in vm.snapshot_metadata
                and len(vm.snapshot_metadata["mac_addresses"]) > 0
        ):
            vm.mgmt_mac = vm.snapshot_metadata["mac_addresses"][0]
            vm.logger.info(f"Using saved management MAC: {vm.mgmt_mac}")
        else:
            vm.mgmt_mac = vm.get_mgmt_mac()

        self.before_gen_mgmt(vm)

        return [
            "-device",
            f"{vm.nic_type},netdev=p00,mac={vm.mgmt_mac}",
            "-netdev",
            self.gen_mgmt_netdev(vm),
        ]

    def before_gen_mgmt(self, vm: vrnetlab.VM):
        return

    def gen_mgmt_netdev(self, vm: vrnetlab.VM):
        pass

    def write_ifup_script(self, vm: vrnetlab.VM):
        return
