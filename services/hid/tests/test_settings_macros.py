"""Golden-byte tests for device settings (04 17), the remap action
vocabulary, Fn-layer CLI plumbing, macros (04 19 / 04 15) and the LCD frame
delay override. Offsets are payload offsets (no report id); spec:
docs command-table.md sections 2.1, 2.7, 2.8, 2.10. No hardware is touched.
"""

import json
import os
import tempfile
import unittest

from services.hid import macros, protocol, remap, screen
from services.hid.protocol import DeviceSettings, KeyRemap, ProtocolError
from services.hid.tests.test_cli import FakeCliTransport, run_cli
from services.hid.tests.test_device import WIRED_ENUM

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
BASELINE = os.path.join(DATA, "settings_baseline.json")


def tmp_json(doc) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(doc, fh)
    return fh.name


def slot(send, position="F5"):
    return remap.resolve_mapping([{"position": position, "send": send}])[0]


def slot_bytes(r: KeyRemap):
    return (r.action, r.param1, r.param2, r.param3)


class TestSettingsPackets(unittest.TestCase):
    def test_init_golden(self):
        init = protocol.build_wired_settings_init()
        self.assertEqual(len(init), 64)
        self.assertEqual(init[:9], bytes([0x04, 0x17, 0x01, 0, 0, 0, 0, 0, 0x01]))
        self.assertEqual(init[9:], bytes(55))

    def test_data_golden_baseline_and_all_set(self):
        data = protocol.build_wired_settings_data(DeviceSettings(0, 0, 0, 0, 2, 2))
        self.assertEqual(data[:9], bytes([0, 1, 0, 0, 0, 0, 2, 0, 2]))
        self.assertEqual(data[9:62], bytes(53))
        self.assertEqual(data[62:], b"\xaa\x55")
        data = protocol.build_wired_settings_data(DeviceSettings(1, 1, 1, 1, 3, 5))
        self.assertEqual(data[:9], bytes([0, 1, 1, 1, 1, 1, 3, 0, 5]))

    def test_validation(self):
        for bad in ({"sleep_time": 4}, {"key_respondtime": 0}, {"key_respondtime": 6},
                    {"fn_switch": 2}, {"disable_win": -1}, {"disable_altf4": "1"}):
            fields = dict(disable_alttab=0, disable_altf4=0, disable_win=0,
                          fn_switch=0, sleep_time=2, key_respondtime=2)
            fields.update(bad)
            with self.assertRaises(ProtocolError, msg=bad):
                DeviceSettings(**fields).validate()
        with self.assertRaises(TypeError):  # no silent defaults
            DeviceSettings(sleep_time=1)

    def test_baseline_file_matches_vendor_db_values(self):
        doc = json.load(open(BASELINE, encoding="utf-8"))["settings"]
        self.assertEqual(
            doc,
            {"disable_alttab": 0, "disable_altf4": 0, "disable_win": 0,
             "fn_switch": 0, "sleep_time": 2, "key_respondtime": 2},
        )

    def test_client_sequence(self):
        from services.hid.tests.test_client import FakeClock, MockTransport, make_client

        clock = FakeClock()
        transport = MockTransport(clock)
        client = make_client(clock, transport)
        client.apply_settings(DeviceSettings(0, 0, 0, 0, 2, 2))
        sent = [p for kind, p, _ in transport.ops if kind == "feature"]
        self.assertEqual([p[:2] for p in sent], [b"\x04\x18", b"\x04\x17", b"\x00\x01", b"\x04\x02"])
        kinds = [k for k, _, _ in transport.ops]
        # readback after begin, init, apply; none after the data page
        self.assertEqual(
            kinds,
            ["feature", "get_feature", "feature", "get_feature", "feature", "feature", "get_feature"],
        )


