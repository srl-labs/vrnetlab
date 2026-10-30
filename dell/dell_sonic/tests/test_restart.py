"""Container restart safety for the Dell SONiC launcher.

Run with: python3 -m unittest discover -s dell/dell_sonic/tests
"""

import unittest
from unittest.mock import MagicMock, patch

from test_management_bootstrap import launch


class DiskImageTests(unittest.TestCase):
    def test_uses_base_image_not_overlay(self):
        vm_init = MagicMock(return_value=None)
        find = MagicMock(return_value="/dell-sonic-4.6.0.qcow2")
        with (
            patch.object(launch.vrnetlab, "find_disk_image", find, create=True),
            patch.object(launch.vrnetlab.VM, "__init__", vm_init, create=True),
        ):
            launch.Dell_Sonic_VM("leaf1", "admin", "secret", "tc")
        find.assert_called_once_with()
        self.assertEqual(vm_init.call_args.kwargs["disk_image"], find.return_value)


if __name__ == "__main__":
    unittest.main()
