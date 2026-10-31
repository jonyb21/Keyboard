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


if __name__ == "__main__":
    unittest.main()