class TestSettingsCli(unittest.TestCase):
    def test_show_baseline_never_touches_hardware(self):
        code, text, calls = run_cli(["settings", "show", "--file", BASELINE])
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertIn("wired <- [settings-init] 04 17 01 00 00 00 00 00 01", text)
        self.assertNotIn("04 f0", text.lower())

    def test_missing_field_is_refused(self):
        code, text, _ = run_cli(["settings", "show", "--sleep-time", "1"])
        self.assertEqual(code, 2)
        self.assertIn("missing", text)

    def test_flags_override_file_and_range_checked(self):
        code, text, _ = run_cli(["settings", "show", "--file", BASELINE, "--sleep-time", "3"])
        self.assertEqual(code, 0)
        self.assertIn("setting: sleep_time=3", text)
        code, text, _ = run_cli(["settings", "show", "--file", BASELINE, "--key-respondtime", "9"])
        self.assertEqual(code, 2)

    def test_unknown_file_field_refused(self):
        path = tmp_json({"settings": {"bogus": 1}})
        code, text, _ = run_cli(["settings", "show", "--file", path])
        self.assertEqual(code, 2)
        self.assertIn("unknown settings fields", text)

    def test_dry_run_apply_needs_no_device_and_live_apply_sends_four(self):
        code, _, calls = run_cli(["--dry-run", "settings", "apply", "--file", BASELINE])
        self.assertEqual((code, calls), (0, []))
        transport = FakeCliTransport()
        code, text, _ = run_cli(
            ["settings", "apply", "--file", BASELINE], enum=WIRED_ENUM, transport=transport
        )
        self.assertEqual(code, 0, text)
        self.assertEqual(len(transport.features), 4)
        self.assertEqual(transport.features[2][:9], bytes([0, 1, 0, 0, 0, 0, 2, 0, 2]))


class TestRemapVocabulary(unittest.TestCase):
    def test_plain_strings_unchanged(self):
        self.assertEqual(slot_bytes(slot("A")), (0x02, 0, 0x04, 0))
        self.assertEqual(slot_bytes(slot("Ctrl_R")), (0x02, 0x10, 0, 0))

    def test_combo(self):
        self.assertEqual(slot_bytes(slot({"combo": "Ctrl+Shift+Esc"})), (0x02, 0x03, 0x29, 0))
        self.assertEqual(slot_bytes(slot({"combo": "Win+D"})), (0x02, 0x08, 0x07, 0))
        self.assertEqual(slot_bytes(slot({"combo": "ctrl+alt+Delete"})), (0x02, 0x05, 0x4C, 0))
        self.assertEqual(slot_bytes(slot({"combo": "Ctrl_R"})), slot_bytes(slot("Ctrl_R")))
        self.assertEqual(slot_bytes(slot({"combo": "Ctrl+Shift"})), (0x02, 0x03, 0, 0))
        with self.assertRaises(ProtocolError):
            slot({"combo": "Ctrl+A+B"})

    def test_ui_alias_combos_resolve(self):
        # names bridge-ui.js sendFor() rewrites UI aliases to (SEND map)
        for tail in ("Shift_R", "Ctrl_R", "Alt_R", "ScrollLock", "NumLock", "KeypadAsterisk", "Pause", "0x68"):
            with self.subTest(tail=tail):
                slot({"combo": "Ctrl+" + tail})
        slot({"combo": "Win_L+Alt_R"})

    def test_media(self):
        for name, lo, hi in (("PlayPause", 0xCD, 0), ("Stop", 0xB7, 0), ("Prev", 0xB6, 0),
                             ("Next", 0xB5, 0), ("VolumeUp", 0xE9, 0), ("VolumeDown", 0xEA, 0),
                             ("Mute", 0xE2, 0), ("MyComputer", 0x94, 0x01), ("Calculator", 0x92, 0x01),
                             ("Mail", 0x8A, 0x01), ("MediaSelect", 0x83, 0x01), ("WebHome", 0x23, 0x02),
                             ("Refresh", 0x27, 0x02), ("WebStop", 0x26, 0x02), ("Back", 0x24, 0x02),
                             ("Forward", 0x25, 0x02), ("Favorites", 0x2A, 0x02), ("Search", 0x21, 0x02)):
            self.assertEqual(slot_bytes(slot({"media": name})), (0x03, lo, hi, 0), name)
        with self.assertRaises(ProtocolError):
            slot({"media": "Nope"})

    def test_mouse(self):
        for name, p1, p2 in (("Left", 1, 1), ("Right", 1, 2), ("Middle", 1, 4), ("Back", 1, 8),
                             ("Button4", 1, 8), ("Forward", 1, 0x10), ("Button5", 1, 0x10),
                             ("ScrollUp", 3, 1), ("ScrollDown", 3, 0xFF)):
            self.assertEqual(slot_bytes(slot({"mouse": name})), (0x01, p1, p2, 0), name)

    def test_macro_and_disabled(self):
        self.assertEqual(
            slot_bytes(slot({"macro": 2, "mode": "once", "count": 5})), (0x06, 2, 0, 5)
        )
        self.assertEqual(slot_bytes(slot({"macro": 0})), (0x06, 0, 0, 1))
        for bad in ({"macro": 100}, {"macro": 0, "count": 0}, {"macro": 0, "count": 256},
                    {"macro": 0, "mode": "toggle"}, {"macro": 0, "extra": 1}):
            with self.assertRaises(ProtocolError, msg=bad):
                slot(bad)
        self.assertEqual(slot_bytes(slot("Disabled")), (0x05, 0x03, 0, 0))

    def test_typed_send_errors(self):
        for bad in ({}, {"combo": "A", "media": "Mute"}, {"key": "A"}, 5):
            with self.assertRaises(ProtocolError, msg=bad):
                slot(bad)

    def test_describe_marks_inferred(self):
        layout = remap.load_layout()
        line = remap.describe_remap(slot("Disabled"), layout)
        self.assertIn("[INFERRED: disable key]", line)
        self.assertNotIn("INFERRED", remap.describe_remap(slot("A"), layout))

    def test_fn_key_guard_still_applies_to_typed_send(self):
        with self.assertRaises(ProtocolError):
            remap.resolve_mapping([{"position": "Fn", "send": {"media": "Mute"}}])


