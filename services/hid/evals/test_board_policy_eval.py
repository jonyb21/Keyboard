"""Periodic end-to-end quality eval for the shipped board policy."""

import json
import pathlib
import unittest

from services import hid
from services.hid import device


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
        self.assertEqual(hid.__version__, "1.2.0")
        self.assertIn("remap", hid.__doc__)

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


if __name__ == "__main__":
    unittest.main()
