"""Gate tests for services.hid.client: pacing, sequencing, battery flow.

Everything runs against MockTransport and a fake clock. Real time never
elapses and no device is opened.
"""

import unittest

from services.hid import device, protocol
from services.hid.client import (
    AckError,
    Client,
    DeviceIdentityError,
    HidapiTransport,
    Pacer,
    Transport,
)
from services.hid.protocol import LightingConfig


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert seconds >= 0
        self.sleeps.append(seconds)
        self.now += seconds


class MockTransport(Transport):
    """Records every operation with its fake timestamp."""

    def __init__(self, clock, input_reports=None, ack=True):
        self.clock = clock
        self.ops = []  # (kind, payload_or_None, timestamp)
        self.input_reports = list(input_reports or [])
        self.ack = ack
        self._last_feature = None
        self.closed = False

    def send_feature(self, payload):
        self._last_feature = bytes(payload)
        self.ops.append(("feature", bytes(payload), self.clock.now))

    def get_feature(self, length=protocol.WIRED_REPORT_SIZE):
        self.ops.append(("get_feature", None, self.clock.now))
        head = self._last_feature[:2] if self._last_feature else b"\x00\x00"
        status = 0x01 if self.ack else 0x00
        return head + bytes([0x00, status]) + bytes(length - 4)

    def write_output(self, payload):
        self.ops.append(("write", bytes(payload), self.clock.now))

    def read_input(self, timeout_ms):
        self.ops.append(("read", None, self.clock.now))
        if self.input_reports:
            item = self.input_reports.pop(0)
            if item is None:
                self.clock.now += timeout_ms / 1000.0
                return None
            return item
        # Simulate a blocking read that times out.
        self.clock.now += timeout_ms / 1000.0
        return None

    def close(self):
        self.closed = True


def make_client(clock, transport, **kwargs):
    return Client(
        transport, monotonic=clock.monotonic, sleep=clock.sleep, **kwargs
    )


class TestHidapiIdentityBoundary(unittest.TestCase):
    def test_raw_path_is_rejected_before_hidapi_import(self):
        with self.assertRaisesRegex(DeviceIdentityError, "raw paths are rejected"):
            HidapiTransport("shared-pid-device-path")

    def test_related_shared_pid_endpoint_is_rejected_before_open(self):
        endpoint = device.discover(
            [
                {
                    "vendor_id": 0x0C45,
                    "product_id": 0x800A,
                    "usage_page": 0xFF13,
                    "usage": 1,
                    "interface_number": 3,
                    "path": "shared-pid-device-path",
                    "product_string": "AULA F108Pro",
                    "release_number": 0x0108,
                }
            ]
        ).wired_config
        with self.assertRaisesRegex(DeviceIdentityError, "refusing wired transport"):
            HidapiTransport(endpoint)


class TestPacer(unittest.TestCase):
    def test_first_command_not_delayed(self):
        clock = FakeClock()
        pacer = Pacer(monotonic=clock.monotonic, sleep=clock.sleep)
        pacer.pace()
        self.assertEqual(clock.sleeps, [])

    def test_back_to_back_commands_are_35ms_apart(self):
        clock = FakeClock()
        pacer = Pacer(monotonic=clock.monotonic, sleep=clock.sleep)
        stamps = []
        for _ in range(4):
            pacer.pace()
            stamps.append(clock.now)
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        for gap in gaps:
            self.assertAlmostEqual(gap, protocol.COMMAND_DELAY_S)

    def test_no_sleep_when_enough_time_elapsed(self):
        clock = FakeClock()
        pacer = Pacer(monotonic=clock.monotonic, sleep=clock.sleep)
        pacer.pace()
        clock.now += 0.100  # work took longer than the pacing interval
        pacer.pace()
        self.assertEqual(clock.sleeps, [])

    def test_partial_gap_topped_up(self):
        clock = FakeClock()
        pacer = Pacer(monotonic=clock.monotonic, sleep=clock.sleep)
        pacer.pace()
        clock.now += 0.020
        pacer.pace()
        self.assertEqual(len(clock.sleeps), 1)
        self.assertAlmostEqual(clock.sleeps[0], 0.015)


