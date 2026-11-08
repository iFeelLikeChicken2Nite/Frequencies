import importlib.util
import json
import os
import platform
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def load_updater(platform_name, architecture):
    qt = types.ModuleType("PyQt5")
    qt.__path__ = []
    qtcore = types.ModuleType("PyQt5.QtCore")

    class QObject:
        def __init__(self, parent=None):
            pass

    qtcore.QObject = QObject
    qtcore.QTimer = mock.Mock()
    qtcore.pyqtSignal = lambda *args: mock.Mock()
    jobs = types.ModuleType("jobs")
    jobs.Job = type("Job", (), {})
    version = types.ModuleType("version")
    version.BUILD = 100
    modules = {"PyQt5": qt, "PyQt5.QtCore": qtcore,
               "jobs": jobs, "version": version}
    spec = importlib.util.spec_from_file_location(
        "updater_test", os.path.join(ROOT, "src", "updater.py"))
    module = importlib.util.module_from_spec(spec)
    with mock.patch.object(sys, "platform", platform_name), \
            mock.patch.object(platform, "machine", return_value=architecture), \
            mock.patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class MacOSUpdaterTests(unittest.TestCase):
    def test_platform_and_architecture_dispatch(self):
        for platform_name, architecture, expected in (
                ("darwin", "arm64", "macos_arm64"),
                ("darwin", "x86_64", "macos_x86_64"),
                ("linux", "x86_64", "linux"),
                ("win32", "AMD64", "windows")):
            with self.subTest(platform=platform_name, architecture=architecture):
                self.assertEqual(load_updater(platform_name, architecture).OS, expected)

    def test_macos_never_processes_pending_installers(self):
        updater = load_updater("darwin", "arm64")
        with mock.patch.object(updater, "ours", side_effect=AssertionError("pending read")):
            self.assertFalse(updater.before_window())
            self.assertIsNone(updater.apply_on_quit())
            self.assertIn("manually", updater.cant_replace())

    def test_missing_and_new_updates_are_manual(self):
        updater = load_updater("darwin", "arm64")
        instance = updater.Updater({})
        instance._save = mock.Mock()
        instance._download = mock.Mock()
        with mock.patch.object(updater, "_log"):
            instance._checked({}, "")
            self.assertIn("No macOS update", instance.text)
            instance._checked({"build": 101, "url": "https://example.com/app.dmg"}, "")
            self.assertEqual(instance.manual_url, "https://example.com/app.dmg")
            instance._download.assert_not_called()
            instance._checked({"build": 102, "url": "file:///tmp/app.dmg"}, "")
            self.assertEqual(instance.manual_url, "")
            self.assertIn("HTTPS", instance.text)
            instance._checked({"build": 103, "url": "https://[invalid"}, "")
            self.assertIn("HTTPS", instance.text)
            instance._checked({"build": "invalid", "url": "https://example.com"}, "")
            self.assertIn("invalid build", instance.text)

    def test_existing_desktop_updates_still_download(self):
        for platform_name in ("linux", "win32"):
            with self.subTest(platform=platform_name):
                updater = load_updater(platform_name, "x86_64")
                with mock.patch.object(updater, "ours", return_value={}), \
                        mock.patch.object(updater, "_dev", return_value=False), \
                        mock.patch.object(updater, "_log"):
                    instance = updater.Updater({})
                    instance._save = mock.Mock()
                    instance._download = mock.Mock()
                    entry = {"build": 101, "url": "https://example.com/update"}
                    instance._checked(entry, "")
                    instance._download.assert_called_once_with(101, entry)

    def test_manifest_reads_only_matching_mac_architecture(self):
        updater = load_updater("darwin", "arm64")
        response = mock.Mock(status_code=200)
        response.json.return_value = {"linux": {"build": 500},
                                      "macos_x86_64": {"build": 300},
                                      "macos_arm64": {"build": 200}}
        requests = types.ModuleType("requests")
        requests.get = mock.Mock(return_value=response)
        job = updater.Check("https://example.com/latest.json")
        job.send = mock.Mock()
        with mock.patch.dict(sys.modules, {"requests": requests}):
            job.work()
        self.assertEqual(job.send.call_args.args[1]["build"], 200)

    def test_publishing_preserves_other_platforms(self):
        updater = load_updater("darwin", "arm64")
        manifest = {"linux": {"build": 8}, "windows": {"build": 9},
                    "android": {"build": 10}, "macos_x86_64": {"build": 11}}
        original = json.loads(json.dumps(manifest))
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "app.dmg")
            with open(path, "wb") as handle:
                handle.write(b"test")
            job = updater.PublishUpdate.__new__(updater.PublishUpdate)
            job.path = path
            job._token = "token"
            job.send = mock.Mock()
            job._manifest = mock.Mock(return_value=(manifest, None))
            job._release = mock.Mock(return_value={"id": 1})
            job._replace = mock.Mock()
            info = {"build": 101, "size": 4, "sha256": "digest"}
            with mock.patch.object(updater, "repository", return_value=("owner", "repo")), \
                    mock.patch.object(updater, "build_info", return_value=(info, "")), \
                    mock.patch.dict(sys.modules, {"requests": types.ModuleType("requests")}):
                job._publish()
            for name, entry in original.items():
                self.assertEqual(manifest[name], entry)
            self.assertEqual(manifest["macos_arm64"]["build"], 101)
            uploaded = json.loads(job._replace.call_args.args[5])
            self.assertEqual(uploaded, manifest)

    def test_build_metadata_identifies_both_macos_architectures(self):
        spec = importlib.util.spec_from_file_location(
            "build_info_test", os.path.join(ROOT, "tools", "build_info.py"))
        build_info = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build_info)
        with tempfile.TemporaryDirectory() as folder:
            for arch in ("arm64", "x86_64"):
                with self.subTest(arch=arch):
                    path = os.path.join(folder, f"Frequencies-macOS-{arch}.dmg")
                    with open(path, "wb") as handle:
                        handle.write(b"test")
                    self.assertEqual(build_info.main(["build_info.py", path, "--dev"]), 0)
                    with open(path + ".json", encoding="utf-8") as handle:
                        info = json.load(handle)
                    self.assertEqual(info["os"], "macos_" + arch)
                    self.assertEqual(info["arch"], arch)
                    self.assertTrue(info["dev"])

    def test_dev_dmg_cannot_be_marked_as_user_build(self):
        spec = importlib.util.spec_from_file_location(
            "build_info_test", os.path.join(ROOT, "tools", "build_info.py"))
        build_info = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build_info)
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "Frequencies-macOS-arm64-dev.dmg")
            with open(path, "wb") as handle:
                handle.write(b"test")
            self.assertEqual(build_info.main(["build_info.py", path]), 2)
            self.assertEqual(build_info.main(["build_info.py", path, "--dev"]), 0)
            with open(path + ".json", encoding="utf-8") as handle:
                self.assertTrue(json.load(handle)["dev"])


if __name__ == "__main__":
    unittest.main()