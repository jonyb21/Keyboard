import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestStartupContract(unittest.TestCase):
    def test_startup_is_hidden_and_uses_collision_free_description(self):
        text = (ROOT / "startup" / "install-startup.ps1").read_text(encoding="utf-8")
        self.assertIn("$sc.WindowStyle      = 7", text)
        self.assertIn("F13-F15/F18-F24", text)
        self.assertNotIn("F13-F22 bindings", text)


if __name__ == "__main__":
    unittest.main()
