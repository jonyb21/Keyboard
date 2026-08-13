"""Gate tests for public services.hid package metadata."""

import unittest

from services import hid


class TestPackageMetadata(unittest.TestCase):
    def test_version_tracks_protocol_contract(self):
        self.assertEqual(hid.__version__, "1.3.0")

    def test_package_docs_list_every_cli_command(self):
        for command in ("status", "battery", "light", "clock", "screen", "remap"):
            with self.subTest(command=command):
                self.assertIn(command, hid.__doc__)


if __name__ == "__main__":
    unittest.main()
