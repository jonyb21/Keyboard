"""Gate tests for services.hid.cli against fake enumerations and transports.

The default transport factory (which would open hardware) is always replaced;
--dry-run paths never construct a transport at all.
"""

import io
import unittest

from services.hid import cli, protocol
from services.hid.client import Transport
from services.hid.protocol import LightingConfig
from services.hid.tests.test_device import DONGLE_ENUM, NOISE, WIRED_ENUM, rec


class FakeCliTransport(Transport):
    def __init__(self, input_reports=None):
        self.features = []
        self.writes = []
        self.input_reports = list(input_reports or [])
        self.closed = False

    def send_feature(self, payload):
        self.features.append(bytes(payload))

    def get_feature(self, length=protocol.WIRED_REPORT_SIZE):
        head = self.features[-1][:2] if self.features else b"\x00\x00"
        return head + b"\x00\x01" + bytes(length - 4)

    def write_output(self, payload):
        self.writes.append(bytes(payload))

    def read_input(self, timeout_ms):
        if self.input_reports:
            return self.input_reports.pop(0)
        return None

    def close(self):
        self.closed = True


def run_cli(argv, enum=(), transport=None):
    out = io.StringIO()
    factory_calls = []

    def factory(endpoint):
        factory_calls.append(endpoint.path)
        if transport is None:
            raise AssertionError("transport factory must not be called")
        return transport

    code = cli.main(
        argv,
        out=out,
        enumerator=lambda: list(enum),
        transport_factory=factory,
    )
    return code, out.getvalue(), factory_calls


class TestStatus(unittest.TestCase):
    def test_all_endpoints_listed(self):
        code, text, calls = run_cli(["status"], enum=WIRED_ENUM + DONGLE_ENUM)
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertIn("wired config (0xFF13 feature)", text)
        self.assertIn("mi_03_config", text)
        self.assertIn("mi_02_screen", text)
        self.assertIn("d_mi_03_raw", text)

    def test_nothing_found(self):
        code, text, _ = run_cli(["status"], enum=NOISE)
        self.assertEqual(code, 1)
        self.assertIn("no AULA F75 Max endpoints found", text)


class TestBatteryCommand(unittest.TestCase):
    def test_dry_run_prints_golden_request(self):
        code, text, calls = run_cli(["--dry-run", "battery"])
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertIn(protocol.build_battery_request().hex(" "), text)

    def test_battery_via_fake_transport(self):
        report = bytes([0x20, 0x01, 0x00, 87]) + bytes(28)
        transport = FakeCliTransport(input_reports=[report])
        code, text, calls = run_cli(
            ["battery"], enum=DONGLE_ENUM, transport=transport
        )
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["d_mi_03_raw"])
        self.assertIn("Battery: 87%", text)
        self.assertEqual(transport.writes, [protocol.build_battery_request()])
        self.assertTrue(transport.closed)

    def test_battery_without_dongle_fails_cleanly(self):
        code, text, calls = run_cli(["battery"], enum=WIRED_ENUM)
        self.assertEqual(code, 1)
        self.assertEqual(calls, [])
        self.assertIn("requires the 2.4G dongle", text)


class TestLightCommand(unittest.TestCase):
    def test_dry_run_prints_both_transports(self):
        code, text, calls = run_cli(
            [
                "--dry-run", "light",
                "--mode", "breath",
                "--brightness", "5",
                "--speed", "3",
                "--color", "FF0000",
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        config = LightingConfig(mode=7, red=255, brightness=5, speed=3)
        self.assertIn(protocol.build_wired_lighting_data(config).hex(" "), text)
        self.assertIn(protocol.build_dongle_lighting(config).hex(" "), text)
        self.assertIn(protocol.build_wired_begin().hex(" "), text)

    def test_wired_transport_runs_full_sequence(self):
        transport = FakeCliTransport()
        code, text, calls = run_cli(
            ["light", "--mode", "11", "--colorful"],
            enum=WIRED_ENUM + DONGLE_ENUM,
            transport=transport,
        )
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["mi_03_config"])  # auto prefers wired
        self.assertEqual(len(transport.features), 5)
        self.assertIn("Rolling (mode 11) via wired-config", text)
        self.assertTrue(transport.closed)

    def test_dongle_transport_single_packet(self):
        transport = FakeCliTransport()
        code, text, calls = run_cli(
            ["light", "--mode", "1", "--color", "00FF00", "--transport", "dongle"],
            enum=WIRED_ENUM + DONGLE_ENUM,
            transport=transport,
        )
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["d_mi_03_raw"])
        config = LightingConfig(mode=1, green=255)
        self.assertEqual(transport.writes, [protocol.build_dongle_lighting(config)])

    def test_missing_endpoint_fails_cleanly(self):
        code, text, calls = run_cli(
            ["light", "--mode", "1", "--transport", "wired"], enum=DONGLE_ENUM
        )
        self.assertEqual(code, 1)
        self.assertEqual(calls, [])
        self.assertIn("no wired config endpoint", text)

    def test_wired_lighting_refuses_shared_pid_wrong_model(self):
        shared_pid = [
            rec(
                0x0C45,
                0x800A,
                0xFF13,
                0x0001,
                3,
                path="f108_config",
                product_string="AULA F108Pro",
            )
        ]
        code, text, calls = run_cli(
            ["light", "--mode", "1", "--transport", "wired"],
            enum=shared_pid,
        )
        self.assertEqual(code, 1)
        self.assertEqual(calls, [])
        self.assertIn("refusing wired lighting", text)
        self.assertIn("AULA F108Pro", text)

    def test_bad_parameters_exit_2(self):
        for argv in (
            ["light", "--mode", "disco"],
            ["light", "--mode", "1", "--color", "XYZ"],
            ["light", "--mode", "1", "--brightness", "9"],
        ):
            code, text, calls = run_cli(["--dry-run"] + argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("error:", text)
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
