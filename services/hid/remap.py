"""Mapping model for AULA F75 Max key remapping.

Bridges human-readable mapping files (JSON lists of {position, send}) to the
pure remap slot builders in protocol.py:

- `position` names a physical key on the F75 Max, resolved through the
  vendor layout truth table `data/f75max_layout.json` (parsed from
  layouts/rgb-keyboard.xml of the vendor app v1.0.0.5).
- `send` names the USB HID usage that position should produce, resolved
  through the layout plus `USAGE_NAMES` (keys the F75 does not have:
  keypad, PrintScreen, right-hand modifiers, ...), or given directly as
  hex ("0x46").

No I/O beyond reading JSON files. Wire format and citations live in
protocol.py and contracts/hid_protocol.md section 6.4.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import protocol
from .protocol import KeyRemap, ProtocolError

DATA_DIR = Path(__file__).resolve().parent / "data"
LAYOUT_PATH = DATA_DIR / "f75max_layout.json"

# Send-target names with no physical F75 key, plus [F108]-style aliases.
# Codes are standard USB HID usage page 0x07; the exact values are also in
# [F108] pkg/aula/remap.go KeyNameToHID (printscreen 0x46, numlock 0x53,
# numslash 0x54, numstar 0x55, numminus 0x56, rctrl 0xE4, menu 0x65, ...).
USAGE_NAMES = {
    "printscreen": 0x46,
    "scrolllock": 0x47,
    "pause": 0x48,
    "insert": 0x49,
    "home": 0x4A,
    "numlock": 0x53,
    "keypadslash": 0x54,
    "keypadasterisk": 0x55,
    "keypadminus": 0x56,
    "keypadplus": 0x57,
    "keypadenter": 0x58,
    "keypad1": 0x59,
    "keypad2": 0x5A,
    "keypad3": 0x5B,
    "keypad4": 0x5C,
    "keypad5": 0x5D,
    "keypad6": 0x5E,
    "keypad7": 0x5F,
    "keypad8": 0x60,
    "keypad9": 0x61,
    "keypad0": 0x62,
    "keypaddot": 0x63,
    "menu": 0x65,
    "ctrl_l": 0xE0,
    "shift_l": 0xE1,
    "alt_l": 0xE2,
    "win_l": 0xE3,
    "ctrl_r": 0xE4,
    "shift_r": 0xE5,
    "alt_r": 0xE6,
    "win_r": 0xE7,
    # Aliases matching [F108] remap.go names.
    "numslash": 0x54,
    "numstar": 0x55,
    "numminus": 0x56,
    "numplus": 0x57,
}


@dataclass(frozen=True)
class LayoutKey:
    """One physical key from the vendor layout table."""

    key_index: int
    usage: int
    row: int
    col: int
    name: str


def load_layout(path: Path = LAYOUT_PATH) -> dict[str, LayoutKey]:
    """Load the F75 layout truth table as {lowercase name: LayoutKey}."""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    layout: dict[str, LayoutKey] = {}
    for entry in doc["keys"]:
        key = LayoutKey(
            key_index=int(entry["key_index"]),
            usage=int(entry["usage"], 16),
            row=int(entry["row"]),
            col=int(entry["col"]),
            name=entry["name"],
        )
        layout[key.name.lower()] = key
    return layout


def resolve_send(name: str, layout: dict[str, LayoutKey]) -> int:
    """Resolve a `send` value (usage name or hex) to a HID usage code.

    Order: hex literal, USAGE_NAMES, then physical-key names from the
    layout (F7 -> 0x40, ...). Fn is never sendable: its 0xAF is a vendor
    pseudo-code, not a HID usage (data/f75max_layout.json comment).
    """
    text = name.strip()
    lowered = text.lower()
    if lowered.startswith("0x"):
        try:
            usage = int(lowered, 16)
        except ValueError as exc:
            raise ProtocolError(f"send value {name!r} is not hex") from exc
    elif lowered in USAGE_NAMES:
        usage = USAGE_NAMES[lowered]
    elif lowered in layout:
        usage = layout[lowered].usage
    else:
        raise ProtocolError(f"unknown send target {name!r}")
    if usage == protocol.FN_PSEUDO_USAGE:
        raise ProtocolError(
            "0xAF is the vendor Fn pseudo-usage, not a sendable HID usage"
        )
    if not 0x01 <= usage <= 0xFF:
        raise ProtocolError(f"send usage {usage:#x} out of range 01..FF")
    return usage


def resolve_position(name: str, layout: dict[str, LayoutKey]) -> LayoutKey:
    """Resolve a `position` value to its layout key (Fn guard applies later)."""
    key = layout.get(name.strip().lower())
    if key is None:
        raise ProtocolError(f"unknown position {name!r} (not in f75max_layout)")
    return key


def resolve_mapping(
    entries, layout: dict[str, LayoutKey] | None = None
) -> list[KeyRemap]:
    """Resolve mapping entries [{position, send}, ...] into KeyRemap slots.

    Raises ProtocolError for unknown names, duplicate positions, and the Fn
    key (protocol.KeyRemap.validate guard).
    """
    layout = layout if layout is not None else load_layout()
    remaps: list[KeyRemap] = []
    for entry in entries:
        try:
            position = entry["position"]
            send = entry["send"]
        except (TypeError, KeyError) as exc:
            raise ProtocolError(
                f"mapping entry {entry!r} needs 'position' and 'send'"
            ) from exc
        key = resolve_position(position, layout)
        if key.key_index == protocol.FN_KEY_INDEX:
            raise ProtocolError(
                "Fn key is the hardware layer key and cannot be remapped"
            )
        usage = resolve_send(send, layout)
        remaps.append(protocol.make_key_remap(key.key_index, usage))
    seen: set[int] = set()
    for remap in remaps:
        if remap.key_index in seen:
            raise ProtocolError(f"duplicate remap for key_index {remap.key_index}")
        seen.add(remap.key_index)
    return remaps


def load_mapping_file(path) -> list[dict]:
    """Load a mapping file: either a bare JSON list of {position, send} or an
    object with a 'mappings' list (extra keys like 'comment' are ignored)."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except OSError as exc:
        raise ProtocolError(f"cannot read mapping file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"{path} is not valid JSON: {exc}") from exc
    if isinstance(doc, dict):
        doc = doc.get("mappings")
    if not isinstance(doc, list):
        raise ProtocolError(
            f"{path}: expected a JSON list of mappings or an object with a "
            "'mappings' list"
        )
    return doc


def default_mapping() -> list[dict]:
    """The identity mapping: no entries, so every remap slot stays
    00 00 00 00 and every key produces its default output.

    [F108] ai-docs/key-remap-protocol.md: "A slot of 00 00 00 00 means no
    remap (key keeps its default behavior)"; pkg/aula/remap.go ResetKeyRemap
    clears all remaps by sending the table built from nil remaps. Applying
    this mapping is therefore the full reset.
    """
    return []


def remap_transactions(
    remaps, fn_layer: bool = False
) -> list[tuple[str, bytes]]:
    """The full wired transaction sequence for one layer as (label, payload).

    Order per [F108] pkg/aula/remap.go sendRemapTable: begin, remap init,
    nine 64-byte table packets, apply, finalize.
    """
    layer = "fn" if fn_layer else "normal"
    table = protocol.build_remap_table(remaps)
    steps: list[tuple[str, bytes]] = [
        ("begin", protocol.build_wired_begin()),
        (f"remap-init-{layer}", protocol.build_wired_remap_init(fn_layer)),
    ]
    packets = protocol.split_remap_table(table)
    steps.extend(
        (f"table-{i + 1}/{len(packets)}", packet)
        for i, packet in enumerate(packets)
    )
    steps.append(("apply", protocol.build_wired_apply()))
    steps.append(("finalize", protocol.build_wired_finalize()))
    return steps


def describe_remap(remap: KeyRemap, layout: dict[str, LayoutKey]) -> str:
    """One stable human-readable line per resolved slot (used by `remap show`)."""
    by_index = {key.key_index: key for key in layout.values()}
    key = by_index.get(remap.key_index)
    where = (
        f"{key.name} (key_index {remap.key_index}, {key.row}#{key.col})"
        if key
        else f"key_index {remap.key_index}"
    )
    slot = bytes(
        (remap.action, remap.param1, remap.param2, remap.param3)
    ).hex(" ")
    return f"{where} -> slot {slot} @ offset {remap.key_index * 4}"
