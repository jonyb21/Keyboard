"""Score the fail-closed F75-family compatibility recommendation policy."""

from __future__ import annotations

import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.device_audit.audit import recommend_compatibility  # noqa: E402


def main() -> int:
    fixture_path = Path(__file__).with_name("cases.json")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    cases = fixture["cases"]
    failures = []
    for case in cases:
        actual = recommend_compatibility(case["evidence"])
        expected_action = case["expected_action"]
        expected_safe = case["expected_safe_to_configure"]
        if (
            actual.get("action") != expected_action
            or actual.get("safe_to_configure") is not expected_safe
            or actual.get("safe_to_flash_firmware") is not False
        ):
            failures.append(
                {
                    "id": case["id"],
                    "expected": {
                        "action": expected_action,
                        "safe_to_configure": expected_safe,
                        "safe_to_flash_firmware": False,
                    },
                    "actual": {
                        "action": actual.get("action"),
                        "safe_to_configure": actual.get("safe_to_configure"),
                        "safe_to_flash_firmware": actual.get(
                            "safe_to_flash_firmware"
                        ),
                    },
                }
            )
    passed = len(cases) - len(failures)
    score = passed / len(cases) if cases else 0.0
    threshold = float(fixture["threshold"])
    result = {
        "suite": "device_audit_compatibility",
        "cases": len(cases),
        "passed": passed,
        "score": score,
        "threshold": threshold,
        "status": "PASS" if score >= threshold else "FAIL",
        "failures": failures,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if score >= threshold else 1


if __name__ == "__main__":
    raise SystemExit(main())
