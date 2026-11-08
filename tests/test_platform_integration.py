import builtins
import importlib.util
import os
import sys
import types
import unittest
from unittest import mock

SOURCE = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, SOURCE)

import projectm


def load_with_qt_stubs(name):
    qtcore = types.ModuleType("PyQt5.QtCore")
    qtcore.QObject = object
    qtcore.QTimer = object
    qtcore.pyqtSignal = lambda *args: None
    qtgui = types.ModuleType("PyQt5.QtGui")
    qtgui.QIcon = object
    qtwidgets = types.ModuleType("PyQt5.QtWidgets")
    qtwidgets.QApplication = object
    qtwidgets.QMenu = object
    qtwidgets.QSystemTrayIcon = object
    qt = types.ModuleType("PyQt5")
    qt.__path__ = []
    jobs = types.ModuleType("jobs")
    jobs.Job = object
    modules = {"PyQt5": qt, "PyQt5.QtCore": qtcore,
               "PyQt5.QtGui": qtgui, "PyQt5.QtWidgets": qtwidgets,
               "jobs": jobs}
    for dependency in ("crashlog", "healthlog", "storage", "workers"):
        modules[dependency] = types.ModuleType(dependency)
    spec = importlib.util.spec_from_file_location(name, os.path.join(SOURCE, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class PlatformIntegrationTests(unittest.TestCase):
    def test_macos_does_not_start_linux_media_controls(self):
        desktop = load_with_qt_stubs("desktop")
        with mock.patch.object(desktop.sys, "platform", "darwin"):
            self.assertIsNone(desktop.Desktop._start_mpris(None))

    def test_linux_and_windows_still_start_their_media_bridges(self):
        desktop = load_with_qt_stubs("desktop")
        for platform, name, bridge_name in (("linux", "mpris", "MprisBridge"),
                                             ("win32", "smtc", "SmtcBridge")):
            bridge = mock.Mock()
            bridge.register.return_value = False
            module = types.ModuleType(name)
            setattr(module, bridge_name, mock.Mock(return_value=bridge))
            with self.subTest(platform=platform), \
                    mock.patch.object(desktop.sys, "platform", platform), \
                    mock.patch.dict(sys.modules, {name: module}):
                desktop.Desktop._start_mpris(None)
                bridge.register.assert_called_once()

    def test_macos_skips_networkmanager(self):
        offline = load_with_qt_stubs("offline")
        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name == "PyQt5.QtDBus":
                raise AssertionError("NetworkManager was consulted")
            return original_import(name, *args, **kwargs)

        with mock.patch.object(offline.sys, "platform", "darwin"), \
                mock.patch.object(builtins, "__import__", side_effect=guarded_import):
            self.assertIsNone(offline.system_says_online())

    def test_macos_missing_projectm_does_not_suggest_linux_packages(self):
        with mock.patch.object(projectm.sys, "platform", "darwin"), \
                mock.patch.object(projectm, "find_library", return_value=None):
            available, message = projectm.availability()
            self.assertFalse(available)
            self.assertNotIn("Fedora", message)


if __name__ == "__main__":
    unittest.main()