import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import appdirs


class MacOSAppDirsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = self.temporary.name
        self.environment = mock.patch.dict(os.environ, {"HOME": self.home}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.macos = mock.patch.object(appdirs, "MACOS", True)
        self.macos.start()
        self.addCleanup(self.macos.stop)
        self.windows = mock.patch.object(appdirs, "WINDOWS", False)
        self.windows.start()
        self.addCleanup(self.windows.stop)

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_native_paths_and_non_destructive_migration(self):
        old = os.path.join(self.home, ".config", "frequencies")
        new = os.path.join(self.home, "Library", "Application Support", "frequencies")
        self.write(os.path.join(old, "settings.json"), "old settings")
        self.write(os.path.join(old, "nested", "stations.json"), "stations")
        self.write(os.path.join(new, "settings.json"), "new settings")
        self.assertEqual(appdirs.config_base(), os.path.dirname(new))
        with open(os.path.join(new, "settings.json"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "new settings")
        with open(os.path.join(new, "nested", "stations.json"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "stations")
        self.assertTrue(os.path.exists(os.path.join(old, "settings.json")))
        os.remove(os.path.join(new, "nested", "stations.json"))
        appdirs.config_base()
        self.assertFalse(os.path.exists(os.path.join(new, "nested", "stations.json")))

    def test_each_category_migrates_to_native_location(self):
        cases = ((appdirs.data_base, (".local", "share"), "Application Support"),
                 (appdirs.cache_base, (".cache",), "Caches"),
                 (appdirs.state_base, (".local", "state"), "Logs"))
        for function, old_parts, native in cases:
            with self.subTest(native=native):
                original = os.path.join(self.home, *old_parts, "frequencies", "test.txt")
                self.write(original, native)
                base = function()
                self.assertEqual(base, os.path.join(self.home, "Library", native))
                with open(os.path.join(base, "frequencies", "test.txt"), encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), native)

    def test_explicit_overrides_keep_their_original_paths(self):
        overrides = {"XDG_CONFIG_HOME": "/custom/config", "XDG_DATA_HOME": "/custom/data",
                     "XDG_CACHE_HOME": "/custom/cache", "XDG_STATE_HOME": "/custom/state"}
        with mock.patch.dict(os.environ, overrides):
            self.assertEqual(appdirs.config_base(), overrides["XDG_CONFIG_HOME"])
            self.assertEqual(appdirs.data_base(), overrides["XDG_DATA_HOME"])
            self.assertEqual(appdirs.cache_base(), overrides["XDG_CACHE_HOME"])
            self.assertEqual(appdirs.state_base(), overrides["XDG_STATE_HOME"])
        self.assertFalse(os.path.exists(os.path.join(self.home, "Library")))

    def test_old_application_name_is_migrated(self):
        old = os.path.join(self.home, ".config", "streamdeck-radio", "stations.json")
        self.write(old, "legacy stations")
        base = appdirs.config_base()
        with open(os.path.join(base, "frequencies", "stations.json"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "legacy stations")

    def test_migration_preserves_restrictive_file_permissions(self):
        old = os.path.join(self.home, ".config", "frequencies", "release_token.json")
        self.write(old, "private")
        os.chmod(old, 0o600)
        target = os.path.join(appdirs.config_base(), "frequencies", "release_token.json")
        self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o600)

    def test_interrupted_copy_is_retried_without_partial_files(self):
        old = os.path.join(self.home, ".config", "frequencies", "settings.json")
        self.write(old, "settings")
        new = os.path.join(self.home, "Library", "Application Support", "frequencies")
        with mock.patch.object(appdirs.shutil, "copyfileobj", side_effect=OSError("failed")):
            with self.assertRaises(OSError):
                appdirs.config_base()
        self.assertFalse(os.path.exists(os.path.join(new, "settings.json")))
        self.assertFalse(os.path.exists(os.path.join(new, ".xdg-migrated-config")))
        self.assertFalse(any(name.startswith(".frequencies-migrate-")
                             for name in os.listdir(new)))
        appdirs.config_base()
        with open(os.path.join(new, "settings.json"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "settings")

    def test_symlinks_are_preserved_without_following_destination_links(self):
        old = os.path.join(self.home, ".config", "frequencies")
        new = os.path.join(self.home, "Library", "Application Support", "frequencies")
        external = os.path.join(self.home, "external")
        self.write(os.path.join(old, "linked.json"), "settings")
        self.write(os.path.join(old, "nested", "stations.json"), "old stations")
        os.symlink("linked.json", os.path.join(old, "settings.json"))
        os.makedirs(new)
        os.makedirs(external)
        os.symlink(external, os.path.join(new, "nested"))
        appdirs.config_base()
        self.assertTrue(os.path.islink(os.path.join(new, "settings.json")))
        with open(os.path.join(new, "settings.json"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "settings")
        self.assertFalse(os.path.exists(os.path.join(external, "stations.json")))

    def test_linux_paths_are_unchanged(self):
        with mock.patch.object(appdirs, "MACOS", False):
            self.assertEqual(appdirs.config_base(), os.path.join(self.home, ".config"))
            self.assertEqual(appdirs.data_base(), os.path.join(self.home, ".local", "share"))


if __name__ == "__main__":
    unittest.main()