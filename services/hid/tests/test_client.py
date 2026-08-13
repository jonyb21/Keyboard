"""Gate tests for services.hid.client: pacing, sequencing, battery flow.

Everything runs against MockTransport and a fake clock. Real time never
elapses and no device is opened.
"""

import unittest
from unittest import mock

from services.hid import device, protocol, screen as screen_model
from services.hid.client import (
    AckError,
    Client,
    DeviceIdentityError,
    HidapiTransport,
    Pacer,
    ScreenClient,
    ScreenUploadError,
    Transport,
)
from services.hid.protocol import LightingConfig, ProtocolError


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


class ClockEchoTransport(MockTransport):
    """Models Jon's exact F75 Max clock-data feature-report echo."""

    def __init__(self, clock, *, clock_response=None, bad_control=None):
        super().__init__(clock)
        self.clock_response = clock_response
        self.bad_control = bad_control

    def get_feature(self, length=protocol.WIRED_REPORT_SIZE):
        if self._last_feature and self._last_feature[:2] == b"\x00\x01":
            self.ops.append(("get_feature", None, self.clock.now))
            if self.clock_response is not None:
                return self.clock_response(self._last_feature)
            return self._last_feature
        if self._last_feature and self._last_feature[:2] == self.bad_control:
            self.ops.append(("get_feature", None, self.clock.now))
            return self._last_feature[:2] + b"\x00\x00" + bytes(length - 4)
        return super().get_feature(length)


class BeginFailureTransport(MockTransport):
    """Injects a definite begin-send failure or an uncertain begin ACK."""

    def __init__(self, clock, *, fail_send=False, fail_ack=False):
        super().__init__(clock)
        self.fail_send = fail_send
        self.fail_ack = fail_ack

    def send_feature(self, payload):
        if self.fail_send and payload == protocol.build_wired_begin():
            self.ops.append(("feature_failed", bytes(payload), self.clock.now))
            raise OSError("simulated begin send failure")
        super().send_feature(payload)

    def get_feature(self, length=protocol.WIRED_REPORT_SIZE):
        if self.fail_ack and self._last_feature == protocol.build_wired_begin():
            self.ops.append(("get_feature", None, self.clock.now))
            return self._last_feature[:2] + b"\x00\x00" + bytes(length - 4)
        return super().get_feature(length)


def make_client(clock, transport, **kwargs):
    return Client(
        transport, monotonic=clock.monotonic, sleep=clock.sleep, **kwargs
    )


def valid_screen_stream(frame_count=1, delay=5):
    frame = bytes(screen_model.RGB565_FRAME_BYTES)
    return screen_model.build_stream(
        [frame] * frame_count, [delay] * frame_count
    )[0]


