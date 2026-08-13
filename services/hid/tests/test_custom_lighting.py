"""Gate tests for named F75 Max stock and per-key lighting profiles."""

import json
import tempfile
import unittest
from pathlib import Path

from services.hid import custom_lighting, protocol
from services.hid.protocol import ProtocolError


class TestBuiltInProfiles(unittest.TestCase):
    def test_expected_profiles_compile(self):
        self.assertEqual(
            custom_lighting.available_profiles(), ("Focus Core", "Aurora")
        )
        focus = custom_lighting.compile_profile("focus core")
        aurora = custom_lighting.compile_profile("AURORA")
        self.assertEqual(focus.stock.mode, protocol.resolve_mode("Static"))
        self.assertEqual(aurora.stock.mode, protocol.resolve_mode("Flowing"))
        self.assertTrue(aurora.stock.colorful)
        self.assertIn("capture_required", custom_lighting.CAPTURE_REQUIRED)

    def test_all_layout_keys_have_unique_canonical_light_indices(self):
        layout = json.loads(custom_lighting.LAYOUT_PATH.read_text(encoding="utf-8"))
        profile = custom_lighting.compile_profile("Focus Core")
        indices = [key["light_index"] for key in layout["keys"]]
        self.assertEqual(len(indices), 80)
        self.assertEqual(len(indices), len(set(indices)))
        self.assertEqual([key.light_index for key in profile.keys], sorted(indices))
        self.assertEqual(len(profile.table), 576)

    def test_profile_colors_land_at_light_indices(self):
        focus = custom_lighting.compile_profile("Focus Core")
        by_name = {key.name: key for key in focus.keys}
        self.assertEqual(by_name["W"].color_hex, "17BFFF")
        self.assertEqual(by_name["Space"].color_hex, "8DEBFF")
        self.assertEqual(by_name["F1"].color_hex, "06142B")
        for key in focus.keys:
            offset = key.light_index * 4
            self.assertEqual(
                focus.table[offset : offset + 4],
                bytes((key.light_index, key.red, key.green, key.blue)),
            )

    def test_compiled_per_key_data_contains_no_wire_or_remap_opcode(self):
        for name in custom_lighting.available_profiles():
            table = custom_lighting.compile_profile(name).table
            # Compiler output is data-only. It must never smuggle an unverified
            # 04 23 or the normal/FN remap opcodes into a command sequence.
            self.assertNotEqual(table[:2], b"\x04\x23")
            self.assertNotEqual(table[:2], b"\x04\x11")
            self.assertNotEqual(table[:2], b"\x04\x27")

    def test_live_per_key_path_always_refuses(self):
        with self.assertRaisesRegex(ProtocolError, "capture_required"):
            custom_lighting.require_per_key_capture()


class TestSchemaValidation(unittest.TestCase):
    def test_unknown_keys_duplicate_assignment_and_bad_color_rejected(self):
        base = json.loads(custom_lighting.PROFILE_PATH.read_text(encoding="utf-8"))
        mutations = []
        unknown = json.loads(json.dumps(base))
        unknown["profiles"][0]["per_key"]["groups"][0]["keys"].append("NoSuchKey")
        mutations.append(unknown)
        duplicate = json.loads(json.dumps(base))
        duplicate["profiles"][0]["per_key"]["groups"][1]["keys"].append("W")
        mutations.append(duplicate)
        bad_color = json.loads(json.dumps(base))
        bad_color["profiles"][0]["per_key"]["default"] = "purple"
        mutations.append(bad_color)
        bad_stock_type = json.loads(json.dumps(base))
        bad_stock_type["profiles"][0]["stock"]["brightness"] = "2"
        mutations.append(bad_stock_type)
        bad_boolean = json.loads(json.dumps(base))
        bad_boolean["profiles"][0]["stock"]["colorful"] = 1
        mutations.append(bad_boolean)

        with tempfile.TemporaryDirectory() as directory:
            for index, data in enumerate(mutations):
                with self.subTest(index=index):
                    path = Path(directory, f"bad-{index}.json")
                    path.write_text(json.dumps(data), encoding="utf-8")
                    with self.assertRaises(ProtocolError):
                        custom_lighting.compile_profile("Focus Core", profile_path=path)

    def test_missing_light_index_rejected(self):
        layout = json.loads(custom_lighting.LAYOUT_PATH.read_text(encoding="utf-8"))
        del layout["keys"][0]["light_index"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "layout.json")
            path.write_text(json.dumps(layout), encoding="utf-8")
            with self.assertRaisesRegex(ProtocolError, "light_index"):
                custom_lighting.compile_profile("Focus Core", layout_path=path)


if __name__ == "__main__":
    unittest.main()
