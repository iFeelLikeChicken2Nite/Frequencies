import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import audiotap


class AudioTapPlatformTests(unittest.TestCase):
    def test_macos_does_not_use_linux_capture_tools(self):
        with mock.patch.object(audiotap.sys, "platform", "darwin"), \
                mock.patch.object(audiotap.shutil, "which", return_value="/tool") as which:
            self.assertIsNone(audiotap.capture_command())
            self.assertEqual(audiotap.list_sources(), [])
            available, message = audiotap.availability()
            self.assertFalse(available)
            self.assertIn("macOS", message)
            which.assert_not_called()

    def test_linux_still_selects_pulse_capture(self):
        with mock.patch.object(audiotap.sys, "platform", "linux"), \
                mock.patch.object(audiotap.shutil, "which", return_value="/tool"):
            self.assertEqual(audiotap.capture_command()[0], "parec")
            self.assertTrue(audiotap.availability()[0])


if __name__ == "__main__":
    unittest.main()