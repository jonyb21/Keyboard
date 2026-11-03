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


# Typed send vocabulary (vendor FUN_004183d0, docs command-table.md 2.7).
# Slot action 01 is the vendor's mouse action (the F108 doc calls 01 "special").
MOUSE_ACTION = protocol.REMAP_ACTION_SPECIAL
# (P1, P2) pairs are OBSERVED in the builder. Which button each pair means is
# INFERRED from UI string order; Back/Forward follow the USB mouse bitmask
# (08 = Button4/Back, 10 = Button5/Forward), the doc's string order says the
# opposite, so this one is a guess. DoubleClick (01 03) is bytes-observed only.
MOUSE_NAMES = {
    "left": (0x01, 0x01),
    "right": (0x01, 0x02),
    "middle": (0x01, 0x04),
    "doubleclick": (0x01, 0x03),
    "back": (0x01, 0x08),
    "button4": (0x01, 0x08),
    "forward": (0x01, 0x10),
    "button5": (0x01, 0x10),
    "scrollup": (0x03, 0x01),
    "scrolldown": (0x03, 0xFF),
}
# 16-bit consumer usages (OBSERVED in the vendor id 500-517 table; names are
# inferred from the usage values).
MEDIA_NAMES = {
    "playpause": 0x00CD,
    "stop": 0x00B7,
    "prev": 0x00B6,
    "next": 0x00B5,
    "volumeup": 0x00E9,
    "volumedown": 0x00EA,
    "mute": 0x00E2,
    "mycomputer": 0x0194,
    "calculator": 0x0192,
    "mail": 0x018A,
    "mediaselect": 0x0183,
    "webhome": 0x0223,
    "refresh": 0x0227,
    "webstop": 0x0226,
    "back": 0x0224,
    "forward": 0x0225,
    "favorites": 0x022A,
    "search": 0x0221,
}
COMBO_MODIFIERS = {
    "ctrl": 0x01, "lctrl": 0x01, "ctrl_l": 0x01,
    "shift": 0x02, "lshift": 0x02, "shift_l": 0x02,
    "alt": 0x04, "lalt": 0x04, "alt_l": 0x04,
    "win": 0x08, "gui": 0x08, "lwin": 0x08, "win_l": 0x08,
    "rctrl": 0x10, "ctrl_r": 0x10,
    "rshift": 0x20, "shift_r": 0x20,
    "ralt": 0x40, "alt_r": 0x40,
    "rwin": 0x80, "win_r": 0x80,
}
# 05 03 00 00 bytes OBSERVED (vendor binding type 1); "disable key" is INFERRED.
DISABLED_SLOT = (protocol.REMAP_ACTION_PROFILE, 0x03, 0x00, 0x00)
# Macro slot 06 IDX A B: A = play mode, B = loop count, both INFERRED from UI
# strings. Only "once" is supported; A=0 for it is a guess.
MACRO_MODES = {"once": 0x00}
MACRO_MAX_INDEX = 99


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


def _lookup(table: dict, value, what: str):
    if not isinstance(value, str) or value.strip().lower() not in table:
        raise ProtocolError(f"unknown {what} {value!r}")
    return table[value.strip().lower()]


def _resolve_combo(index: int, text, layout) -> KeyRemap:
    """'Ctrl+Shift+Esc' -> 02 MOD KEY 00. A lone key or modifier falls back
    to make_key_remap so it matches the plain string form byte for byte."""
    if not isinstance(text, str) or not text.strip():
        raise ProtocolError(f"combo must be text like 'Ctrl+Shift+Esc', got {text!r}")
    tokens = [t.strip() for t in text.split("+")]
    if len(tokens) == 1:
        return protocol.make_key_remap(index, resolve_send(tokens[0], layout))
    mods = 0
    keys = []
    for token in tokens:
        bit = COMBO_MODIFIERS.get(token.lower())
        if bit is None:
            keys.append(token)
        else:
            mods |= bit
    if len(keys) > 1:
        raise ProtocolError(f"combo {text!r} has more than one non-modifier key")
    usage = resolve_send(keys[0], layout) if keys else 0
    if usage and protocol.is_modifier_usage(usage):
        mods |= protocol.MODIFIER_BITS[usage]
        usage = 0
    return KeyRemap(index, protocol.REMAP_ACTION_KEY, mods, usage).validate()


def resolve_slot(index: int, send, layout) -> KeyRemap:
    """Resolve a `send` value (plain string or typed object) to a slot."""
    if isinstance(send, str):
        if send.strip().lower() in ("disabled", "disable"):
            return KeyRemap(index, *DISABLED_SLOT).validate()
        return protocol.make_key_remap(index, resolve_send(send, layout))
    if not isinstance(send, dict):
        raise ProtocolError(f"send must be a string or object, got {send!r}")
    kinds = [k for k in ("combo", "media", "mouse", "macro") if k in send]
    if len(kinds) != 1:
        raise ProtocolError(
            "typed send needs exactly one of combo, media, mouse, macro"
        )
    kind = kinds[0]
    extra = set(send) - {kind} - ({"mode", "count"} if kind == "macro" else set())
    if extra:
        raise ProtocolError(f"unexpected send fields {sorted(extra)}")
    if kind == "combo":
        return _resolve_combo(index, send["combo"], layout)
    if kind == "media":
        usage = _lookup(MEDIA_NAMES, send["media"], "media name")
        return KeyRemap(
            index, protocol.REMAP_ACTION_CONSUMER, usage & 0xFF, usage >> 8
        ).validate()
    if kind == "mouse":
        p1, p2 = _lookup(MOUSE_NAMES, send["mouse"], "mouse name")
        return KeyRemap(index, MOUSE_ACTION, p1, p2).validate()
    macro, count = send["macro"], send.get("count", 1)
    mode = _lookup(MACRO_MODES, send.get("mode", "once"), "macro mode")
    for name, value, lo, hi in (
        ("macro index", macro, 0, MACRO_MAX_INDEX),
        ("macro count", count, 1, 255),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
            raise ProtocolError(f"{name} must be an integer {lo}..{hi}")
    return KeyRemap(index, protocol.REMAP_ACTION_MACRO, macro, mode, count).validate()


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
        remaps.append(resolve_slot(key.key_index, send, layout))
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
    note = ""
    if (remap.action, remap.param1, remap.param2, remap.param3) == DISABLED_SLOT:
        note = " [INFERRED: disable key]"
    elif remap.action == protocol.REMAP_ACTION_MACRO:
        note = " [INFERRED: macro mode/count bytes]"
    return f"{where} -> slot {slot} @ offset {remap.key_index * 4}{note}"