class TestFnLayerCli(unittest.TestCase):
    MAPPING = None

    @classmethod
    def setUpClass(cls):
        cls.MAPPING = tmp_json({"mappings": [{"position": "F5", "send": {"media": "PlayPause"}}]})

    def test_show_defaults_to_normal_and_fn_uses_04_27(self):
        _, normal, _ = run_cli(["remap", "show", self.MAPPING])
        _, fn, _ = run_cli(["remap", "show", self.MAPPING, "--layer", "fn"])
        self.assertIn("[remap-init-normal] 04 11", normal)
        self.assertIn("[remap-init-fn] 04 27", fn)
        self.assertEqual(normal.replace("04 11", "04 27").replace("-normal]", "-fn]"), fn)

    def test_live_apply_fn_layer_sends_04_27(self):
        transport = FakeCliTransport()
        code, text, _ = run_cli(
            ["remap", "apply", self.MAPPING, "--layer", "fn"], enum=WIRED_ENUM, transport=transport
        )
        self.assertEqual(code, 0, text)
        self.assertEqual(transport.features[1][:2], b"\x04\x27")
        self.assertIn("(fn layer)", text)

    def test_f16_f17_consistent_across_layers(self):
        # The same resolver feeds both layers; F16/F17 (0x6B/0x6C) resolve identically.
        for token in ("0x6B", "0x6C"):
            a = slot(token)
            self.assertEqual(slot_bytes(a), (0x02, 0, int(token, 16), 0))
        _, normal, _ = run_cli(["remap", "show", self.MAPPING])
        _, fn, _ = run_cli(["remap", "show", self.MAPPING, "--layer", "fn"])
        self.assertEqual(
            [l for l in normal.splitlines() if l.startswith("slot:")],
            [l for l in fn.splitlines() if l.startswith("slot:")],
        )


EXAMPLE = [
    {"name": "a", "events": [{"down": "A"}, {"delay": 50}, {"up": "A"}]},
    {"name": "empty", "events": []},
    {"name": "click", "events": [{"mouse_down": "Left"}, {"delay": 5}, {"mouse_up": "Left"}]},
]


