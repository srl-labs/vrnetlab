import ast
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import vrnetlab


class TestQemuResourceEnvironment(unittest.TestCase):
    def setUp(self):
        self.vm = object.__new__(vrnetlab.VM)
        self.vm._ram = 4096
        self.vm._cpu = "host"
        self.vm._smp = "1"

    def test_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.vm.ram, 4096)
            self.assertEqual(self.vm.cpu, "host")
            self.assertEqual(self.vm.smp, "1")

    def test_environment_overrides_defaults(self):
        with patch.dict(
            os.environ,
            {
                "QEMU_MEMORY": "8192",
                "QEMU_CPU": "qemu64",
                "QEMU_SMP": "4",
            },
            clear=False,
        ):
            self.assertEqual(self.vm.ram, 8192)
            self.assertEqual(self.vm.cpu, "qemu64")
            self.assertEqual(self.vm.smp, "4")

    def test_sonic_launchers_use_common_smp_override(self):
        launchers = [
            Path(__file__).parents[1] / "sonic/docker/launch.py",
            Path(__file__).parents[1] / "dell/dell_sonic/docker/launch.py",
            Path(__file__).parents[1] / "plvision/plvision_sonic/docker/launch.py",
        ]

        for launcher in launchers:
            source = launcher.read_text()
            self.assertNotIn('self.qemu_args.extend(["-smp", "2"])', source)
            tree = ast.parse(source, filename=str(launcher))
            smp_defaults = [
                keyword.value.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                for keyword in node.keywords
                if keyword.arg == "smp"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ]
            self.assertIn("2", smp_defaults, launcher)

    def test_sros_uses_common_resource_properties(self):
        launcher = Path(__file__).parents[1] / "nokia/sros/docker/launch.py"
        tree = ast.parse(launcher.read_text(), filename=str(launcher))
        sros_vm = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "SROS_vm"
        )
        property_names = {
            node.name
            for node in sros_vm.body
            if isinstance(node, ast.FunctionDef)
            and any(
                isinstance(decorator, ast.Name) and decorator.id == "property"
                for decorator in node.decorator_list
            )
        }
        self.assertNotIn("ram", property_names)
        self.assertNotIn("cpu", property_names)
        self.assertNotIn("smp", property_names)


if __name__ == "__main__":
    unittest.main()
