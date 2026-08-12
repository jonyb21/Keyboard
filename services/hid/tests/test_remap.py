"""Gate tests for key remapping: golden tables, name resolution, Fn guard,
reset-as-identity, client sequencing, CLI, and the no-hardware property.

Golden bytes are hand-derived from [F108] pkg/aula/remap.go and
ai-docs/key-remap-protocol.md (see contracts/hid_protocol.md section 6.4).
Everything runs against mocks; HidapiTransport is never constructed
(test-enforced below).
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from services.hid import cli, protocol, remap
from services.hid.protocol import KeyRemap, ProtocolError
from services.hid.tests.test_cli import FakeCliTransport, run_cli
from services.hid.tests.test_client import FakeClock, MockTransport, make_client
from services.hid.tests.test_device import DONGLE_ENUM, WIRED_ENUM, rec

KEYCAP_TRUTH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures",
    "historical_keycap_truth_2026-08-10.json",
)
BOARD_MAP = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "board_map.json",
)
HID_README = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "README.md",
)
PROJECT_LOG = os.path.join(
    os.path.dirname(
        os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
    ),
    "docs",
    "PROJECT-LOG.md",
)

FN_GUARD_MESSAGE = "Fn key is the hardware layer key and cannot be remapped"


def write_mapping(entries) -> str:
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    )
    with handle as fh:
        json.dump(entries, fh)
    return handle.name


class TestLayoutData(unittest.TestCase):
    def test_layout_matches_vendor_table(self):
        layout = remap.load_layout()
        self.assertEqual(len(layout), 80)  # 80-key 75% board
        indices = [key.key_index for key in layout.values()]
        self.assertEqual(len(indices), len(set(indices)))
        # Spot checks against the rgb-keyboard.xml table.
        self.assertEqual(layout["esc"].key_index, 1)
        self.assertEqual(layout["esc"].usage, 0x29)
        self.assertEqual(layout["f7"].key_index, 8)
        self.assertEqual((layout["f7"].row, layout["f7"].col), (0, 7))
        self.assertEqual(layout["capslock"].key_index, 55)
        self.assertEqual(layout["backspace"].key_index, 103)
        self.assertEqual(layout["pageup"].key_index, 118)
        self.assertEqual(layout["pageup"].usage, 0x4B)
        self.assertEqual(layout["pagedown"].key_index, 121)

    def test_fn_key_is_index_96_pseudo_usage_af(self):
        layout = remap.load_layout()
        self.assertEqual(layout["fn"].key_index, protocol.FN_KEY_INDEX)
        self.assertEqual(layout["fn"].usage, protocol.FN_PSEUDO_USAGE)

    def test_max_key_index_fits_remap_table(self):
        layout = remap.load_layout()
        highest = max(key.key_index for key in layout.values())
        self.assertEqual(highest, 121)
        self.assertLessEqual(highest, protocol.REMAP_MAX_KEY_INDEX)


class TestRemapBuilders(unittest.TestCase):
    def test_remap_init_golden(self):
        # [F108] key-remap-protocol.md "Step 2: Init Command":
        # byte0=0x04, byte1=0x11 (normal) / 0x27 (FN), byte8=0x09.
        normal = protocol.build_wired_remap_init()
        self.assertEqual(normal.hex(), "0411" + "00" * 6 + "09" + "00" * 55)
        fn = protocol.build_wired_remap_init(fn_layer=True)
        self.assertEqual(fn.hex(), "0427" + "00" * 6 + "09" + "00" * 55)

    def test_table_golden_capslock_to_lctrl(self):
        # Exact wire example from [F108] key-remap-protocol.md "Wire Format
        # Example": CapsLock (key_index 55) -> Left Ctrl gives 02 01 00 00
        # at offset 220, trailer AA 55 at 574-575, all else zero.
        table = protocol.build_remap_table(
            [protocol.make_key_remap(55, 0xE0)]
        )
        golden = bytearray(576)
        golden[220:224] = bytes([0x02, 0x01, 0x00, 0x00])
        golden[574] = 0xAA
        golden[575] = 0x55
        self.assertEqual(table, bytes(golden))

    def test_table_golden_f7_to_f9(self):
        # F7 is key_index 8 (f75max_layout.json); F9 usage 0x42 ->
        # slot 02 00 42 00 at offset 32 (non-modifier: usage in param2 per
        # [F108] remap.go NewKeySwap).
        table = protocol.build_remap_table([protocol.make_key_remap(8, 0x42)])
        golden = bytearray(576)
        golden[32:36] = bytes([0x02, 0x00, 0x42, 0x00])
        golden[574] = 0xAA
        golden[575] = 0x55
        self.assertEqual(table, bytes(golden))

    def test_table_golden_pageup_to_keypad_asterisk(self):
        # PageUp is key_index 118 -> offset 472; Keypad * usage 0x55.
        table = protocol.build_remap_table(
            [protocol.make_key_remap(118, 0x55)]
        )
        golden = bytearray(576)
        golden[472:476] = bytes([0x02, 0x00, 0x55, 0x00])
        golden[574] = 0xAA
        golden[575] = 0x55
        self.assertEqual(table, bytes(golden))

    def test_modifier_targets_use_bitmask_param1(self):
        # [F108] remap.go hidToModifierBit: 0xE0..0xE7 -> 01..80.
        expected = {
            0xE0: 0x01, 0xE1: 0x02, 0xE2: 0x04, 0xE3: 0x08,
            0xE4: 0x10, 0xE5: 0x20, 0xE6: 0x40, 0xE7: 0x80,
        }
        self.assertEqual(protocol.MODIFIER_BITS, expected)
        for usage, bit in expected.items():
            slot = protocol.make_key_remap(92, usage)
            self.assertEqual(
                (slot.action, slot.param1, slot.param2),
                (protocol.REMAP_ACTION_KEY, bit, 0x00),
            )

    def test_split_table_nine_packets(self):
        table = protocol.build_remap_table([])
        packets = protocol.split_remap_table(table)
        self.assertEqual(len(packets), protocol.REMAP_PACKET_COUNT)
        self.assertEqual(len(packets), 9)
        for packet in packets:
            self.assertEqual(len(packet), protocol.WIRED_REPORT_SIZE)
        self.assertEqual(b"".join(packets), table)
        with self.assertRaises(ProtocolError):
            protocol.split_remap_table(table[:-1])

    def test_fn_guard_key_index_96(self):
        with self.assertRaises(ProtocolError) as ctx:
            KeyRemap(key_index=96, action=protocol.REMAP_ACTION_KEY).validate()
        self.assertEqual(str(ctx.exception), FN_GUARD_MESSAGE)
        with self.assertRaises(ProtocolError) as ctx:
            protocol.build_remap_table([protocol.make_key_remap(96, 0x04)])
        self.assertEqual(str(ctx.exception), FN_GUARD_MESSAGE)

    def test_slot_bounds_and_duplicates(self):
        # Valid slots are 1..142 ([F108] remap.go skips idx 0 and any slot
        # touching the trailer area).
        protocol.build_remap_table([protocol.make_key_remap(142, 0x04)])
        for bad_index in (0, -1, 143, 200):
            with self.assertRaises(ProtocolError):
                KeyRemap(
                    key_index=bad_index, action=protocol.REMAP_ACTION_KEY
                ).validate()
        with self.assertRaises(ProtocolError):
            protocol.build_remap_table(
                [
                    protocol.make_key_remap(8, 0x42),
                    protocol.make_key_remap(8, 0x40),
                ]
            )

    def test_make_key_remap_rejects_fn_pseudo_usage_and_zero(self):
        with self.assertRaises(ProtocolError):
            protocol.make_key_remap(8, 0xAF)
        with self.assertRaises(ProtocolError):
            protocol.make_key_remap(8, 0x00)


class TestNameResolution(unittest.TestCase):
    def test_canonical_board_map_uses_collision_free_host_tokens(self):
        entries = remap.load_mapping_file(BOARD_MAP)
        f_row = {
            entry["position"]: remap.resolve_send(entry["send"], remap.load_layout())
            for entry in entries
            if entry["position"].lower().startswith("f")
        }
        self.assertEqual(
            f_row,
            {
                "F1": 0x68,
                "F3": 0x69,
                "F5": 0x6A,
                "F6": 0x6D,
                "F7": 0x6E,
                "F8": 0x6F,
                "F9": 0x70,
                "F10": 0x71,
                "F11": 0x72,
                "F12": 0x73,
            },
        )
        self.assertTrue({0x6B, 0x6C}.isdisjoint(f_row.values()))

    def test_keycap_truth_resolves_to_expected_slots(self):
        entries = remap.load_mapping_file(KEYCAP_TRUTH)
        remaps = remap.resolve_mapping(entries)
        got = [
            (r.key_index, r.action, r.param1, r.param2, r.param3)
            for r in remaps
        ]
        expected = [
            (8, 0x02, 0x00, 0x42, 0x00),    # F7 -> F9
            (10, 0x02, 0x00, 0x40, 0x00),   # F9 -> F7
            (19, 0x02, 0x00, 0x46, 0x00),   # Backtick -> PrintScreen
            (92, 0x02, 0x04, 0x00, 0x00),   # Win_L -> Alt_L (bit 0x04)
            (93, 0x02, 0x08, 0x00, 0x00),   # Alt_L -> Win_L (bit 0x08)
            (95, 0x02, 0x10, 0x00, 0x00),   # Alt_R -> Ctrl_R (bit 0x10)
            (118, 0x02, 0x00, 0x55, 0x00),  # PageUp -> Keypad *
            (121, 0x02, 0x00, 0x54, 0x00),  # PageDown -> Keypad /
            (120, 0x02, 0x00, 0x56, 0x00),  # End -> Keypad -
        ]
        self.assertEqual(got, expected)

    def test_send_accepts_hex_and_is_case_insensitive(self):
        layout = remap.load_layout()
        self.assertEqual(remap.resolve_send("0x46", layout), 0x46)
        self.assertEqual(remap.resolve_send("PrintScreen", layout), 0x46)
        self.assertEqual(remap.resolve_send("printscreen", layout), 0x46)
        self.assertEqual(remap.resolve_send("KEYPADASTERISK", layout), 0x55)
        self.assertEqual(remap.resolve_send("numstar", layout), 0x55)  # alias
        self.assertEqual(remap.resolve_send("f9", layout), 0x42)  # layout name
        self.assertEqual(remap.resolve_send("Ctrl_R", layout), 0xE4)
        self.assertEqual(remap.resolve_send("Win_R", layout), 0xE7)

    def test_unknown_names_rejected(self):
        layout = remap.load_layout()
        with self.assertRaises(ProtocolError):
            remap.resolve_send("hyperkey", layout)
        with self.assertRaises(ProtocolError):
            remap.resolve_send("0xZZ", layout)
        with self.assertRaises(ProtocolError):
            remap.resolve_position("NumpadEnter", layout)
        with self.assertRaises(ProtocolError):
            remap.resolve_mapping([{"position": "F1"}])  # missing send

    def test_fn_rejected_as_position_and_send(self):
        with self.assertRaises(ProtocolError) as ctx:
            remap.resolve_mapping([{"position": "Fn", "send": "A"}])
        self.assertEqual(str(ctx.exception), FN_GUARD_MESSAGE)
        layout = remap.load_layout()
        with self.assertRaises(ProtocolError):
            remap.resolve_send("Fn", layout)
        with self.assertRaises(ProtocolError):
            remap.resolve_send("0xAF", layout)

    def test_duplicate_positions_rejected(self):
        with self.assertRaises(ProtocolError):
            remap.resolve_mapping(
                [
                    {"position": "F7", "send": "F9"},
                    {"position": "f7", "send": "F8"},
                ]
            )


class TestResetIdentity(unittest.TestCase):
    def test_default_mapping_is_identity(self):
        self.assertEqual(remap.default_mapping(), [])
        remaps = remap.resolve_mapping(remap.default_mapping())
        self.assertEqual(remaps, [])

    def test_identity_table_is_all_zero_plus_trailer(self):
        # [F108] remap.go ResetKeyRemap: nil remaps -> every slot
        # 00 00 00 00 (passthrough), only the trailer set.
        table = protocol.build_remap_table(
            remap.resolve_mapping(remap.default_mapping())
        )
        self.assertEqual(table, bytes(574) + bytes([0xAA, 0x55]))

    def test_client_reset_sends_identity_table(self):
        clock = FakeClock()
        transport = MockTransport(clock)
        make_client(clock, transport).reset_remap()
        features = [op[1] for op in transport.ops if op[0] == "feature"]
        sent_table = b"".join(features[2:11])
        self.assertEqual(sent_table, bytes(574) + bytes([0xAA, 0x55]))


class TestClientApplyRemap(unittest.TestCase):
    REMAPS = (
        protocol.make_key_remap(8, 0x42),
        protocol.make_key_remap(92, 0xE2),
    )

    def test_sequence_order_and_payloads(self):
        clock = FakeClock()
        transport = MockTransport(clock)
        make_client(clock, transport).apply_remap(self.REMAPS)

        kinds = [op[0] for op in transport.ops]
        self.assertEqual(
            kinds,
            [
                "feature", "get_feature",  # 04 18 begin + readback
                "feature", "get_feature",  # 04 11 remap init + readback
            ]
            + ["feature"] * 9              # table packets (no readback)
            + [
                "feature", "get_feature",  # 04 02 apply + readback
                "feature", "get_feature",  # 04 F0 finalize + readback
            ],
        )
        features = [op[1] for op in transport.ops if op[0] == "feature"]
        self.assertEqual(features[0], protocol.build_wired_begin())
        self.assertEqual(features[1], protocol.build_wired_remap_init())
        table = protocol.build_remap_table(self.REMAPS)
        self.assertEqual(features[2:11], protocol.split_remap_table(table))
        self.assertEqual(features[11], protocol.build_wired_apply())
        self.assertEqual(features[12], protocol.build_wired_finalize())

    def test_fn_layer_uses_04_27(self):
        clock = FakeClock()
        transport = MockTransport(clock)
        make_client(clock, transport).apply_remap(self.REMAPS, fn_layer=True)
        features = [op[1] for op in transport.ops if op[0] == "feature"]
        self.assertEqual(
            features[1], protocol.build_wired_remap_init(fn_layer=True)
        )

    def test_every_transfer_paced_35ms(self):
        clock = FakeClock()
        transport = MockTransport(clock)
        make_client(clock, transport).apply_remap(self.REMAPS)
        stamps = [op[2] for op in transport.ops]
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        self.assertEqual(len(gaps), 16)
        for gap in gaps:
            self.assertGreaterEqual(gap, protocol.COMMAND_DELAY_S - 1e-9)

    def test_fn_guard_blocks_before_any_transfer(self):
        clock = FakeClock()
        transport = MockTransport(clock)
        client = make_client(clock, transport)
        with self.assertRaises(ProtocolError):
            client.apply_remap(
                [KeyRemap(key_index=96, action=protocol.REMAP_ACTION_KEY)]
            )
        self.assertEqual(transport.ops, [])

    def test_apply_remap_never_constructs_hidapi_transport(self):
        with mock.patch(
            "services.hid.client.HidapiTransport",
            side_effect=AssertionError("hardware transport constructed"),
        ) as guard:
            clock = FakeClock()
            transport = MockTransport(clock)
            client = make_client(clock, transport)
            client.apply_remap(self.REMAPS)
            client.reset_remap()
            guard.assert_not_called()


class TestRemapCli(unittest.TestCase):
    def test_documented_apply_uses_only_canonical_map_and_repo_venv(self):
        sources = []
        for path in (HID_README, PROJECT_LOG):
            with open(path, encoding="utf-8") as stream:
                sources.append(stream.read())
        with open(BOARD_MAP, encoding="utf-8") as stream:
            sources.append(json.load(stream)["comment"])
        apply_lines = [
            line.strip()
            for source in sources
            for line in source.splitlines()
            if "remap apply" in line
        ]
        self.assertTrue(apply_lines)
        self.assertTrue(
            all("services/hid/data/board_map.json" in line for line in apply_lines),
            apply_lines,
        )
        self.assertTrue(
            all(".\\.venv\\Scripts\\python.exe" in line for line in apply_lines),
            apply_lines,
        )
        self.assertTrue(
            all("keycap_truth_2026-08-10.json" not in source for source in sources)
        )

    def expected_plan_output(self, remaps, layout):
        lines = [
            f"slot: {remap.describe_remap(entry, layout)}" for entry in remaps
        ]
        if not remaps:
            lines.append("slot: none (identity table, clears all remaps)")
        lines.extend(
            f"wired <- [{label}] {payload.hex(' ')}"
            for label, payload in remap.remap_transactions(remaps)
        )
        return "".join(line + "\n" for line in lines)

    def test_show_output_is_stable_and_golden(self):
        layout = remap.load_layout()
        remaps = remap.resolve_mapping(remap.load_mapping_file(KEYCAP_TRUTH))
        expected = self.expected_plan_output(remaps, layout)

        first = run_cli(["remap", "show", KEYCAP_TRUTH])
        second = run_cli(["remap", "show", KEYCAP_TRUTH])
        self.assertEqual(first[0], 0)
        self.assertEqual(first[2], [])  # transport factory never called
        self.assertEqual(first[1], expected)
        self.assertEqual(first[1], second[1])  # byte-for-byte stable
        # 13 transaction lines: begin, init, 9 table packets, apply, finalize.
        wire_lines = [
            line for line in first[1].splitlines() if line.startswith("wired <-")
        ]
        self.assertEqual(len(wire_lines), 13)
        self.assertIn("[remap-init-normal]", wire_lines[1])

    def test_show_never_needs_endpoints(self):
        code, text, calls = run_cli(["remap", "show", KEYCAP_TRUTH], enum=())
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])

    def test_apply_dry_run_matches_show(self):
        show = run_cli(["remap", "show", KEYCAP_TRUTH])
        dry = run_cli(["--dry-run", "remap", "apply", KEYCAP_TRUTH])
        self.assertEqual(dry[0], 0)
        self.assertEqual(dry[2], [])
        self.assertEqual(dry[1], show[1])

    def test_apply_runs_full_sequence_on_wired(self):
        transport = FakeCliTransport()
        code, text, calls = run_cli(
            ["remap", "apply", KEYCAP_TRUTH],
            enum=WIRED_ENUM + DONGLE_ENUM,
            transport=transport,
        )
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["mi_03_config"])
        self.assertEqual(len(transport.features), 13)
        remaps = remap.resolve_mapping(remap.load_mapping_file(KEYCAP_TRUTH))
        table = protocol.build_remap_table(remaps)
        self.assertEqual(b"".join(transport.features[2:11]), table)
        self.assertIn("Remap applied: 9 keys", text)
        self.assertTrue(transport.closed)

    def test_reset_sends_identity_table(self):
        transport = FakeCliTransport()
        code, text, calls = run_cli(
            ["remap", "reset"], enum=WIRED_ENUM, transport=transport
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            b"".join(transport.features[2:11]),
            bytes(574) + bytes([0xAA, 0x55]),
        )
        self.assertIn("Remap reset", text)

    def test_reset_dry_run_prints_identity_plan(self):
        code, text, calls = run_cli(["--dry-run", "remap", "reset"])
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertIn("slot: none (identity table, clears all remaps)", text)

    def test_apply_without_wired_endpoint_fails_cleanly(self):
        code, text, calls = run_cli(
            ["remap", "apply", KEYCAP_TRUTH], enum=DONGLE_ENUM
        )
        self.assertEqual(code, 1)
        self.assertEqual(calls, [])
        self.assertIn("requires the wired", text)

    def test_apply_refuses_shared_pid_wrong_revision(self):
        shared_pid = [
            rec(
                0x0C45,
                0x800A,
                0xFF13,
                0x0001,
                3,
                path="wrong_revision",
                release_number=0x0107,
            )
        ]
        code, text, calls = run_cli(
            ["remap", "apply", KEYCAP_TRUTH], enum=shared_pid
        )
        self.assertEqual(code, 1)
        self.assertEqual(calls, [])
        self.assertIn("refusing remap", text)
        self.assertIn("0x0107", text)

    def test_fn_position_in_file_exits_2_with_guard_message(self):
        path = write_mapping([{"position": "Fn", "send": "A"}])
        try:
            code, text, calls = run_cli(["remap", "show", path])
        finally:
            os.unlink(path)
        self.assertEqual(code, 2)
        self.assertEqual(calls, [])
        self.assertIn(FN_GUARD_MESSAGE, text)

    def test_bad_mapping_files_exit_2(self):
        bad_files = [
            write_mapping([{"position": "F1", "send": "hyperkey"}]),
            write_mapping([{"position": "NoSuchKey", "send": "A"}]),
            write_mapping({"not_mappings": []}),
        ]
        try:
            for path in bad_files:
                code, text, calls = run_cli(["remap", "show", path])
                self.assertEqual(code, 2, path)
                self.assertIn("error:", text)
                self.assertEqual(calls, [])
        finally:
            for path in bad_files:
                os.unlink(path)

    def test_missing_file_exits_2(self):
        code, text, calls = run_cli(["remap", "show", "no-such-file.json"])
        self.assertEqual(code, 2)
        self.assertIn("error:", text)


if __name__ == "__main__":
    unittest.main()
