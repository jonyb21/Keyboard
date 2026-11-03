"""Gate tests for services.hid.protocol: golden payloads and parsers.

Golden bytes are hand-derived from the cited sources (see module docstrings
in protocol.py and contracts/hid_protocol.md). No I/O, no device, no network.
"""

import inspect
import unittest

from services.hid import protocol
from services.hid.protocol import LightingConfig, ProtocolError


def hexs(payload: bytes) -> str:
    return payload.hex()


class TestPurity(unittest.TestCase):
    def test_protocol_has_zero_io_imports(self):
        source = inspect.getsource(protocol)
        imports = [
            line.strip()
            for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        forbidden = ("hid", "os", "io", "socket", "subprocess", "ctypes", "time")
        for line in imports:
            for name in forbidden:
                self.assertNotIn(
                    f" {name}", f" {line} ".replace(",", " , "),
                    f"I/O-adjacent import in protocol.py: {line!r}",
                )
        self.assertEqual(
            imports,
            ["from __future__ import annotations", "from dataclasses import dataclass"],
        )


class TestChecksum(unittest.TestCase):
    def test_additive_checksum_matches_osx_applyRawChecksum(self):
        payload = bytearray(32)
        payload[0] = 0x20
        payload[1] = 0x01
        self.assertEqual(protocol.checksum8(bytes(payload), 31), 0x21)

    def test_checksum_ignores_existing_checksum_byte(self):
        payload = bytearray(32)
        payload[0] = 0x05
        payload[31] = 0xEE  # must not contribute
        self.assertEqual(protocol.checksum8(bytes(payload), 31), 0x05)

    def test_checksum_wraps_to_8_bits(self):
        payload = bytes([0xFF] * 31 + [0x00])
        self.assertEqual(protocol.checksum8(payload, 31), (0xFF * 31) & 0xFF)

    def test_checksum_index_out_of_range(self):
        with self.assertRaises(ProtocolError):
            protocol.checksum8(b"\x00" * 4, 4)


class TestScreenProtocol(unittest.TestCase):
    def test_screen_metadata_golden_and_little_endian_pages(self):
        packet = protocol.build_wired_screen_init(2009)
        self.assertEqual(packet[:3], b"\x04\x72\x01")
        self.assertEqual(packet[8:10], b"\xd9\x07")
        self.assertEqual(len(packet), protocol.WIRED_REPORT_SIZE)
        self.assertEqual(packet[3:8], bytes(5))
        self.assertEqual(packet[10:], bytes(54))

    def test_screen_metadata_page_bounds(self):
        for pages in (1, 0xFFFF):
            self.assertEqual(len(protocol.build_wired_screen_init(pages)), 64)
        for pages in (0, 0x10000, True):
            with self.assertRaises(ProtocolError):
                protocol.build_wired_screen_init(pages)


class TestBattery(unittest.TestCase):
    def test_battery_request_golden(self):
        # [OSX] AulaF75Bar/main.m: payload[0]=0x20, payload[1]=0x01,
        # payload[31]=0x21, all else zero, 32 bytes.
        golden = "2001" + "00" * 29 + "21"
        self.assertEqual(hexs(protocol.build_battery_request()), golden)

    def test_battery_request_checksum_selfconsistent(self):
        pkt = protocol.build_battery_request()
        self.assertEqual(pkt[31], protocol.checksum8(pkt, 31))

    def test_parse_valid_response(self):
        report = bytes([0x20, 0x01, 0x00, 87]) + bytes(28)
        self.assertEqual(protocol.parse_battery_response(report), 87)

    def test_parse_response_with_report_id_prefix(self):
        report = bytes([0x00, 0x20, 0x01, 0x00, 87]) + bytes(27)
        self.assertEqual(protocol.parse_battery_response(report), 87)

    def test_parse_rejects_zero_and_out_of_range(self):
        self.assertIsNone(
            protocol.parse_battery_response(bytes([0x20, 0x01, 0x00, 0]))
        )
        self.assertIsNone(
            protocol.parse_battery_response(bytes([0x20, 0x01, 0x00, 101]))
        )

    def test_parse_rejects_wrong_header_and_short(self):
        self.assertIsNone(
            protocol.parse_battery_response(bytes([0x21, 0x01, 0x00, 50]))
        )
        self.assertIsNone(protocol.parse_battery_response(bytes([0x20, 0x01])))

    def test_request_response_round_trip(self):
        request = protocol.build_battery_request()
        # A conforming firmware echoes bytes 0-1 and puts percent at byte 3.
        for percent in (1, 50, 100):
            response = bytes([request[0], request[1], 0x00, percent])
            self.assertEqual(protocol.parse_battery_response(response), percent)


class TestWiredCommands(unittest.TestCase):
    def test_begin_apply_finalize_init_golden(self):
        self.assertEqual(hexs(protocol.build_wired_begin()), "0418" + "00" * 62)
        self.assertEqual(hexs(protocol.build_wired_apply()), "0402" + "00" * 62)
        self.assertEqual(hexs(protocol.build_wired_finalize()), "04f0" + "00" * 62)
        init = protocol.build_wired_lighting_init()
        self.assertEqual(init[:2], bytes([0x04, 0x13]))
        self.assertEqual(init[8], 0x01)
        self.assertEqual(len(init), 64)
        self.assertEqual(init[2:8] + init[9:], bytes(6 + 55))

    def test_lighting_data_golden_breath_red(self):
        # mode=7 Breath, red, brightness 5, speed 3, direction 0, single color.
        payload = protocol.build_wired_lighting_data(
            LightingConfig(mode=7, red=255, green=0, blue=0, brightness=5, speed=3)
        )
        golden = bytearray(64)
        golden[0] = 7
        golden[1] = 0xFF
        golden[9] = 5
        golden[10] = 3
        golden[14] = 0xAA  # trailer wire order AA 55 (conflict C1)
        golden[15] = 0x55
        self.assertEqual(payload, bytes(golden))

    def test_lighting_data_mode_off_leaves_params_zero(self):
        # [F108] hid-protocol.md: when mode=0 the parameter bytes stay zero.
        payload = protocol.build_wired_lighting_data(
            LightingConfig(mode=0, red=9, green=9, blue=9, brightness=4, speed=4)
        )
        golden = bytearray(64)
        golden[14] = 0xAA
        golden[15] = 0x55
        self.assertEqual(payload, bytes(golden))

    def test_trailer_wire_order_is_aa_55(self):
        self.assertEqual(protocol.TRAILER, bytes([0xAA, 0x55]))

    def test_clock_data_golden(self):
        payload = protocol.build_wired_clock_data(2026, 8, 10, 18, 30, 15, 1)
        golden = bytearray(64)
        golden[1] = 0x01
        golden[2] = 0x5A
        golden[3] = 26
        golden[4] = 8
        golden[5] = 10
        golden[6] = 18
        golden[7] = 30
        golden[8] = 15
        golden[10] = 1
        golden[62] = 0xAA
        golden[63] = 0x55
        self.assertEqual(payload, bytes(golden))

    def test_clock_data_validation(self):
        with self.assertRaises(ProtocolError):
            protocol.build_wired_clock_data(1999, 1, 1, 0, 0, 0, 0)
        with self.assertRaises(ProtocolError):
            protocol.build_wired_clock_data(2026, 13, 1, 0, 0, 0, 0)
        with self.assertRaises(ProtocolError):
            protocol.build_wired_clock_data(2026, 1, 1, 0, 0, 0, 7)

    def test_clock_data_echo_parsing_is_exact(self):
        command = protocol.build_wired_clock_data(2026, 8, 12, 22, 23, 0, 3)
        self.assertTrue(protocol.parse_wired_payload_echo(command, command))

        changed = bytearray(command)
        changed[7] += 1
        self.assertFalse(protocol.parse_wired_payload_echo(bytes(changed), command))
        self.assertFalse(protocol.parse_wired_payload_echo(command[:-1], command))
        self.assertFalse(protocol.parse_wired_payload_echo(command + b"\x00", command))
        begin = protocol.build_wired_begin()
        self.assertFalse(protocol.parse_wired_payload_echo(begin, begin))
        self.assertFalse(protocol.parse_wired_payload_echo(b"\x00\x01", b"\x00\x01"))

    def test_ack_parsing(self):
        begin = protocol.build_wired_begin()
        self.assertTrue(
            protocol.parse_wired_ack(bytes([0x04, 0x18, 0x00, 0x01]), begin)
        )
        self.assertFalse(
            protocol.parse_wired_ack(bytes([0x04, 0x18, 0x00, 0x00]), begin)
        )
        # Echo mismatch.
        self.assertFalse(
            protocol.parse_wired_ack(bytes([0x04, 0x02, 0x00, 0x01]), begin)
        )
        # Without command the echo is not checked.
        self.assertTrue(protocol.parse_wired_ack(bytes([0x04, 0x02, 0x00, 0x01])))
        self.assertFalse(protocol.parse_wired_ack(b"\x04\x18"))


class TestDongleLighting(unittest.TestCase):
    def test_lighting_golden_breath_red(self):
        pkt = protocol.build_dongle_lighting(
            LightingConfig(mode=7, red=255, green=0, blue=0, brightness=5, speed=3)
        )
        golden = bytearray(32)
        golden[0] = 0x05
        golden[1] = 0x10
        golden[3] = 7
        golden[4] = 0xFF
        golden[12] = 5
        golden[13] = 3
        golden[17] = 0xAA
        golden[18] = 0x55
        golden[31] = 0x22  # 05+10+07+FF+05+03+AA+55 = 0x222 -> 0x22
        self.assertEqual(pkt, bytes(golden))

    def test_lighting_mode_off_golden(self):
        pkt = protocol.build_dongle_lighting(LightingConfig(mode=0, red=255))
        golden = bytearray(32)
        golden[0] = 0x05
        golden[1] = 0x10
        golden[17] = 0xAA
        golden[18] = 0x55
        golden[31] = 0x14  # 05+10+AA+55 = 0x114 -> 0x14
        self.assertEqual(pkt, bytes(golden))

    def test_all_dongle_builders_are_checksum_consistent(self):
        packets = [
            protocol.build_battery_request(),
            protocol.build_dongle_commit(),
            protocol.build_dongle_lighting(LightingConfig(mode=11, colorful=True)),
            protocol.build_dongle_function_settings(
                fn_switch=1, sleep_time=2, response_level=3, game_mode=1
            ),
        ]
        for pkt in packets:
            self.assertEqual(len(pkt), 32)
            self.assertEqual(pkt[31], protocol.checksum8(pkt, 31))

    def test_commit_golden(self):
        golden = "0f" + "00" * 30 + "0f"
        self.assertEqual(hexs(protocol.build_dongle_commit()), golden)

    def test_function_settings_golden_all_fields(self):
        pkt = protocol.build_dongle_function_settings(
            fn_switch=0,
            sleep_time=2,
            response_level=3,
            game_mode=1,
            disable_alt_tab=1,
            disable_alt_f4=1,
            disable_win=1,
        )
        golden = bytearray(32)
        golden[0] = 0x07
        golden[1] = 0x10
        golden[4] = 0x01
        golden[5] = 0x01
        golden[6] = 0x01
        golden[7] = 0x01
        golden[9] = 2
        golden[11] = 3
        golden[12] = 1
        golden[13] = 1
        golden[14] = 1
        golden[15] = 1
        golden[17] = 0xAA
        golden[18] = 0x55
        golden[31] = 0x23  # sum 0x123 -> 0x23
        self.assertEqual(pkt, bytes(golden))

    def test_function_settings_excluded_fields(self):
        pkt = protocol.build_dongle_function_settings(response_level=5)
        self.assertEqual(pkt[5], 0x00)  # fn flag off
        self.assertEqual(pkt[6], 0x00)  # sleep flag off
        self.assertEqual(pkt[7], 0x01)
        self.assertEqual(pkt[11], 5)

    def test_function_settings_validation(self):
        with self.assertRaises(ProtocolError):
            protocol.build_dongle_function_settings(fn_switch=2)
        with self.assertRaises(ProtocolError):
            protocol.build_dongle_function_settings(sleep_time=4)
        with self.assertRaises(ProtocolError):
            protocol.build_dongle_function_settings(response_level=0)
        with self.assertRaises(ProtocolError):
            protocol.build_dongle_function_settings(game_mode=2)


class TestLightingValidation(unittest.TestCase):
    def test_mode_table_complete(self):
        self.assertEqual(sorted(protocol.LIGHT_MODES), list(range(20)))
        self.assertEqual(protocol.LIGHT_MODES[11], "Rolling")

    def test_out_of_range_rejected(self):
        for bad in (
            LightingConfig(mode=20),
            LightingConfig(mode=-1),
            LightingConfig(mode=1, red=256),
            LightingConfig(mode=1, brightness=6),
            LightingConfig(mode=1, speed=6),
            LightingConfig(mode=1, direction=2),
        ):
            with self.assertRaises(ProtocolError):
                bad.validate()

    def test_resolve_mode(self):
        self.assertEqual(protocol.resolve_mode("11"), 11)
        self.assertEqual(protocol.resolve_mode("rolling"), 11)
        self.assertEqual(protocol.resolve_mode("Breath"), 7)
        with self.assertRaises(ProtocolError):
            protocol.resolve_mode("disco")
        with self.assertRaises(ProtocolError):
            protocol.resolve_mode("20")

    def test_parse_color(self):
        self.assertEqual(protocol.parse_color("FF8000"), (255, 128, 0))
        self.assertEqual(protocol.parse_color("#00ff00"), (0, 255, 0))
        self.assertEqual(protocol.parse_color("0x0000FF"), (0, 0, 255))
        with self.assertRaises(ProtocolError):
            protocol.parse_color("FFF")
        with self.assertRaises(ProtocolError):
            protocol.parse_color("GGGGGG")


if __name__ == "__main__":
    unittest.main()