class TestHidapiIdentityBoundary(unittest.TestCase):
    @staticmethod
    def _endpoint(kind):
        if kind == device.KIND_DONGLE_CONFIG:
            return device.Endpoint(
                kind=kind,
                path="dongle-path",
                vendor_id=device.DONGLE_VID,
                product_id=device.DONGLE_PID,
                usage_page=device.DONGLE_CONFIG_USAGE_PAGE,
                usage=device.DONGLE_CONFIG_USAGE,
                interface_number=3,
                product_string="AULA F75Max Dongle",
                release_number=0,
            )
        return device.Endpoint(
            kind=kind,
            path="wired-path",
            vendor_id=device.WIRED_VID,
            product_id=device.WIRED_PID,
            usage_page=(
                device.WIRED_CONFIG_USAGE_PAGE
                if kind == device.KIND_WIRED_CONFIG
                else device.WIRED_SCREEN_USAGE_PAGE
            ),
            usage=0x61,
            interface_number=(
                device.WIRED_CONFIG_INTERFACE
                if kind == device.KIND_WIRED_CONFIG
                else device.WIRED_SCREEN_INTERFACE
            ),
            product_string=device.WIRED_PRODUCT_STRING,
            release_number=device.WIRED_RELEASE_NUMBER,
        )

    def _transport_with_result(self, kind, result):
        class FakeDevice:
            def __init__(self):
                self.reports = []

            def open_path(self, path):
                self.path = path

            def send_feature_report(self, payload):
                self.reports.append(bytes(payload))
                return result

            def write(self, payload):
                self.reports.append(bytes(payload))
                return result

            def close(self):
                pass

        fake_device = FakeDevice()
        fake_hid = mock.Mock()
        fake_hid.device.return_value = fake_device
        with mock.patch.dict("sys.modules", {"hid": fake_hid}):
            transport = HidapiTransport(self._endpoint(kind))
        return transport, fake_device

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

    def test_screen_write_prepends_report_id_for_4097_windows_bytes(self):
        endpoint = device.discover(
            [
                {
                    "vendor_id": 0x0C45,
                    "product_id": 0x800A,
                    "usage_page": 0xFF68,
                    "usage": 0x61,
                    "interface_number": 2,
                    "path": "screen-path",
                    "product_string": "AULA F75Max",
                    "release_number": 0x0108,
                }
            ]
        ).wired_screen

        class FakeDevice:
            def __init__(self):
                self.opened = None
                self.written = []

            def open_path(self, path):
                self.opened = path

            def write(self, payload):
                self.written.append(bytes(payload))
                return len(payload)

            def close(self):
                pass

        fake_device = FakeDevice()
        fake_hid = mock.Mock()
        fake_hid.device.return_value = fake_device
        with mock.patch.dict("sys.modules", {"hid": fake_hid}):
            transport = HidapiTransport(endpoint)
            page = bytes((index & 0xFF for index in range(4096)))
            transport.write_output(page)
            self.assertEqual(fake_device.opened, b"screen-path")
            self.assertEqual(fake_device.written, [b"\x00" + page])
            self.assertEqual(len(fake_device.written[0]), 4097)
            with self.assertRaisesRegex(ProtocolError, "4096"):
                transport.write_output(bytes(64))
            with self.assertRaisesRegex(ProtocolError, "MI_03"):
                transport.send_feature(bytes(64))

    def test_all_hidapi_writes_require_exact_report_byte_count(self):
        cases = (
            (device.KIND_WIRED_CONFIG, "feature", 65, bytes(64)),
            (device.KIND_DONGLE_CONFIG, "output", 33, bytes(32)),
            (device.KIND_WIRED_SCREEN, "output", 4097, bytes(4096)),
        )
        for kind, operation, expected, payload in cases:
            with self.subTest(kind=kind, result=expected):
                transport, fake_device = self._transport_with_result(kind, expected)
                if operation == "feature":
                    transport.send_feature(payload)
                else:
                    transport.write_output(payload)
                self.assertEqual(fake_device.reports, [b"\x00" + payload])

            for bad in (-1, 0, None, expected - 1, expected + 1, True, "sent"):
                with self.subTest(kind=kind, result=bad):
                    transport, fake_device = self._transport_with_result(kind, bad)
                    with self.assertRaisesRegex(
                        ProtocolError, f"expected exactly {expected}"
                    ):
                        if operation == "feature":
                            transport.send_feature(payload)
                        else:
                            transport.write_output(payload)
                    self.assertEqual(fake_device.reports, [b"\x00" + payload])

    def test_screen_read_requires_a_bounded_positive_hidapi_timeout(self):
        endpoint = device.discover(
            [
                {
                    "vendor_id": 0x0C45,
                    "product_id": 0x800A,
                    "usage_page": 0xFF68,
                    "usage": 0x61,
                    "interface_number": 2,
                    "path": "screen-path",
                    "product_string": "AULA F75Max",
                    "release_number": 0x0108,
                }
            ]
        ).wired_screen

        class FakeDevice:
            def __init__(self):
                self.reads = []

            def open_path(self, path):
                pass

            def read(self, max_length, timeout_ms=0):
                self.reads.append((max_length, timeout_ms))
                return []

            def close(self):
                pass

        fake_device = FakeDevice()
        fake_hid = mock.Mock()
        fake_hid.device.return_value = fake_device
        with mock.patch.dict("sys.modules", {"hid": fake_hid}):
            transport = HidapiTransport(endpoint)
            self.assertIsNone(transport.read_input(1))
            self.assertEqual(fake_device.reads, [(protocol.WIRED_REPORT_SIZE, 1)])
            with self.assertRaisesRegex(ProtocolError, "positive timeout"):
                transport.read_input(0)
            self.assertEqual(fake_device.reads, [(protocol.WIRED_REPORT_SIZE, 1)])


