"""Macro blob builder for the wired `04 19` / `04 15` upload.

Format recovered statically from the vendor DeviceDriver FUN_0042d6d0
(docs command-table.md 2.8). Pure functions plus a JSON loader; no hardware.

Blob (little-endian): 100 index entries x 4 B at 0 (u16 record offset + two
zero bytes, or FF FF FF FF when the macro has no events), records from offset
400: u16 event_count, 6 bytes zero (meaning UNKNOWN), then 4-byte events
[u16 delay][code][type].
"""

from __future__ import annotations

import json

from . import protocol, remap as remap_model
from .protocol import ProtocolError

MAX_MACROS = 100
INDEX_BYTES = MAX_MACROS * 4
GUARD_BYTES = 0xA8C  # vendor refuses to send when events * 8 > 2700
MIN_DELAY_MS = 10  # vendor clamps shorter delays up to 10 ms

KEY_DOWN, KEY_UP, MOUSE_DOWN, MOUSE_UP, DELAY = 0xB0, 0x30, 0x90, 0x10, 0x50
MOUSE_CODES = {"left": 0x01, "middle": 0x04, "right": 0x02}
_EVENT_KINDS = ("down", "up", "delay", "mouse_down", "mouse_up")


def _event(item, layout) -> bytes:
    if not isinstance(item, dict) or len(item) != 1:
        raise ProtocolError(f"macro event {item!r} needs exactly one of {_EVENT_KINDS}")
    ((kind, value),) = item.items()
    if kind in ("down", "up"):
        code = remap_model.resolve_send(value, layout)
        return bytes((0, 0, code, KEY_DOWN if kind == "down" else KEY_UP))
    if kind in ("mouse_down", "mouse_up"):
        code = MOUSE_CODES.get(str(value).strip().lower())
        if code is None:
            raise ProtocolError(f"unknown macro mouse button {value!r}")
        return bytes((0, 0, code, MOUSE_DOWN if kind == "mouse_down" else MOUSE_UP))
    if kind == "delay":
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFF:
            raise ProtocolError(f"macro delay {value!r} must be an integer 0..65535 ms")
        delay = max(value, MIN_DELAY_MS)
        return bytes((delay & 0xFF, delay >> 8, 0, DELAY))
    raise ProtocolError(f"unknown macro event {kind!r} (expected one of {_EVENT_KINDS})")


def build_blob(macros, layout=None) -> bytes:
    """Index table + records, unpadded (len = bytes used)."""
    layout = layout if layout is not None else remap_model.load_layout()
    if not isinstance(macros, list) or len(macros) > MAX_MACROS:
        raise ProtocolError(f"macros must be a list of at most {MAX_MACROS}")
    index = bytearray(INDEX_BYTES)
    records = bytearray()
    total_events = 0
    for i, macro in enumerate(macros):
        events = macro.get("events") if isinstance(macro, dict) else None
        if not isinstance(events, list):
            raise ProtocolError(f"macro {i} needs an 'events' list")
        if not events:
            index[i * 4 : i * 4 + 4] = b"\xff\xff\xff\xff"
            continue
        encoded = [_event(item, layout) for item in events]
        total_events += len(encoded)
        offset = INDEX_BYTES + len(records)
        index[i * 4 : i * 4 + 2] = offset.to_bytes(2, "little")
        records += len(encoded).to_bytes(2, "little") + bytes(6) + b"".join(encoded)
    for i in range(len(macros), MAX_MACROS):
        index[i * 4 : i * 4 + 4] = b"\xff\xff\xff\xff"
    if total_events * 8 > GUARD_BYTES:
        raise ProtocolError(
            f"insufficient macro space: {total_events} events (limit {GUARD_BYTES // 8})"
        )
    return bytes(index + records)


def build_pages(blob: bytes) -> list[bytes]:
    """PAGES = ceil(len/64)+1 64-byte pages, AA 55 in the last two bytes."""
    size = protocol.WIRED_REPORT_SIZE
    pages = -(-len(blob) // size) + 1
    data = bytearray(blob.ljust(pages * size, b"\x00"))
    data[-2:] = protocol.TRAILER
    return [bytes(data[i : i + size]) for i in range(0, len(data), size)]


def macro_transactions(macros, layout=None) -> list[tuple[str, bytes]]:
    """04 19 -> 04 15 [8]=PAGES -> data pages -> 04 02 (no 04 18, no 04 F0)."""
    pages = build_pages(build_blob(macros, layout))
    steps = [
        ("macro-prepare", protocol.build_wired_macro_prepare()),
        ("macro-init", protocol.build_wired_macro_init(len(pages))),
    ]
    steps.extend((f"page-{i + 1}/{len(pages)}", p) for i, p in enumerate(pages))
    steps.append(("apply", protocol.build_wired_apply()))
    return steps


def load_macro_file(path) -> list[dict]:
    """A bare JSON list of macros or an object with a 'macros' list."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except OSError as exc:
        raise ProtocolError(f"cannot read macro file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"{path} is not valid JSON: {exc}") from exc
    if isinstance(doc, dict):
        doc = doc.get("macros")
    if not isinstance(doc, list):
        raise ProtocolError(f"{path}: expected a JSON list of macros or a 'macros' list")
    return doc