class TestMacros(unittest.TestCase):
    def test_blob_golden(self):
        blob = macros.build_blob(EXAMPLE)
        self.assertEqual(len(blob), 400 + 20 + 20)
        self.assertEqual(blob[0:4], bytes([0x90, 0x01, 0, 0]))  # offset 400
        self.assertEqual(blob[4:8], b"\xff\xff\xff\xff")  # empty macro
        self.assertEqual(blob[8:12], bytes([0xA4, 0x01, 0, 0]))  # offset 420
        self.assertEqual(blob[12:16], b"\xff\xff\xff\xff")  # unused slot
        self.assertEqual(blob[396:400], b"\xff\xff\xff\xff")
        self.assertEqual(
            blob[400:420],
            bytes([3, 0, 0, 0, 0, 0, 0, 0,
                   0, 0, 0x04, 0xB0,  # A down
                   50, 0, 0, 0x50,  # delay 50
                   0, 0, 0x04, 0x30]),  # A up
        )
        self.assertEqual(
            blob[420:440],
            bytes([3, 0, 0, 0, 0, 0, 0, 0,
                   0, 0, 0x01, 0x90,  # mouse left down
                   10, 0, 0, 0x50,  # delay clamped up to 10
                   0, 0, 0x01, 0x10]),  # mouse left up
        )

    def test_mouse_codes_and_delay_encoding(self):
        blob = macros.build_blob(
            [{"events": [{"mouse_down": "Middle"}, {"mouse_up": "Right"}, {"delay": 0x1234}]}]
        )
        self.assertEqual(blob[408:420], bytes([0, 0, 4, 0x90, 0, 0, 2, 0x10, 0x34, 0x12, 0, 0x50]))

    def test_pages_rule_and_trailer(self):
        pages = macros.build_pages(macros.build_blob(EXAMPLE))
        self.assertEqual(len(pages), 7 + 1)  # ceil(440/64) + 1
        self.assertTrue(all(len(p) == 64 for p in pages))
        self.assertEqual(pages[-1][-2:], b"\xaa\x55")
        self.assertEqual(pages[-1][:-2], bytes(62))
        # exact multiple of 64 still gets the extra page
        self.assertEqual(len(macros.build_pages(bytes(448))), 8)

    def test_transactions_sequence(self):
        steps = macros.macro_transactions(EXAMPLE)
        labels = [l for l, _ in steps]
        self.assertEqual(labels[:2], ["macro-prepare", "macro-init"])
        self.assertEqual(labels[-1], "apply")
        self.assertEqual(steps[0][1][:2], b"\x04\x19")
        self.assertEqual(steps[1][1][:9], bytes([0x04, 0x15, 0, 0, 0, 0, 0, 0, 8]))
        self.assertEqual(len(steps), 2 + 8 + 1)
        self.assertNotIn(b"\x04\x18", [p[:2] for _, p in steps])
        self.assertNotIn(b"\x04\xf0", [p[:2] for _, p in steps])

    def test_size_guard(self):
        ok = [{"events": [{"delay": 20}] * 337}]
        macros.build_blob(ok)
        with self.assertRaises(ProtocolError):
            macros.build_blob([{"events": [{"delay": 20}] * 338}])
        with self.assertRaises(ProtocolError):
            macros.build_blob([{"events": []}] * 101)

    def test_bad_events(self):
        for ev in ({"down": "Nope"}, {"mouse_down": "Back"}, {"delay": "x"}, {"delay": 70000},
                   {"bogus": 1}, {"down": "A", "up": "A"}):
            with self.assertRaises(ProtocolError, msg=ev):
                macros.build_blob([{"events": [ev]}])
        with self.assertRaises(ProtocolError):
            macros.build_blob([{"name": "no events"}])

    def test_cli_show_and_apply(self):
        path = tmp_json({"macros": EXAMPLE})
        code, text, calls = run_cli(["macro", "show", path])
        self.assertEqual((code, calls), (0, []))
        self.assertIn("[macro-prepare] 04 19", text)
        self.assertIn("[macro-init] 04 15 00 00 00 00 00 00 08", text)
        code, _, calls = run_cli(["--dry-run", "macro", "apply", path])
        self.assertEqual((code, calls), (0, []))
        transport = FakeCliTransport()
        code, text, _ = run_cli(["macro", "apply", path], enum=WIRED_ENUM, transport=transport)
        self.assertEqual(code, 0, text)
        self.assertEqual(len(transport.features), 2 + 8 + 1)
        self.assertEqual(transport.features[0][:2], b"\x04\x19")
        self.assertEqual(transport.features[-1][:2], b"\x04\x02")

    def test_client_readbacks_only_on_control_packets(self):
        from services.hid.tests.test_client import FakeClock, MockTransport, make_client

        clock = FakeClock()
        transport = MockTransport(clock)
        client = make_client(clock, transport)
        client.apply_macros(macros.macro_transactions(EXAMPLE))
        self.assertEqual(sum(1 for k, _, _ in transport.ops if k == "get_feature"), 3)


class TestFrameDelayOverride(unittest.TestCase):
    def png(self):
        from PIL import Image

        path = os.path.join(tempfile.mkdtemp(), "x.png")
        Image.new("RGB", (128, 128), (1, 2, 3)).save(path)
        return path

    def test_override_applies_to_every_frame_even_stills(self):
        prepared = screen.prepare_image(self.png(), delay_ms=100)
        self.assertEqual(prepared.delays, (50,))
        self.assertEqual(prepared.stream[1], 50)
        self.assertEqual(screen.prepare_image(self.png()).delays, (screen.STILL_DELAY,))

    def test_range(self):
        for bad in (29, 501, True, 1.5):
            with self.assertRaises(screen.ScreenError):
                screen.prepare_image(self.png(), delay_ms=bad)
        self.assertEqual(screen.prepare_image(self.png(), delay_ms=30).delays, (15,))
        self.assertEqual(screen.prepare_image(self.png(), delay_ms=500).delays, (250,))

    def test_cli_inspect_reports_delay(self):
        code, text, _ = run_cli(["screen", "inspect", self.png(), "--delay-ms", "100"])
        self.assertEqual(code, 0, text)
        self.assertIn("50", json.loads(text)["delays"].__repr__())


if __name__ == "__main__":
    unittest.main()