class ScreenTransport(Transport):
    def __init__(self, clock, page_responses=None, stale=None, fail_write_at=None):
        self.clock = clock
        self.page_responses = list(page_responses or [])
        self.stale = list(stale or [])
        self.fail_write_at = fail_write_at
        self.writes = []
        self.reads = []
        self.closed = False

    def send_feature(self, payload):
        raise AssertionError("LCD pages must never be feature reports")

    def get_feature(self, length=64):
        raise AssertionError("MI_02 has no feature-report role")

    def write_output(self, payload):
        if self.fail_write_at == len(self.writes):
            raise OSError("simulated page write failure")
        self.writes.append(bytes(payload))

    def read_input(self, timeout_ms):
        self.reads.append(timeout_ms)
        if timeout_ms <= 1:
            return self.stale.pop(0) if self.stale else None
        if self.page_responses:
            response = self.page_responses.pop(0)
            if response is None:
                self.clock.now += timeout_ms / 1000
                return None
            return response
        self.clock.now += timeout_ms / 1000
        return None

    def close(self):
        self.closed = True


class TestScreenUpload(unittest.TestCase):
    def test_definite_begin_send_failure_does_not_send_apply(self):
        clock = FakeClock()
        control = BeginFailureTransport(clock, fail_send=True)
        screen = ScreenTransport(clock)
        client = ScreenClient(
            control,
            screen,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

        with self.assertRaisesRegex(ScreenUploadError, "begin send failure"):
            client.upload(valid_screen_stream())

        attempted = [payload for _, payload, _ in control.ops if payload is not None]
        self.assertEqual(attempted, [protocol.build_wired_begin()])
        self.assertNotIn(protocol.build_wired_apply(), attempted)
        self.assertNotIn(0.100, clock.sleeps)

    def test_begin_ack_failure_attempts_conservative_apply_cleanup(self):
        clock = FakeClock()
        control = BeginFailureTransport(clock, fail_ack=True)
        screen = ScreenTransport(clock)
        client = ScreenClient(
            control,
            screen,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

        with self.assertRaisesRegex(ScreenUploadError, "no ACK for command 04 18"):
            client.upload(valid_screen_stream())

        sent = [payload for kind, payload, _ in control.ops if kind == "feature"]
        self.assertEqual(
            sent,
            [protocol.build_wired_begin(), protocol.build_wired_apply()],
        )
        self.assertEqual(clock.sleeps.count(0.100), 1)

    def test_normal_upload_requires_shipped_live_ack_prefix(self):
        clock = FakeClock()
        control = MockTransport(clock)
        screen = ScreenTransport(
            clock,
            page_responses=[b"\x01\x5a\x02"] * 9,
        )
        client = ScreenClient(
            control,
            screen,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

        result = client.upload(valid_screen_stream())

        self.assertEqual(protocol.SCREEN_ACK_PREFIX, b"\x01\x5a\x02")
        self.assertEqual(result.ack_prefix, protocol.SCREEN_ACK_PREFIX)
        self.assertEqual(
            result.page_ack_prefixes,
            (protocol.SCREEN_ACK_PREFIX,) * 9,
        )

    def test_stale_drain_is_positive_bounded_and_runs_final_apply(self):
        clock = FakeClock()
        control = MockTransport(clock)
        screen = ScreenTransport(clock, stale=[b"old"] * 32)
        client = ScreenClient(
            control,
            screen,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            allow_ack_learning=True,
        )

        with self.assertRaisesRegex(
            ScreenUploadError, "stale-input queue did not drain"
        ):
            client.upload(valid_screen_stream())

        self.assertEqual(
            screen.reads,
            [ScreenClient.STALE_DRAIN_POLL_MS]
            * ScreenClient.STALE_DRAIN_MAX_REPORTS,
        )
        self.assertTrue(all(timeout > 0 for timeout in screen.reads))
        self.assertEqual(screen.writes, [])
        features = [payload for kind, payload, _ in control.ops if kind == "feature"]
        self.assertEqual(features[-1], protocol.build_wired_apply())
        self.assertEqual(clock.sleeps.count(0.1), 1)

    def test_validated_mutable_input_is_frozen_before_transfer(self):
        clock = FakeClock()
        control = MockTransport(clock)
        screen = ScreenTransport(clock, page_responses=[b"\x01\x5a\x02"] * 9)
        candidate = bytearray(valid_screen_stream())
        expected = bytes(candidate)
        mutated = False

        def mutating_sleep(seconds):
            nonlocal mutated
            if not mutated:
                candidate[-1] = 1
                mutated = True
            clock.sleep(seconds)

        client = ScreenClient(
            control,
            screen,
            monotonic=clock.monotonic,
            sleep=mutating_sleep,
        )
        client.upload(candidate)
        self.assertTrue(mutated)
        self.assertEqual(b"".join(screen.writes), expected)

    def test_exact_sequence_fresh_ack_and_no_finalize(self):
        clock = FakeClock()
        control = MockTransport(clock)
        screen = ScreenTransport(
            clock,
            page_responses=[b"\x01\x5a\x02"] * 9,
            stale=[b"old"],
        )
        client = ScreenClient(
            control,
            screen,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        stream = valid_screen_stream()
        result = client.upload(stream)
        self.assertEqual(result.page_count, 9)
        self.assertEqual(result.ack_prefix, b"\x01\x5a\x02")
        self.assertEqual(
            result.page_ack_prefixes,
            (b"\x01\x5a\x02",) * 9,
        )

        features = [payload for kind, payload, _ in control.ops if kind == "feature"]
        self.assertEqual(
            features,
            [
                protocol.build_wired_begin(),
                protocol.build_wired_screen_init(9),
                protocol.build_wired_apply(),
            ],
        )
        self.assertNotIn(protocol.build_wired_finalize(), features)
        self.assertEqual(
            screen.writes,
            [
                stream[offset : offset + screen_model.LCD_PAGE_BYTES]
                for offset in range(0, len(stream), screen_model.LCD_PAGE_BYTES)
            ],
        )
        self.assertNotIn(0, screen.reads)
        self.assertEqual(
            screen.reads.count(ScreenClient.STALE_DRAIN_POLL_MS),
            10,  # stale + empty, then one empty drain for each remaining page
        )
        self.assertIn(0.2, clock.sleeps)
        self.assertIn(0.05, clock.sleeps)
        self.assertEqual(clock.sleeps.count(0.005), 9)
        self.assertEqual(clock.sleeps.count(0.1), 1)

    def test_short_input_is_not_an_ack_and_timeout_runs_cleanup(self):
        clock = FakeClock()
        control = MockTransport(clock)
        screen = ScreenTransport(clock, page_responses=[b"\x01\x02", None])
        client = ScreenClient(
            control,
            screen,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            allow_ack_learning=True,
        )
        with self.assertRaisesRegex(ScreenUploadError, "timed out") as raised:
            client.upload(valid_screen_stream())
        self.assertIsNone(raised.exception.cleanup_error)
        features = [payload for kind, payload, _ in control.ops if kind == "feature"]
        self.assertEqual(features[-1], protocol.build_wired_apply())
        self.assertNotIn(protocol.build_wired_finalize(), features)
        self.assertEqual(clock.sleeps.count(0.1), 1)

    def test_page_write_failure_runs_final_apply(self):
        clock = FakeClock()
        control = MockTransport(clock)
        screen = ScreenTransport(
            clock,
            page_responses=[b"\x01\x5a\x02"],
            fail_write_at=1,
        )
        client = ScreenClient(
            control,
            screen,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        with self.assertRaisesRegex(ScreenUploadError, "write failure") as raised:
            client.upload(valid_screen_stream())
        self.assertEqual(raised.exception.page_index, 1)
        features = [payload for kind, payload, _ in control.ops if kind == "feature"]
        self.assertEqual(features[-1], protocol.build_wired_apply())
        self.assertEqual(clock.sleeps.count(0.1), 1)

    def test_changed_ack_prefix_fails_closed_and_runs_apply(self):
        clock = FakeClock()
        control = MockTransport(clock)
        screen = ScreenTransport(
            clock,
            page_responses=[b"\x10\x20\x30", b"\x10\x20\x31"],
        )
        client = ScreenClient(
            control,
            screen,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            allow_ack_learning=True,
        )
        with mock.patch.object(protocol, "SCREEN_ACK_PREFIX", None):
            with self.assertRaisesRegex(ScreenUploadError, "differs from learned"):
                client.upload(valid_screen_stream())
        features = [payload for kind, payload, _ in control.ops if kind == "feature"]
        self.assertEqual(features[-1], protocol.build_wired_apply())
        self.assertEqual(clock.sleeps.count(0.1), 1)

    def test_normal_upload_rejects_first_ack_that_differs_from_shipped_prefix(self):
        clock = FakeClock()
        control = MockTransport(clock)
        screen = ScreenTransport(clock, page_responses=[b"\x01\x02\x03"])
        client = ScreenClient(
            control,
            screen,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        with self.assertRaisesRegex(ScreenUploadError, "pinned 01 5a 02"):
            client.upload(valid_screen_stream())
        features = [payload for kind, payload, _ in control.ops if kind == "feature"]
        self.assertEqual(features[-1], protocol.build_wired_apply())

    def test_unpinned_upload_refuses_before_any_transfer(self):
        clock = FakeClock()
        control = MockTransport(clock)
        screen = ScreenTransport(clock)
        client = ScreenClient(
            control,
            screen,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        with mock.patch.object(protocol, "SCREEN_ACK_PREFIX", None):
            with self.assertRaisesRegex(ScreenUploadError, "ACK prefix is not pinned"):
                client.upload(valid_screen_stream())
        self.assertEqual(control.ops, [])
        self.assertEqual(screen.writes, [])
        self.assertEqual(screen.reads, [])

    def test_malformed_streams_refuse_before_any_transfer(self):
        valid = bytearray(valid_screen_stream())
        malformed = []
        zero_count = bytearray(valid)
        zero_count[0] = 0
        malformed.append(zero_count)
        zero_delay = bytearray(valid)
        zero_delay[1] = 0
        malformed.append(zero_delay)
        bad_filler = bytearray(valid)
        bad_filler[2] = 1
        malformed.append(bad_filler)
        malformed.append(valid + bytes(screen_model.LCD_PAGE_BYTES))
        bad_tail = bytearray(valid)
        bad_tail[-1] = 1
        malformed.append(bad_tail)

        for candidate in malformed:
            with self.subTest(length=len(candidate)):
                clock = FakeClock()
                control = MockTransport(clock)
                screen = ScreenTransport(clock)
                client = ScreenClient(
                    control,
                    screen,
                    monotonic=clock.monotonic,
                    sleep=clock.sleep,
                    allow_ack_learning=True,
                )
                with self.assertRaises(ProtocolError):
                    client.upload(bytes(candidate))
                self.assertEqual(control.ops, [])
                self.assertEqual(screen.writes, [])
                self.assertEqual(screen.reads, [])

    def test_open_exact_rejects_ambiguity_before_open(self):
        discovery = device.discover(
            __import__(
                "services.hid.tests.test_device", fromlist=["WIRED_ENUM"]
            ).WIRED_ENUM
            + [
                {
                    **__import__(
                        "services.hid.tests.test_device", fromlist=["WIRED_ENUM"]
                    ).WIRED_ENUM[-2],
                    "path": "duplicate-screen",
                }
            ]
        )
        calls = []
        with self.assertRaises(device.DeviceSelectionError):
            ScreenClient.open_exact(discovery, lambda endpoint: calls.append(endpoint))
        self.assertEqual(calls, [])

    def test_close_closes_both_handles(self):
        clock = FakeClock()
        control = MockTransport(clock)
        screen = ScreenTransport(clock)
        client = ScreenClient(control, screen)
        client.close()
        self.assertTrue(control.closed)
        self.assertTrue(screen.closed)


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
    def test_sequence_accepts_exact_data_echo_and_converts_weekday(self):
        import time as _time

        clock = FakeClock()
        transport = ClockEchoTransport(clock)
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

    def test_non_echo_clock_data_is_rejected_before_apply(self):
        import time as _time

        clock = FakeClock()

        def corrupt_minute(command):
            response = bytearray(command)
            response[7] ^= 0x01
            return bytes(response)

        transport = ClockEchoTransport(clock, clock_response=corrupt_minute)
        when = _time.struct_time((2026, 8, 12, 22, 23, 0, 2, 224, -1))
        with self.assertRaisesRegex(AckError, "payload echo"):
            make_client(clock, transport).sync_clock_wired(when)

        features = [op[1] for op in transport.ops if op[0] == "feature"]
        self.assertEqual(features[-1][:2], b"\x00\x01")
        self.assertNotIn(protocol.build_wired_apply(), features)

    def test_begin_select_and_apply_keep_strict_status_ack(self):
        import time as _time

        when = _time.struct_time((2026, 8, 12, 22, 23, 0, 2, 224, -1))
        for prefix in (b"\x04\x18", b"\x04\x28", b"\x04\x02"):
            with self.subTest(prefix=prefix.hex(" ")):
                clock = FakeClock()
                transport = ClockEchoTransport(clock, bad_control=prefix)
                with self.assertRaisesRegex(AckError, prefix.hex(" ")):
                    make_client(clock, transport).sync_clock_wired(when)

                features = [op[1] for op in transport.ops if op[0] == "feature"]
                self.assertEqual(features[-1][:2], prefix)

    def test_close_closes_transport(self):
        clock = FakeClock()
        transport = MockTransport(clock)
        client = make_client(clock, transport)
        client.close()
        self.assertTrue(transport.closed)


if __name__ == "__main__":
    unittest.main()
