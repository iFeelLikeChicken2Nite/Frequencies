import ctypes.util
import importlib.util
import os
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock


class MacOSLibmpvTests(unittest.TestCase):
    def test_frozen_macos_loads_versioned_bundled_libmpv(self):
        source = os.path.join(os.path.dirname(__file__), "..", "src", "player.py")
        spec = importlib.util.spec_from_file_location("player_macos_test", source)
        module = importlib.util.module_from_spec(spec)
        with tempfile.TemporaryDirectory() as folder:
            library = os.path.join(folder, "libmpv.2.dylib")
            with open(library, "wb"):
                pass
            modules = {"mpv": types.ModuleType("mpv"),
                       "playlists": types.ModuleType("playlists")}
            with mock.patch.object(sys, "platform", "darwin"), \
                    mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(sys, "_MEIPASS", folder, create=True), \
                    mock.patch.dict(os.environ, {"STREAMDECK_LIBMPV_PATH": ""}), \
                    mock.patch.dict(sys.modules, modules), \
                    mock.patch.object(ctypes.util, "find_library"):
                spec.loader.exec_module(module)
                self.assertEqual(ctypes.util.find_library("mpv"), library)

    @unittest.skipUnless(sys.platform == "darwin", "requires macOS")
    def test_bundled_libmpv_decodes_audio_without_system_mpv(self):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        pointer = os.path.join(root, "build-macos", "build-python")
        try:
            with open(pointer, encoding="utf-8") as handle:
                python = handle.read().strip()
        except OSError:
            python = os.path.join(root, "build-macos", "venv", "bin", "python")
        library = os.path.join(root, "build-macos", "dist", "Frequencies.app",
                               "Contents", "Resources", "libmpv.dylib")
        if not os.path.isfile(python) or not os.path.isfile(library):
            self.skipTest("macOS build is not available")
        environment = {"HOME": os.path.join(root, "build-macos"),
                       "PATH": "/usr/bin:/bin",
                       "PYTHONPATH": os.path.join(root, "src"),
                       "STREAMDECK_LIBMPV_PATH": library}
        code = ("import player, mpv; "
                "instance = mpv.MPV(ao='null', vo='null', config=False); "
                "instance.play('av://lavfi:sine=frequency=440:duration=1'); "
                "instance.wait_until_playing(timeout=15); "
                "instance.wait_for_playback(timeout=15); "
                "instance.terminate()")
        subprocess.run([python, "-c", code], env=environment, check=True,
                       timeout=35, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()