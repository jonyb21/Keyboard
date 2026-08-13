"""Gate tests for services.hid.cli against fake enumerations and transports.

The default transport factory (which would open hardware) is always replaced.
When a test omits enumeration records, its injected enumerator raises on any
call, so every device-free path proves that it does not even discover hardware.
"""

import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from services.hid import cli, protocol, screen as screen_model
from services.hid.client import Transport
from services.hid.protocol import LightingConfig
from services.hid.tests.test_device import DONGLE_ENUM, NOISE, WIRED_ENUM, rec


class FastClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeCliTransport(Transport):
    def __init__(self, input_reports=None):
        self.features = []
        self.writes = []
        self.input_reports = list(input_reports or [])
        self.closed = False

    def send_feature(self, payload):
        self.features.append(bytes(payload))

    def get_feature(self, length=protocol.WIRED_REPORT_SIZE):
        if self.features and self.features[-1][:2] == b"\x00\x01":
            return self.features[-1]
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


def run_cli(argv, enum=None, transport=None):
    out = io.StringIO()
    factory_calls = []

    def enumerate_records():
        if enum is None:
            raise AssertionError("device-free CLI path must not enumerate")
        return list(enum)

    def factory(endpoint):
        factory_calls.append(endpoint.path)
        if transport is None:
            raise AssertionError("transport factory must not be called")
        if isinstance(transport, dict):
            return transport[endpoint.path]
        return transport

    clock = FastClock()
    real_client = cli.Client
    real_screen_open = cli.ScreenClient.open_exact
    with mock.patch.object(
        cli,
        "Client",
        side_effect=lambda item: real_client(
            item, monotonic=clock.monotonic, sleep=clock.sleep
        ),
    ), mock.patch.object(
        cli.ScreenClient,
        "open_exact",
        side_effect=lambda discovery, item_factory, **kwargs: real_screen_open(
            discovery,
            item_factory,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            **kwargs,
        ),
    ):
        code = cli.main(
            argv,
            out=out,
            enumerator=enumerate_records,
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

    def test_dry_run_status_is_rejected_without_enumerating(self):
        with mock.patch.object(
            cli, "_discover", side_effect=AssertionError("must not enumerate")
        ):
            code, text, calls = run_cli(["--dry-run", "status"])
        self.assertEqual(code, 2)
        self.assertEqual(calls, [])
        self.assertIn("status is already read-only", text)


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

    def test_duplicate_dongle_candidates_fail_before_open(self):
        duplicate = DONGLE_ENUM + [{**DONGLE_ENUM[-1], "path": "d_mi_03_raw_2"}]
        code, text, calls = run_cli(["battery"], enum=duplicate)
        self.assertEqual(code, 2)
        self.assertEqual(calls, [])
        self.assertIn("exactly one exact dongle", text)


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

    def test_named_focus_core_preset_uses_verified_stock_flow(self):
        transport = FakeCliTransport()
        code, text, calls = run_cli(
            ["light", "preset", "Focus Core", "--transport", "wired"],
            enum=WIRED_ENUM,
            transport=transport,
        )
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["mi_03_config"])
        self.assertIn("Focus Core -> Static", text)
        self.assertEqual(len(transport.features), 5)

    def test_aurora_preset_dry_run_has_no_hardware(self):
        code, text, calls = run_cli(
            ["--dry-run", "light", "preset", "Aurora", "--transport", "wired"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertIn(protocol.build_wired_lighting_init().hex(" "), text)

    def test_custom_profile_show_and_dry_run_are_data_only(self):
        for argv in (
            ["light", "custom", "Focus Core", "--show"],
            ["--dry-run", "light", "custom", "Aurora"],
        ):
            code, text, calls = run_cli(argv)
            self.assertEqual(code, 0)
            self.assertEqual(calls, [])
            self.assertIn("table_sha256=", text)
            self.assertIn("capture_required", text)
            self.assertNotIn("wired <-", text)

    def test_custom_profile_live_refuses_before_discovery(self):
        code, text, calls = run_cli(
            ["light", "custom", "Focus Core"], enum=WIRED_ENUM
        )
        self.assertEqual(code, 2)
        self.assertEqual(calls, [])
        self.assertIn("capture_required", text)

    def test_show_is_rejected_outside_custom_before_open(self):
        for argv in (
            ["light", "--mode", "static", "--show"],
            ["light", "preset", "Focus Core", "--show"],
        ):
            with self.subTest(argv=argv):
                code, text, calls = run_cli(argv, enum=WIRED_ENUM)
                self.assertEqual(code, 2)
                self.assertEqual(calls, [])
                self.assertIn("--show is valid only with light custom", text)

    def test_duplicate_config_candidates_fail_before_light_open(self):
        cases = (
            (
                ["light", "--mode", "static", "--transport", "wired"],
                WIRED_ENUM + [{**WIRED_ENUM[-1], "path": "wired-config-2"}],
            ),
            (
                ["light", "--mode", "static", "--transport", "dongle"],
                DONGLE_ENUM + [{**DONGLE_ENUM[-1], "path": "dongle-config-2"}],
            ),
        )
        for argv, records in cases:
            with self.subTest(transport=argv[-1]):
                code, text, calls = run_cli(argv, enum=records)
                self.assertEqual(code, 2)
                self.assertEqual(calls, [])
                self.assertIn("exactly one exact", text)


class TestClockCommand(unittest.TestCase):
    WHEN = time.struct_time((2026, 8, 12, 21, 30, 15, 2, 224, -1))

    def test_clock_sync_dry_run_never_opens_hardware(self):
        with mock.patch.object(cli.time, "localtime", return_value=self.WHEN):
            code, text, calls = run_cli(["--dry-run", "clock", "sync"])
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertIn(protocol.build_wired_clock_init().hex(" "), text)
        self.assertNotIn(protocol.build_wired_finalize().hex(" "), text)

    def test_clock_sync_uses_existing_exact_wired_client(self):
        transport = FakeCliTransport()
        with mock.patch.object(cli.time, "localtime", return_value=self.WHEN):
            code, text, calls = run_cli(
                ["clock", "sync"], enum=WIRED_ENUM, transport=transport
            )
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["mi_03_config"])
        self.assertEqual(transport.features[0], protocol.build_wired_begin())
        self.assertEqual(transport.features[1], protocol.build_wired_clock_init())
        self.assertEqual(transport.features[-1], protocol.build_wired_apply())
        self.assertIn("Clock synchronized", text)

    def test_duplicate_wired_candidates_fail_before_clock_open(self):
        duplicate = WIRED_ENUM + [{**WIRED_ENUM[-1], "path": "wired-config-2"}]
        code, text, calls = run_cli(["clock", "sync"], enum=duplicate)
        self.assertEqual(code, 2)
        self.assertEqual(calls, [])
        self.assertIn("exactly one exact wired", text)


class ScreenCliTransport(FakeCliTransport):
    def read_input(self, timeout_ms):
        if timeout_ms == cli.ScreenClient.STALE_DRAIN_POLL_MS:
            return None
        return super().read_input(timeout_ms)


class TestScreenCommand(unittest.TestCase):
    def make_png(self, directory: str) -> Path:
        from PIL import Image

        path = Path(directory, "source.png")
        Image.new("RGB", (32, 16), (255, 0, 0)).save(path)
        return path

    def test_inspect_and_dry_run_upload_never_open_hardware(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_png(directory)
            for argv in (
                ["screen", "inspect", str(source), "--fit", "contain"],
                ["--dry-run", "screen", "upload", str(source), "--fit", "cover"],
            ):
                code, text, calls = run_cli(argv)
                self.assertEqual(code, 0)
                self.assertEqual(calls, [])
                self.assertIn('"page_count": 9', text)
                self.assertIn('"stream_sha256":', text)

    def test_prepare_writes_only_explicit_local_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_png(directory)
            output = Path(directory, "prepared.f75lcd")
            code, text, calls = run_cli(
                ["screen", "prepare", str(source), "--output", str(output)]
            )
            self.assertEqual(code, 0)
            self.assertEqual(calls, [])
            self.assertEqual(output.stat().st_size, 9 * 4096)
            self.assertIn("Prepared stream written", text)

    def test_upload_opens_exact_control_then_screen_and_sends_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_png(directory)
            control = FakeCliTransport()
            screen_transport = ScreenCliTransport(input_reports=[b"ack"] * 9)
            with mock.patch.object(protocol, "SCREEN_ACK_PREFIX", b"ack"):
                code, text, calls = run_cli(
                    ["screen", "upload", str(source), "--fit", "stretch"],
                    enum=WIRED_ENUM,
                    transport={
                        "mi_03_config": control,
                        "mi_02_screen": screen_transport,
                    },
                )
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["mi_03_config", "mi_02_screen"])
        self.assertEqual(len(screen_transport.writes), 9)
        self.assertTrue(all(len(page) == 4096 for page in screen_transport.writes))
        self.assertEqual(control.features[-1], protocol.build_wired_apply())
        self.assertNotIn(protocol.build_wired_finalize(), control.features)
        self.assertIn("Screen uploaded: 9 pages", text)
        self.assertIn("ack_prefix=61 63 6b", text)
        self.assertIn("ack_count=9", text)
        self.assertTrue(control.closed)
        self.assertTrue(screen_transport.closed)

    def test_unpinned_normal_upload_blocks_before_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_png(directory)
            with mock.patch.object(protocol, "SCREEN_ACK_PREFIX", None), mock.patch.object(
                cli, "_discover", side_effect=AssertionError("must not enumerate")
            ):
                code, text, calls = run_cli(["screen", "upload", str(source)])
        self.assertEqual(code, 2)
        self.assertEqual(calls, [])
        self.assertIn("ACK prefix is not pinned", text)

    def test_unpinned_restore_blocks_before_discovery(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            sequence = Path(directory, "frames")
            sequence.mkdir()
            Image.new("RGB", (8, 8)).save(sequence / "0.png")
            with mock.patch.object(protocol, "SCREEN_ACK_PREFIX", None), mock.patch.object(
                cli, "_discover", side_effect=AssertionError("must not enumerate")
            ):
                code, text, calls = run_cli(
                    [
                        "screen",
                        "restore",
                        "--source",
                        str(sequence),
                        "--delay",
                        "10",
                        "--allow-unverified-timing",
                    ]
                )
        self.assertEqual(code, 2)
        self.assertEqual(calls, [])
        self.assertIn("ACK prefix is not pinned", text)

    def test_test_pattern_is_the_only_live_ack_learning_path(self):
        control = FakeCliTransport()
        screen_transport = ScreenCliTransport(input_reports=[b"ack"] * 9)
        with mock.patch.object(protocol, "SCREEN_ACK_PREFIX", None):
            code, text, calls = run_cli(
                ["screen", "test-pattern"],
                enum=WIRED_ENUM,
                transport={
                    "mi_03_config": control,
                    "mi_02_screen": screen_transport,
                },
            )
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["mi_03_config", "mi_02_screen"])
        self.assertIn("ack_prefix=61 63 6b", text)

    def test_duplicate_or_wrong_endpoints_rejected_before_open(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_png(directory)
            duplicate = WIRED_ENUM + [{**WIRED_ENUM[-2], "path": "screen-2"}]
            wrong = [
                WIRED_ENUM[-1],
                {**WIRED_ENUM[-2], "product_string": "AULA F108Pro"},
            ]
            for enum in (duplicate, wrong):
                with mock.patch.object(protocol, "SCREEN_ACK_PREFIX", b"ack"):
                    code, text, calls = run_cli(
                        ["screen", "upload", str(source)], enum=enum
                    )
                self.assertEqual(code, 2)
                self.assertEqual(calls, [])
                self.assertIn("error:", text)

    def test_explicit_restore_sequence_and_test_pattern_dry_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            from PIL import Image

            sequence = Path(directory, "frames")
            sequence.mkdir()
            Image.new("RGB", (8, 8), (255, 0, 0)).save(sequence / "0.png")
            Image.new("RGB", (8, 8), (0, 0, 255)).save(sequence / "1.png")
            for argv in (
                [
                    "--dry-run",
                    "screen",
                    "restore",
                    "--source",
                    str(sequence),
                    "--delay",
                    "10",
                ],
                ["--dry-run", "screen", "test-pattern"],
            ):
                code, text, calls = run_cli(argv)
                self.assertEqual(code, 0)
                self.assertEqual(calls, [])
                self.assertIn("would upload", text)

    def test_default_restore_requires_explicit_candidate_delay(self):
        with mock.patch.object(
            cli, "_discover", side_effect=AssertionError("must not enumerate")
        ):
            code, text, calls = run_cli(["--dry-run", "screen", "restore"])
        self.assertEqual(code, 2)
        self.assertEqual(calls, [])
        self.assertIn("explicit --delay", text)

    def test_default_restore_enforces_committed_manifest_before_discovery(self):
        prepared = screen_model.prepare_test_pattern()
        manifest = {
            "schema_version": 1,
            "restore": {
                "local_directory": ".local-assets/lcd/stock/frames",
                "fallback_directory": ".rollback/stock/frames",
            },
        }
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            screen_model, "load_lcd_manifest", return_value=manifest
        ), mock.patch.object(
            cli, "_default_restore_sequence", return_value=Path(directory)
        ), mock.patch.object(
            screen_model, "prepare_frame_sequence", return_value=prepared
        ), mock.patch.object(
            screen_model,
            "validate_restore_manifest",
            side_effect=screen_model.ScreenError("stream hash mismatch"),
        ) as validate, mock.patch.object(
            cli, "_discover", side_effect=AssertionError("must not enumerate")
        ):
            code, text, calls = run_cli(
                ["--dry-run", "screen", "restore", "--delay", "10"]
            )
        self.assertEqual(code, 2)
        self.assertEqual(calls, [])
        self.assertIn("stream hash mismatch", text)
        validate.assert_called_once_with(prepared, manifest, 10)

    def test_explicit_restore_source_bypasses_committed_manifest(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            sequence = Path(directory, "frames")
            sequence.mkdir()
            Image.new("RGB", (8, 8)).save(sequence / "0.png")
            with mock.patch.object(
                screen_model,
                "load_lcd_manifest",
                side_effect=AssertionError("explicit source must bypass manifest"),
            ):
                code, text, calls = run_cli(
                    [
                        "--dry-run",
                        "screen",
                        "restore",
                        "--source",
                        str(sequence),
                        "--delay",
                        "10",
                    ]
                )
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertIn("would upload", text)

    def test_live_restore_requires_unverified_timing_override_before_discovery(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            sequence = Path(directory, "frames")
            sequence.mkdir()
            Image.new("RGB", (8, 8)).save(sequence / "0.png")
            with mock.patch.object(protocol, "SCREEN_ACK_PREFIX", b"ack"), mock.patch.object(
                cli, "_discover", side_effect=AssertionError("must not enumerate")
            ):
                code, text, calls = run_cli(
                    [
                        "screen",
                        "restore",
                        "--source",
                        str(sequence),
                        "--delay",
                        "10",
                    ]
                )
        self.assertEqual(code, 2)
        self.assertEqual(calls, [])
        self.assertIn("--allow-unverified-timing", text)

    def test_restore_gif_requires_explicit_override_and_warns(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "stock.gif")
            Image.new("RGB", (8, 8), (255, 0, 0)).save(source, format="GIF")
            code, text, calls = run_cli(
                ["--dry-run", "screen", "restore", "--source", str(source)]
            )
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertIn("warning: explicit GIF restore", text)
        self.assertIn('"delays": [', text)


class TestRemapUniqueEndpoint(unittest.TestCase):
    def test_duplicate_wired_candidates_fail_before_remap_open(self):
        duplicate = WIRED_ENUM + [{**WIRED_ENUM[-1], "path": "wired-config-2"}]
        code, text, calls = run_cli(["remap", "reset"], enum=duplicate)
        self.assertEqual(code, 2)
        self.assertEqual(calls, [])
        self.assertIn("exactly one exact wired", text)


if __name__ == "__main__":
    unittest.main()
