"""Periodic end-to-end quality eval for the shipped board policy."""

import json
import pathlib
import subprocess
import time
import unittest

from services import hid
from services.hid import client as hid_client, custom_lighting, device, protocol, screen


ROOT = pathlib.Path(__file__).resolve().parents[3]
BOARD_MAP = ROOT / "services" / "hid" / "data" / "board_map.json"
HOST_CONFIG = ROOT / "services" / "hostlayer" / "layers.json"

EXPECTED_PHYSICAL_TOKENS = {
    "F1": "F13", "F3": "F14", "F5": "F15", "F6": "F18",
    "F7": "F19", "F8": "F20", "F9": "F21", "F10": "F22",
    "F11": "F23", "F12": "F24",
}


def usage_to_fkey(value: str) -> str:
    usage = int(value, 16)
    if not 0x68 <= usage <= 0x73:
        raise ValueError(f"not an F13-F24 usage: {value}")
    return f"F{usage - 0x68 + 13}"


def score_policy(board_map: dict, host_config: dict) -> tuple[int, list[str]]:
    mapped = {
        row["position"]: usage_to_fkey(row["send"])
        for row in board_map["mappings"]
        if row["position"].startswith("F")
    }
    checks = (
        (mapped == EXPECTED_PHYSICAL_TOKENS, "physical token map drifted"),
        ({"F16", "F17"}.isdisjoint(mapped.values()), "CIDOO tokens collided"),
        (set(host_config["bindings"]) == set(mapped.values()), "host tokens disagree"),
        (len(mapped) == len(set(mapped.values())) == 10, "tokens are not unique"),
        ("0C45:800A" in board_map["comment"], "exact hardware ID is undocumented"),
        ("F75 Max" in board_map["comment"], "exact model is undocumented"),
    )
    failures = [message for passed, message in checks if not passed]
    return len(checks) - len(failures), failures