class TestWiredLighting(unittest.TestCase):
    CONFIG = LightingConfig(mode=7, red=255, brightness=5, speed=3)

    def test_sequence_order_and_payloads(self):
        clock = FakeClock()
        transport = MockTransport(clock)
        make_client(clock, transport).set_lighting_wired(self.CONFIG)

        kinds = [op[0] for op in transport.ops]
        self.assertEqual(
            kinds,
            [
                "feature", "get_feature",  # 04 18 begin + readback
                "feature", "get_feature",  # 04 13 init + readback
                "feature",                  # data (no readback)
                "feature", "get_feature",  # 04 02 apply + readback
                "feature",                  # 04 F0 finalize (no readback)
            ],
        )
        features = [op[1] for op in transport.ops if op[0] == "feature"]
        self.assertEqual(features[0], protocol.build_wired_begin())
        self.assertEqual(features[1], protocol.build_wired_lighting_init())
        self.assertEqual(
            features[2], protocol.build_wired_lighting_data(self.CONFIG)
        )
        self.assertEqual(features[3], protocol.build_wired_apply())
        self.assertEqual(features[4], protocol.build_wired_finalize())

    def test_every_transfer_paced_35ms(self):
        clock = FakeClock()
        transport = MockTransport(clock)
        make_client(clock, transport).set_lighting_wired(self.CONFIG)
        stamps = [op[2] for op in transport.ops]
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        self.assertEqual(len(gaps), 7)
        for gap in gaps:
            self.assertGreaterEqual(gap, protocol.COMMAND_DELAY_S - 1e-9)

    def test_missing_ack_raises(self):
        clock = FakeClock()
        transport = MockTransport(clock, ack=False)
        client = make_client(clock, transport)
        with self.assertRaises(AckError):
            client.set_lighting_wired(self.CONFIG)
        # Failed on the first readback: nothing after begin+readback.
        self.assertEqual(len(transport.ops), 2)

    def test_non_strict_ack_continues(self):
        clock = FakeClock()
        transport = MockTransport(clock, ack=False)
        make_client(clock, transport, strict_ack=False).set_lighting_wired(
            self.CONFIG
        )
        self.assertEqual(len(transport.ops), 8)


class TestDongleLighting(unittest.TestCase):
    def test_single_packet(self):
        clock = FakeClock()
        transport = MockTransport(clock)
        config = LightingConfig(mode=11, colorful=True)
        make_client(clock, transport).set_lighting_dongle(config)
        self.assertEqual(
            transport.ops,
            [("write", protocol.build_dongle_lighting(config), 0.0)],
        )

    def test_optional_commit_precedes_and_is_paced(self):
        clock = FakeClock()
        transport = MockTransport(clock)
        config = LightingConfig(mode=1, red=16)
        make_client(clock, transport).set_lighting_dongle(
            config, send_commit=True
        )
        self.assertEqual(transport.ops[0][1], protocol.build_dongle_commit())
        self.assertEqual(transport.ops[1][1], protocol.build_dongle_lighting(config))
        self.assertGreaterEqual(
            transport.ops[1][2] - transport.ops[0][2],
            protocol.COMMAND_DELAY_S - 1e-9,
        )


class TestBattery(unittest.TestCase):
    def test_reads_percent_after_noise(self):
        clock = FakeClock()
        good = bytes([0x20, 0x01, 0x00, 87]) + bytes(28)
        noise = bytes([0x99, 0x01, 0x00, 55]) + bytes(28)
        transport = MockTransport(clock, input_reports=[None, noise, good])
        percent = make_client(clock, transport).read_battery()
        self.assertEqual(percent, 87)
        self.assertEqual(transport.ops[0][0], "write")
        self.assertEqual(transport.ops[0][1], protocol.build_battery_request())

    def test_timeout_raises(self):
        clock = FakeClock()
        transport = MockTransport(clock, input_reports=[])
        client = make_client(clock, transport)
        with self.assertRaises(TimeoutError):
            client.read_battery(timeout_s=0.5)
        # Fake time advanced past the deadline, not real time.
        self.assertGreaterEqual(clock.now, 0.5)


class TestClockSync(unittest.TestCase):
    def test_sequence_and_weekday_conversion(self):
        import time as _time

        clock = FakeClock()
        transport = MockTransport(clock)
        # 2026-08-10 is a Monday: tm_wday=0 -> wire weekday 1.
        when = _time.struct_time((2026, 8, 10, 18, 30, 15, 0, 222, -1))
        make_client(clock, transport).sync_clock_wired(when)
        features = [op[1] for op in transport.ops if op[0] == "feature"]
        self.assertEqual(features[0], protocol.build_wired_begin())
        self.assertEqual(features[1], protocol.build_wired_clock_init())
        self.assertEqual(
            features[2],
            protocol.build_wired_clock_data(2026, 8, 10, 18, 30, 15, 1),
        )
        self.assertEqual(features[3], protocol.build_wired_apply())
        kinds = [op[0] for op in transport.ops]
        self.assertEqual(kinds.count("get_feature"), 4)  # all steps readback

    def test_close_closes_transport(self):
        clock = FakeClock()
        transport = MockTransport(clock)
        client = make_client(clock, transport)
        client.close()
        self.assertTrue(transport.closed)


if __name__ == "__main__":
    unittest.main()
