import importlib.util
import os
import sys
import types
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def load_audit():
    package = types.ModuleType("macholib")
    package.__path__ = []
    macho_module = types.ModuleType("macholib.MachO")
    macho_module.MachO = mock.Mock()
    commands = types.ModuleType("macholib.mach_o")
    for number, name in enumerate(("LC_LOAD_DYLIB", "LC_LOAD_WEAK_DYLIB",
                                   "LC_REEXPORT_DYLIB", "LC_LOAD_UPWARD_DYLIB",
                                   "LC_LAZY_LOAD_DYLIB", "LC_BUILD_VERSION",
                                   "LC_VERSION_MIN_MACOSX"), 1):
        setattr(commands, name, number)
    package.mach_o = commands
    spec = importlib.util.spec_from_file_location(
        "macos_bundle_audit_test", os.path.join(ROOT, "tools", "check_macos_bundle.py"))
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"macholib": package, "macholib.MachO": macho_module,
                                      "macholib.mach_o": commands}):
        spec.loader.exec_module(module)
    return module


class MacOSBundleAuditTests(unittest.TestCase):
    def test_rejects_newer_target_and_build_host_library(self):
        audit = load_audit()
        build = (types.SimpleNamespace(cmd=audit.mach_o.LC_BUILD_VERSION),
                 types.SimpleNamespace(platform=1, minos=14 << 16), b"")
        dependency = (types.SimpleNamespace(cmd=audit.mach_o.LC_LOAD_DYLIB),
                      None, b"/opt/homebrew/lib/example.dylib\0")
        image = types.SimpleNamespace(headers=[types.SimpleNamespace(commands=[build, dependency])])
        with mock.patch.object(audit, "MachO", return_value=image):
            problems = audit.inspect_image("test")
        self.assertTrue(any("macOS 14" in problem for problem in problems))
        self.assertTrue(any("/opt/homebrew" in problem for problem in problems))

    def test_system_path_traversal_is_not_whitelisted(self):
        audit = load_audit()
        build = (types.SimpleNamespace(cmd=audit.mach_o.LC_BUILD_VERSION),
                 types.SimpleNamespace(platform=1, minos=11 << 16), b"")
        dependency = (types.SimpleNamespace(cmd=audit.mach_o.LC_LOAD_DYLIB),
                      None, b"/usr/lib/../../tmp/library.dylib\0")
        image = types.SimpleNamespace(headers=[types.SimpleNamespace(commands=[build, dependency])])
        with mock.patch.object(audit, "MachO", return_value=image):
            self.assertEqual(len(audit.inspect_image("test")), 1)


if __name__ == "__main__":
    unittest.main()