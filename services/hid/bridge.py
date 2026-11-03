"""JSON bridge between the desktop app and the HID CLI.

Reads one JSON request on stdin, runs the matching `cli.main` command
in-process, and prints one JSON reply: {"ok", "code", "dry_run", "argv",
"output"}. Only the ops below are reachable; everything else is refused.

Writes are dry-run unless BOTH the environment allows live writes
(AULA_LIVE_WRITES=1, set by the app's main process) and the request asks
for `"live": true`. `status` is read-only and always enumerates.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from pathlib import Path

from . import cli

WRITE_OPS = {"light", "remap_apply", "clock_sync", "screen_upload", "settings_apply", "macro_apply"}


def _light_argv(req: dict) -> list[str]:
    argv = [
        "light",
        "--mode", str(req["mode"]),
        "--brightness", str(int(req.get("brightness", 5))),
        "--speed", str(int(req.get("speed", 3))),
        "--color", str(req.get("color", "FFFFFF")).lstrip("#"),
        "--transport", "wired",
    ]
    if req.get("colorful"):
        argv.append("--colorful")
    return argv


def _mapping_file(req: dict, tmpdir: str) -> str:
    mappings = req.get("mappings")
    if not isinstance(mappings, list):
        raise ValueError("remap needs a 'mappings' list")
    path = Path(tmpdir) / "mappings.json"
    path.write_text(json.dumps({"mappings": mappings}), encoding="utf-8")
    return str(path)


def _json_file(req: dict, tmpdir: str, key: str, wrap: str, kind) -> str:
    value = req.get(key)
    if not isinstance(value, kind):
        raise ValueError(f"{key} op needs a {wrap!r} {kind.__name__}")
    path = Path(tmpdir) / f"{wrap}.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return str(path)


def build_argv(req: dict, tmpdir: str) -> list[str]:
    op = req.get("op")
    if op == "status":
        return ["status"]
    if op == "light":
        return _light_argv(req)
    if op in ("remap_show", "remap_apply"):
        verb = "show" if op == "remap_show" else "apply"
        argv = ["remap", verb, _mapping_file(req, tmpdir)]
        layer = req.get("layer")
        if layer is not None:
            if layer not in ("normal", "fn"):
                raise ValueError("layer must be 'normal' or 'fn'")
            argv += ["--layer", layer]
        return argv
    if op in ("settings_show", "settings_apply"):
        verb = "show" if op == "settings_show" else "apply"
        return ["settings", verb, "--file", _json_file(req, tmpdir, "settings", "settings", dict)]
    if op in ("macro_show", "macro_apply"):
        verb = "show" if op == "macro_show" else "apply"
        return ["macro", verb, _json_file(req, tmpdir, "macros", "macros", list)]
    if op == "clock_sync":
        return ["clock", "sync"]
    if op in ("screen_inspect", "screen_upload"):
        verb = "inspect" if op == "screen_inspect" else "upload"
        argv = ["screen", verb, str(req["path"]), "--fit", str(req.get("fit", "contain"))]
        if req.get("delay_ms") is not None:
            argv += ["--delay-ms", str(req["delay_ms"])]
        return argv
    # remap reset is deliberately absent: it clears the F13-F24 board map.
    raise ValueError(f"op {op!r} is not allowed")


def handle(req: dict, live_allowed: bool, **cli_kwargs) -> dict:
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            argv = build_argv(req, tmpdir)
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "code": 2, "dry_run": True, "argv": [], "output": f"error: {exc}"}
        dry_run = req.get("op") in WRITE_OPS and not (live_allowed and req.get("live") is True)
        if dry_run:
            argv = ["--dry-run", *argv]
        out = io.StringIO()
        try:
            code = cli.main(argv, out=out, **cli_kwargs)
        except SystemExit as exc:  # argparse rejects bad values
            code = exc.code if isinstance(exc.code, int) else 2
        except Exception as exc:  # report hidapi/IO failures instead of crashing the app
            print(f"error: {exc}", file=out)
            code = 2
    return {"ok": code == 0, "code": code, "dry_run": dry_run, "argv": argv, "output": out.getvalue()}


def main() -> int:
    try:
        req = json.loads(sys.stdin.read() or "{}")
        if not isinstance(req, dict):
            raise ValueError("request must be a JSON object")
    except ValueError as exc:
        print(json.dumps({"ok": False, "code": 2, "dry_run": True, "argv": [], "output": f"error: {exc}"}))
        return 2
    reply = handle(req, live_allowed=os.environ.get("AULA_LIVE_WRITES") == "1")
    print(json.dumps(reply))
    return 0 if reply["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
