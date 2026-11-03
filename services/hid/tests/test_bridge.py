"""Gate tests for services.hid.bridge: allow-list, dry-run gating, board map."""

import json
import unittest
from pathlib import Path

from services.hid import bridge

BOARD_MAP = Path(__file__).resolve().parents[1] / "data" / "board_map.json"


def no_hardware():
    raise AssertionError("dry-run must not enumerate hardware")


class BridgeTests(unittest.TestCase):
    def run_req(self, req, live_allowed=False):
        return bridge.handle(req, live_allowed, enumerator=no_hardware)

    def test_unknown_and_reset_ops_are_refused(self):
        for op in ("remap_reset", "battery", "rm", None):
            reply = self.run_req({"op": op})
            self.assertFalse(reply["ok"])
            self.assertIn("not allowed", reply["output"])

    def test_writes_are_dry_run_without_env_flag(self):
        reply = self.run_req({"op": "clock_sync", "live": True})
        self.assertTrue(reply["dry_run"])
        self.assertEqual(reply["argv"][0], "--dry-run")
        self.assertTrue(reply["ok"])

    def test_writes_are_dry_run_without_live_request(self):
        reply = self.run_req({"op": "clock_sync"}, live_allowed=True)
        self.assertTrue(reply["dry_run"])

    def test_live_needs_both_flags(self):
        req = {"op": "clock_sync", "live": True}
        argv = bridge.build_argv(req, ".")
        self.assertEqual(argv, ["clock", "sync"])
        sent = []
        reply = bridge.handle(req, True, enumerator=lambda: sent.append(1) or [])
        self.assertFalse(reply["dry_run"])
        self.assertEqual(sent, [1])

    def test_light_dry_run_prints_wire_hex(self):
        reply = self.run_req({"op": "light", "mode": "Rolling", "brightness": 4, "speed": 3, "color": "#7D42FF"})
        self.assertTrue(reply["ok"], reply["output"])
        self.assertIn("wired <-", reply["output"])

    def test_board_map_send_keeps_every_mapping(self):
        mappings = json.loads(BOARD_MAP.read_text(encoding="utf-8"))["mappings"]
        reply = self.run_req({"op": "remap_apply", "mappings": mappings})
        self.assertTrue(reply["ok"], reply["output"])
        slots = [line for line in reply["output"].splitlines() if line.startswith("slot:")]
        self.assertEqual(len(slots), len(mappings))
        for m in mappings:
            self.assertTrue(any(m["position"] in s for s in slots), m)

    def test_bad_mapping_is_an_error_not_a_crash(self):
        reply = self.run_req({"op": "remap_show", "mappings": [{"position": "Nope", "send": "A"}]})
        self.assertFalse(reply["ok"])
        self.assertIn("error", reply["output"])

    # -- settings / macros / layer / frame delay ------------------------------

    SETTINGS = {"disable_alttab": 0, "disable_altf4": 0, "disable_win": 0,
                "fn_switch": 0, "sleep_time": 2, "key_respondtime": 2}
    MACROS = [{"name": "a", "events": [{"down": "A"}, {"delay": 50}, {"up": "A"}]}]

    def test_new_write_ops_are_gated_and_never_build_a_device(self):
        def no_transport(endpoint):
            raise AssertionError("dry-run must not construct a device")

        reqs = [
            {"op": "settings_apply", "settings": self.SETTINGS},
            {"op": "macro_apply", "macros": self.MACROS},
            {"op": "remap_apply", "layer": "fn", "mappings": [{"position": "F5", "send": {"media": "Mute"}}]},
        ]
        for req in reqs:
            for live_allowed, live in ((False, True), (True, False), (False, False)):
                reply = bridge.handle(
                    {**req, "live": live}, live_allowed,
                    enumerator=no_hardware, transport_factory=no_transport,
                )
                self.assertTrue(reply["ok"], reply["output"])
                self.assertTrue(reply["dry_run"], req["op"])
                self.assertEqual(reply["argv"][0], "--dry-run")

    def test_new_write_ops_go_live_only_with_both_gates(self):
        for req in (
            {"op": "settings_apply", "settings": self.SETTINGS, "live": True},
            {"op": "macro_apply", "macros": self.MACROS, "live": True},
        ):
            seen = []
            reply = bridge.handle(req, True, enumerator=lambda: seen.append(1) or [])
            self.assertFalse(reply["dry_run"])
            self.assertEqual(seen, [1])
            self.assertNotEqual(reply["argv"][0], "--dry-run")

    def test_settings_apply_requires_every_field(self):
        partial = {k: v for k, v in self.SETTINGS.items() if k != "sleep_time"}
        reply = self.run_req({"op": "settings_apply", "settings": partial})
        self.assertFalse(reply["ok"])
        self.assertIn("missing sleep_time", reply["output"])
        reply = self.run_req({"op": "settings_apply"})
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["code"], 2)

    def test_settings_and_macro_dry_run_output(self):
        reply = self.run_req({"op": "settings_apply", "settings": self.SETTINGS})
        self.assertIn("[settings-data] 00 01 00 00 00 00 02 00 02", reply["output"])
        reply = self.run_req({"op": "macro_apply", "macros": self.MACROS})
        self.assertIn("[macro-prepare] 04 19", reply["output"])

    def test_show_ops_are_not_writes(self):
        for req in ({"op": "settings_show", "settings": self.SETTINGS},
                    {"op": "macro_show", "macros": self.MACROS}):
            reply = self.run_req(req)
            self.assertTrue(reply["ok"], reply["output"])
            self.assertFalse(reply["dry_run"])
            self.assertNotEqual(reply["argv"][0], "--dry-run")

    def test_remap_layer(self):
        maps = [{"position": "F5", "send": {"combo": "Ctrl+Shift+Esc"}}]
        reply = self.run_req({"op": "remap_apply", "layer": "fn", "mappings": maps})
        self.assertIn("[remap-init-fn] 04 27", reply["output"])
        reply = self.run_req({"op": "remap_apply", "mappings": maps})
        self.assertIn("[remap-init-normal] 04 11", reply["output"])
        reply = self.run_req({"op": "remap_apply", "layer": "bogus", "mappings": maps})
        self.assertFalse(reply["ok"])

    def test_screen_upload_delay_ms_argv(self):
        argv = bridge.build_argv({"op": "screen_upload", "path": "x.png", "delay_ms": 120}, ".")
        self.assertEqual(argv[-2:], ["--delay-ms", "120"])
        argv = bridge.build_argv({"op": "screen_upload", "path": "x.png"}, ".")
        self.assertNotIn("--delay-ms", argv)
        reply = self.run_req({"op": "screen_upload", "path": "x.png", "delay_ms": 120})
        self.assertTrue(reply["dry_run"])


if __name__ == "__main__":
    unittest.main()
