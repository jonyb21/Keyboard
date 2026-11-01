import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location("bridge", pathlib.Path(__file__).parents[1] / "interception_bridge.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class BridgeTests(unittest.TestCase):
    def test_is_aula_matches_wired_and_dongle_only(self):
        self.assertTrue(bridge.is_aula(r"HID\VID_0C45&PID_800A&MI_01&Col03"))
        self.assertTrue(bridge.is_aula(r"hid\vid_05ac&pid_024f&mi_03"))
        self.assertFalse(bridge.is_aula(r"HID\VID_36B0&PID_304C&MI_00"))  # Cidoo V21

    def test_extended_media_key_sends_its_vk(self):
        seen = []
        to_vk = lambda sc: seen.append(sc) or (0xAD if sc == 0xE020 else 0)
        self.assertEqual(bridge.input_fields(0x20, bridge.KEY_E0, to_vk), (0xAD, 0x20, bridge.KEYEVENTF_EXTENDEDKEY))
        self.assertEqual(seen, [0xE020])

    def test_release_and_unmapped_scancode(self):
        vk, scan, flags = bridge.input_fields(0x64, bridge.KEY_UP, lambda sc: 0)
        self.assertEqual((vk, scan), (0, 0x64))
        self.assertEqual(flags, bridge.KEYEVENTF_KEYUP | bridge.KEYEVENTF_SCANCODE)


if __name__ == "__main__":
    unittest.main()