class TestBoardPolicyEval(unittest.TestCase):
    def test_public_package_metadata_matches_shipped_capability(self):
        self.assertEqual(hid.__version__, "1.3.0")
        self.assertIn("remap", hid.__doc__)
        self.assertIn("screen", hid.__doc__)

    def test_shipped_policy_scores_six_of_six(self):
        board = json.loads(BOARD_MAP.read_text(encoding="utf-8"))
        host = json.loads(HOST_CONFIG.read_text(encoding="utf-8"))
        score, failures = score_policy(board, host)
        self.assertEqual(score, 6, failures)

    def test_eval_rejects_old_f16_f17_collision(self):
        board = json.loads(BOARD_MAP.read_text(encoding="utf-8"))
        host = json.loads(HOST_CONFIG.read_text(encoding="utf-8"))
        for row in board["mappings"]:
            if row["position"] == "F6":
                row["send"] = "0x6B"
            elif row["position"] == "F7":
                row["send"] = "0x6C"
        score, failures = score_policy(board, host)
        self.assertLess(score, 6)
        self.assertIn("CIDOO tokens collided", failures)

    def test_mutation_identity_rejects_related_shared_pid_board(self):
        record = {
            "vendor_id": 0x0C45,
            "product_id": 0x800A,
            "usage_page": 0xFF13,
            "usage": 1,
            "interface_number": 3,
            "path": "shared-pid-reference-board",
            "product_string": "AULA F108Pro",
            "release_number": 0x0108,
        }
        endpoint = device.discover([record]).wired_config
        self.assertIsNotNone(endpoint)
        self.assertFalse(endpoint.is_exact_wired_target)

    def test_named_profiles_move_safe_measurable_outcomes(self):
        focus = custom_lighting.compile_profile("Focus Core")
        aurora = custom_lighting.compile_profile("Aurora")
        self.assertEqual(focus.stock.mode, protocol.resolve_mode("Static"))
        self.assertEqual(focus.stock.brightness, 2)
        self.assertEqual(aurora.stock.mode, protocol.resolve_mode("Flowing"))
        self.assertEqual(len(focus.keys), 80)
        self.assertEqual(len({key.light_index for key in focus.keys}), 80)
        with self.assertRaisesRegex(protocol.ProtocolError, "capture_required"):
            custom_lighting.require_per_key_capture()

    def test_local_media_is_ignored_and_no_binary_is_tracked_or_staged(self):
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.splitlines()
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        prohibited = {
            ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff",
            ".webp", ".bin", ".f75lcd", ".exe", ".dll", ".sys", ".pyc",
        }
        for name in tracked + staged:
            normalized = name.replace("\\", "/")
            self.assertFalse(normalized.startswith(".local-assets/"), normalized)
            self.assertNotIn(pathlib.Path(normalized).suffix.lower(), prohibited, normalized)
        ignored = subprocess.run(
            ["git", "check-ignore", ".local-assets/lcd/focus-core.png"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(ignored.returncode, 0, ignored.stderr)

    def test_lcd_manifest_matches_ignored_local_sources_when_present(self):
        manifest = json.loads(
            (ROOT / "services" / "hid" / "data" / "lcd_assets.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["target"]["usb_id"], "0C45:800A")
        self.assertEqual(manifest["target"]["release"], "0x0108")
        self.assertEqual(manifest["restore"]["source_size"], [128, 128])
        self.assertEqual(manifest["restore"]["frame_count"], 251)
        self.assertEqual(manifest["restore"]["candidate_delay_byte"], 10)
        self.assertEqual(manifest["restore"]["prepared_page_count"], 2009)
        restore_locations = (
            ROOT / manifest["restore"]["local_directory"],
            ROOT / manifest["restore"]["fallback_directory"],
        )
        source = next((path for path in restore_locations if path.is_dir()), None)
        if source is not None:
            prepared = screen.prepare_frame_sequence(
                source,
                delay=manifest["restore"]["candidate_delay_byte"],
                fit="stretch",
            )
            screen.validate_restore_manifest(
                prepared,
                manifest,
                manifest["restore"]["candidate_delay_byte"],
            )
        for asset in manifest["assets"]:
            self.assertEqual(len(asset["source_sha256"]), 64)
            local = ROOT / ".local-assets" / "lcd" / asset["local_filename"]
            if local.exists():
                import hashlib

                self.assertEqual(
                    hashlib.sha256(local.read_bytes()).hexdigest(),
                    asset["source_sha256"],
                )

    def test_lcd_live_policy_pins_captured_ack_and_keeps_restore_timing_gated(self):
        manifest = screen.load_lcd_manifest()
        restore = manifest["restore"]
        root_readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertEqual(protocol.SCREEN_ACK_PREFIX, b"\x01\x5a\x02")
        self.assertEqual(len(protocol.SCREEN_ACK_PREFIX), 3)
        self.assertIn("The committed `01 5A 02` prefix is mandatory", root_readme)
        self.assertNotIn("the committed prefix is intentionally unset", root_readme)
        self.assertNotIn("delay_byte", restore)
        self.assertEqual(restore["candidate_delay_byte"], 10)
        self.assertIn("unverified", restore["timing_status"])
        self.assertIn("not proven", restore["note"])

    def test_screen_stale_drain_policy_is_positive_and_bounded(self):
        class EndlessStaleInput:
            def __init__(self):
                self.timeouts = []

            def read_input(self, timeout_ms):
                self.timeouts.append(timeout_ms)
                return b"old"

        screen_transport = EndlessStaleInput()
        uploader = hid_client.ScreenClient(object(), screen_transport)
        with self.assertRaisesRegex(
            hid_client.ScreenUploadError, "stale-input queue did not drain"
        ):
            uploader._drain_stale_screen_input()

        self.assertEqual(
            screen_transport.timeouts,
            [hid_client.ScreenClient.STALE_DRAIN_POLL_MS]
            * hid_client.ScreenClient.STALE_DRAIN_MAX_REPORTS,
        )
        self.assertTrue(all(timeout > 0 for timeout in screen_transport.timeouts))
        self.assertLessEqual(sum(screen_transport.timeouts), 32)

    def test_clock_policy_uses_data_echo_but_strict_control_acks(self):
        class ClockTransport(hid_client.Transport):
            def __init__(self, *, corrupt_clock=False, bad_control=None):
                self.last = b""
                self.sent = []
                self.corrupt_clock = corrupt_clock
                self.bad_control = bad_control

            def send_feature(self, payload):
                self.last = bytes(payload)
                self.sent.append(self.last)

            def get_feature(self, length=protocol.WIRED_REPORT_SIZE):
                if self.last[:2] == b"\x00\x01":
                    response = bytearray(self.last)
                    if self.corrupt_clock:
                        response[8] ^= 0x01
                    return bytes(response)
                status = 0 if self.last[:2] == self.bad_control else 1
                return self.last[:2] + bytes([0, status]) + bytes(length - 4)

        when = time.struct_time((2026, 8, 12, 22, 23, 0, 2, 224, -1))
        transport = ClockTransport()
        hid_client.Client(transport, sleep=lambda _: None).sync_clock_wired(when)
        self.assertEqual(transport.sent[-1], protocol.build_wired_apply())

        with self.assertRaises(hid_client.AckError):
            hid_client.Client(
                ClockTransport(corrupt_clock=True), sleep=lambda _: None
            ).sync_clock_wired(when)
        for prefix in (b"\x04\x18", b"\x04\x28", b"\x04\x02"):
            with self.subTest(strict_control=prefix.hex(" ")):
                with self.assertRaises(hid_client.AckError):
                    hid_client.Client(
                        ClockTransport(bad_control=prefix), sleep=lambda _: None
                    ).sync_clock_wired(when)

    def test_hidapi_write_count_policy_is_exact_for_every_report_kind(self):
        class ResultDevice:
            def __init__(self, result):
                self.result = result

            def send_feature_report(self, payload):
                return self.result

            def write(self, payload):
                return self.result

        cases = (
            (device.KIND_WIRED_CONFIG, "feature", 65, bytes(64)),
            (device.KIND_DONGLE_CONFIG, "output", 33, bytes(32)),
            (device.KIND_WIRED_SCREEN, "output", 4097, bytes(4096)),
        )
        for kind, operation, expected, payload in cases:
            with self.subTest(kind=kind):
                transport = object.__new__(hid_client.HidapiTransport)
                transport._kind = kind
                transport._device = ResultDevice(expected)
                if operation == "feature":
                    transport.send_feature(payload)
                else:
                    transport.write_output(payload)

                for invalid in (-1, 0, None, expected - 1, expected + 1, True):
                    transport._device = ResultDevice(invalid)
                    with self.assertRaises(protocol.ProtocolError):
                        if operation == "feature":
                            transport.send_feature(payload)
                        else:
                            transport.write_output(payload)

    def test_screen_begin_cleanup_policy_distinguishes_send_from_ack_failure(self):
        class BeginControl(hid_client.Transport):
            def __init__(self, failure):
                self.failure = failure
                self.sent = []
                self.last = b""

            def send_feature(self, payload):
                if self.failure == "send" and not self.sent:
                    raise OSError("begin send failed")
                self.last = bytes(payload)
                self.sent.append(self.last)

            def get_feature(self, length=protocol.WIRED_REPORT_SIZE):
                status = 0 if self.failure == "ack" and len(self.sent) == 1 else 1
                return self.last[:2] + bytes([0, status]) + bytes(length - 4)

        stream = screen.build_stream([bytes(screen.RGB565_FRAME_BYTES)], [5])[0]
        send_control = BeginControl("send")
        with self.assertRaises(hid_client.ScreenUploadError):
            hid_client.ScreenClient(
                send_control, object(), sleep=lambda _: None
            ).upload(stream)
        self.assertEqual(send_control.sent, [])

        ack_control = BeginControl("ack")
        with self.assertRaises(hid_client.ScreenUploadError):
            hid_client.ScreenClient(
                ack_control, object(), sleep=lambda _: None
            ).upload(stream)
        self.assertEqual(
            ack_control.sent,
            [protocol.build_wired_begin(), protocol.build_wired_apply()],
        )


if __name__ == "__main__":
    unittest.main()
