"""Pure payload builders and parsers for the AULA F75 Max stock firmware.

Deterministic, no I/O. Every opcode carries a citation to the public source
it was recovered from. Full wire contract: contracts/hid_protocol.md.

Source keys used in comments:
    [OSX]  github.com/mastercoder26/Aula-F75-Max-OSX (exact same hardware)
    [F108] github.com/parsiya/f108-pro (same 0C45:800A Sonix platform)

Conflict rule: where the two disagree, the F75-Max-specific [OSX] wins
(see contracts/hid_protocol.md section 8).
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Report geometry -------------------------------------------------------

# Wired config channel: 64-byte feature reports on the 0xFF13 collection.
# [F108] pkg/aula/device.go: reportSize = 64; ai-docs/hid-protocol.md
# "Packet Format". No report ID on the wire (transport prepends 0x00 for
# Windows HID APIs).
WIRED_REPORT_SIZE = 64

# Dongle config channel: 32-byte output reports on the 0xFF60 collection.
# [OSX] Sources/F75Probe/main.m: all buildWireless* builders use uint8_t[32].
DONGLE_REPORT_SIZE = 32

# Inter-command pacing in seconds. Vendor config.xml <cmd_delaytime value="35"/>;
# [F108] pkg/aula/device.go: cmdDelay = 35 * time.Millisecond.
COMMAND_DELAY_S = 0.035

# Trailer marker bytes, wire order AA 55 (uint16 0x55AA little-endian).
# [F108] ai-docs/hid-protocol.md "Trailer" (USB-capture confirmed);
# [OSX] Sources/AulaF75Bar/main.m timeCommand[62]=0xaa, [63]=0x55.
# Conflict C1: [F108] lighting.go writes 55 AA, contradicting its own doc
# and remap.go; the F75-specific [OSX] order AA 55 is adopted.
TRAILER = bytes((0xAA, 0x55))

# --- Lighting modes --------------------------------------------------------

# Mode IDs shared by wired and dongle transports.
# [F108] ai-docs/hid-protocol.md "Lighting Mode IDs" + pkg/aula/lighting.go;
# names match the vendor 1033.lan strings 521-540.
LIGHT_MODES = {
    0: "Off",
    1: "Static",
    2: "SingleOn",
    3: "SingleOff",
    4: "Glittering",
    5: "Falling",
    6: "Colourful",
    7: "Breath",
    8: "Spectrum",
    9: "Outward",
    10: "Scrolling",
    11: "Rolling",  # factory default (rgb-keyboard.xml default_mode=11)
    12: "Rotating",
    13: "Explode",
    14: "Launch",
    15: "Ripples",
    16: "Flowing",
    17: "Pulsating",
    18: "Tilt",
    19: "Shuttle",
}

LIGHT_MODE_IDS = {name.lower(): mode for mode, name in LIGHT_MODES.items()}

BRIGHTNESS_MAX = 5  # rgb-keyboard.xml brightness_max="5"
SPEED_MAX = 5  # rgb-keyboard.xml speed_max="5"


class ProtocolError(ValueError):
    """Raised for out-of-range protocol parameters."""


@dataclass(frozen=True)
class LightingConfig:
    """Parameters for a lighting-mode change (both transports)."""

    mode: int
    red: int = 0
    green: int = 0
    blue: int = 0
    brightness: int = 5
    speed: int = 3
    direction: int = 0
    colorful: bool = False

    def validate(self) -> "LightingConfig":
        if self.mode not in LIGHT_MODES:
            raise ProtocolError(
                f"mode {self.mode} out of range 0..{max(LIGHT_MODES)}"
            )
        for name, value in (
            ("red", self.red),
            ("green", self.green),
            ("blue", self.blue),
        ):
            if not 0 <= value <= 255:
                raise ProtocolError(f"{name} {value} out of range 0..255")
        if not 0 <= self.brightness <= BRIGHTNESS_MAX:
            raise ProtocolError(
                f"brightness {self.brightness} out of range 0..{BRIGHTNESS_MAX}"
            )
        if not 0 <= self.speed <= SPEED_MAX:
            raise ProtocolError(f"speed {self.speed} out of range 0..{SPEED_MAX}")
        if self.direction not in (0, 1):
            raise ProtocolError(f"direction {self.direction} must be 0 or 1")
        return self


def parse_color(text: str) -> tuple[int, int, int]:
    """Parse 'RRGGBB' (optionally '#RRGGBB' / '0xRRGGBB') into an RGB tuple."""
    cleaned = text.strip().lstrip("#")
    if cleaned.lower().startswith("0x"):
        cleaned = cleaned[2:]
    if len(cleaned) != 6:
        raise ProtocolError(f"color {text!r} is not RRGGBB")
    try:
        value = int(cleaned, 16)
    except ValueError as exc:
        raise ProtocolError(f"color {text!r} is not hex") from exc
    return (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF


def resolve_mode(text: str) -> int:
    """Resolve a mode given as a decimal number or an effect name."""
    stripped = text.strip()
    if stripped.lstrip("-").isdigit():
        mode = int(stripped)
        if mode not in LIGHT_MODES:
            raise ProtocolError(f"mode {mode} out of range 0..{max(LIGHT_MODES)}")
        return mode
    try:
        return LIGHT_MODE_IDS[stripped.lower()]
    except KeyError as exc:
        raise ProtocolError(f"unknown lighting mode {text!r}") from exc


# --- Shared helpers --------------------------------------------------------


def checksum8(payload: bytes, checksum_index: int) -> int:
    """8-bit additive checksum over the whole payload with the checksum byte
    treated as zero.

    [OSX] Sources/F75Probe/main.m applyRawChecksum().
    """
    if not 0 <= checksum_index < len(payload):
        raise ProtocolError(
            f"checksum index {checksum_index} outside payload of {len(payload)}"
        )
    total = sum(payload) - payload[checksum_index]
    return total & 0xFF


def _wired(payload: dict[int, int]) -> bytes:
    buf = bytearray(WIRED_REPORT_SIZE)
    for offset, value in payload.items():
        buf[offset] = value
    return bytes(buf)


def _dongle(payload: dict[int, int]) -> bytes:
    """Build a 32-byte dongle packet: trailer AA 55 at 17-18, checksum at 31.

    [OSX] Sources/F75Probe/main.m builders + applyRawChecksum.
    """
    buf = bytearray(DONGLE_REPORT_SIZE)
    for offset, value in payload.items():
        buf[offset] = value
    buf[31] = checksum8(bytes(buf), 31)
    return bytes(buf)


# --- Wired transaction commands (feature reports on 0xFF13) ----------------


def build_wired_begin() -> bytes:
    """`04 18` begin transaction, requires readback.

    [F108] pkg/aula/device.go beginTransaction; verified on F75 hardware by
    [OSX] AulaF75Bar/main.m beginCommand = {0x04, 0x18}.
    """
    return _wired({0: 0x04, 1: 0x18})


def build_wired_apply() -> bytes:
    """`04 02` apply/commit, requires readback.

    [F108] pkg/aula/device.go applyTransaction; [OSX] exitCommand = {0x04, 0x02}.
    """
    return _wired({0: 0x04, 1: 0x02})


def build_wired_finalize() -> bytes:
    """`04 F0` finalize, no readback.

    [F108] pkg/aula/device.go finalizeTransaction. Not present in [OSX]
    screen/clock flows; [F108] sends it after lighting.
    """
    return _wired({0: 0x04, 1: 0xF0})


def build_wired_lighting_init() -> bytes:
    """`04 13` lighting init with byte[8]=0x01, requires readback.

    [F108] pkg/aula/device.go lightingInit; ai-docs/hid-protocol.md
    "Step 2 detail: Byte[0]=04, Byte[1]=13, Byte[8]=01".
    """
    return _wired({0: 0x04, 1: 0x13, 8: 0x01})


def build_wired_lighting_data(config: LightingConfig) -> bytes:
    """Wired lighting data payload (step 3 of the lighting sequence).

    Layout from [F108] ai-docs/hid-protocol.md "Step 3 data packet layout":
    mode@0, R@1 G@2 B@3, colorful@8, brightness@9, speed@10, direction@11,
    trailer@14-15. Parameter bytes are left zero when mode == 0 (off), per
    the same doc and pkg/aula/lighting.go.
    Trailer wire order AA 55 per conflict C1 (contracts/hid_protocol.md).
    """
    config.validate()
    fields = {0: config.mode}
    if config.mode != 0:
        fields.update(
            {
                1: config.red,
                2: config.green,
                3: config.blue,
                8: 1 if config.colorful else 0,
                9: config.brightness,
                10: config.speed,
                11: config.direction,
            }
        )
    fields.update({14: TRAILER[0], 15: TRAILER[1]})
    return _wired(fields)


def build_wired_clock_init() -> bytes:
    """`04 28` clock sync init with byte[8]=0x01, requires readback.

    [OSX] AulaF75Bar/main.m selectCommand = {0x04, 0x28, 0,0,0,0,0,0, 0x01}
    (verified working on the F75 Max); [F108] ai-docs/hid-protocol.md agrees.
    """
    return _wired({0: 0x04, 1: 0x28, 8: 0x01})


def build_wired_clock_data(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    second: int,
    weekday: int,
) -> bytes:
    """Wired LCD clock data payload. weekday: 0=Sunday .. 6=Saturday.

    [OSX] AulaF75Bar/main.m timeCommand (verified working): 0x00@0, 0x01@1
    (profile), 0x5A@2 (magic), year-2000@3, month@4, day@5, hour@6,
    minute@7, second@8, weekday@10, trailer AA 55 @62-63.
    """
    if not 2000 <= year <= 2255:
        raise ProtocolError(f"year {year} out of range 2000..2255")
    for name, value, lo, hi in (
        ("month", month, 1, 12),
        ("day", day, 1, 31),
        ("hour", hour, 0, 23),
        ("minute", minute, 0, 59),
        ("second", second, 0, 59),
        ("weekday", weekday, 0, 6),
    ):
        if not lo <= value <= hi:
            raise ProtocolError(f"{name} {value} out of range {lo}..{hi}")
    return _wired(
        {
            1: 0x01,
            2: 0x5A,
            3: year - 2000,
            4: month,
            5: day,
            6: hour,
            7: minute,
            8: second,
            10: weekday,
            62: TRAILER[0],
            63: TRAILER[1],
        }
    )


def parse_wired_ack(response: bytes, command: bytes | None = None) -> bool:
    """True when a wired readback response acknowledges the command.

    [F108] ai-docs/hid-protocol.md "Read-back": the response has byte[3] set
    to 0x01 and echoes the command bytes 0-1. If `command` is given, the echo
    is checked too.
    """
    if len(response) < 4:
        return False
    if command is not None and response[:2] != bytes(command[:2]):
        return False
    return response[3] == 0x01


# --- Wired key remap (04 11 normal / 04 27 FN layer) -----------------------

# Remap table geometry. [F108] pkg/aula/remap.go: remapBufSize = 0x240
# ("576 bytes = 144 slots x 4 bytes, last 2 bytes are 0x55AA trailer");
# ai-docs/key-remap-protocol.md "Remap Data Buffer (576 bytes)".
# F75-unverified: table size taken from the F108; the F75's key indices
# (max 121 per data/f75max_layout.json) fit inside the same 144-slot space.
REMAP_TABLE_SIZE = 0x240
REMAP_SLOT_COUNT = 144
REMAP_PACKET_COUNT = REMAP_TABLE_SIZE // WIRED_REPORT_SIZE  # 9 x 64 bytes

# Highest usable slot: [F108] remap.go skips indices where idx*4+3 lands in
# the trailer area (idx*4+3 >= remapBufSize-2), i.e. slots 1..142 are valid
# and slot 0 is unused ("Slot 0 (unused, always zero)",
# ai-docs/key-remap-protocol.md).
REMAP_MAX_KEY_INDEX = 142

# The F75's Fn key occupies key_index 96 with vendor pseudo-usage 0xAF
# (data/f75max_layout.json, from the vendor rgb-keyboard.xml). It selects the
# firmware FN layer and must never be remapped away.
FN_KEY_INDEX = 96
FN_PSEUDO_USAGE = 0xAF

# Slot action types. [F108] pkg/aula/remap.go RemapAction constants;
# ai-docs/key-remap-protocol.md "Action Types".
REMAP_ACTION_NONE = 0x00  # passthrough (slot all zero)
REMAP_ACTION_SPECIAL = 0x01  # lock/system functions
REMAP_ACTION_KEY = 0x02  # modifier bitmask + HID usage
REMAP_ACTION_CONSUMER = 0x03  # consumer page (multimedia)
REMAP_ACTION_PROFILE = 0x05  # profile/lock switch
REMAP_ACTION_MACRO = 0x06  # macro execution
REMAP_ACTION_MOUSE = 0x07  # mouse button / scroll

REMAP_ACTIONS = (
    REMAP_ACTION_NONE,
    REMAP_ACTION_SPECIAL,
    REMAP_ACTION_KEY,
    REMAP_ACTION_CONSUMER,
    REMAP_ACTION_PROFILE,
    REMAP_ACTION_MACRO,
    REMAP_ACTION_MOUSE,
)

# HID modifier usage (0xE0-0xE7) -> USB HID modifier bit.
# [F108] pkg/aula/remap.go hidToModifierBit (Ghidra FUN_00451b90);
# ai-docs/key-remap-protocol.md "Modifier bitmask (standard USB HID)".
MODIFIER_BITS = {
    0xE0: 0x01,  # Left Ctrl
    0xE1: 0x02,  # Left Shift
    0xE2: 0x04,  # Left Alt
    0xE3: 0x08,  # Left Win/GUI
    0xE4: 0x10,  # Right Ctrl
    0xE5: 0x20,  # Right Shift
    0xE6: 0x40,  # Right Alt
    0xE7: 0x80,  # Right Win/GUI
}


def is_modifier_usage(code: int) -> bool:
    """True for HID modifier usages 0xE0..0xE7.

    [F108] pkg/aula/remap.go isModifierKey.
    """
    return 0xE0 <= code <= 0xE7


@dataclass(frozen=True)
class KeyRemap:
    """One remap slot: physical key_index -> 4-byte action slot.

    Slot format [action, param1, param2, param3] per [F108]
    ai-docs/key-remap-protocol.md "Slot Format (4 bytes)" and
    pkg/aula/remap.go KeyRemap.
    """

    key_index: int
    action: int
    param1: int = 0
    param2: int = 0
    param3: int = 0

    def validate(self) -> "KeyRemap":
        if self.key_index == FN_KEY_INDEX:
            raise ProtocolError(
                "Fn key is the hardware layer key and cannot be remapped"
            )
        if not 1 <= self.key_index <= REMAP_MAX_KEY_INDEX:
            raise ProtocolError(
                f"key_index {self.key_index} out of range 1..{REMAP_MAX_KEY_INDEX}"
            )
        if self.action not in REMAP_ACTIONS:
            raise ProtocolError(f"remap action {self.action:#04x} unknown")
        for name, value in (
            ("param1", self.param1),
            ("param2", self.param2),
            ("param3", self.param3),
        ):
            if not 0 <= value <= 255:
                raise ProtocolError(f"{name} {value} out of range 0..255")
        return self


def make_key_remap(key_index: int, target_usage: int) -> KeyRemap:
    """Remap a physical key to produce a keyboard usage (action 0x02).

    [F108] pkg/aula/remap.go NewKeySwap: modifier targets (0xE0-0xE7) put
    their modifier bit in param1 with param2 zero; normal keys put the HID
    usage in param2 with param1 zero. ai-docs/key-remap-protocol.md
    "Type 0x02: Key Combination" examples: CapsLock->A = 02 00 04 00,
    CapsLock->Left Ctrl = 02 01 00 00.
    """
    if not 0x01 <= target_usage <= 0xFF:
        raise ProtocolError(f"target usage {target_usage:#04x} out of range 01..FF")
    if target_usage == FN_PSEUDO_USAGE:
        raise ProtocolError(
            "0xAF is the vendor Fn pseudo-usage, not a sendable HID usage"
        )
    if is_modifier_usage(target_usage):
        return KeyRemap(
            key_index=key_index,
            action=REMAP_ACTION_KEY,
            param1=MODIFIER_BITS[target_usage],
        ).validate()
    return KeyRemap(
        key_index=key_index,
        action=REMAP_ACTION_KEY,
        param2=target_usage,
    ).validate()


def build_wired_remap_init(fn_layer: bool = False) -> bytes:
    """`04 11` (normal layer) / `04 27` (FN layer) init with byte[8]=0x09,
    requires readback.

    [F108] pkg/aula/remap.go sendRemapTable step 2; ai-docs/
    key-remap-protocol.md "Step 2: Init Command" (Ghidra FUN_004185e0:
    param_1=0 -> 0x11, param_1=1 -> 0x27).
    """
    return _wired({0: 0x04, 1: 0x27 if fn_layer else 0x11, 8: 0x09})


def build_remap_table(remaps) -> bytes:
    """Build the full 576-byte remap table for one layer.

    Slot position = key_index * 4; untouched slots stay 00 00 00 00
    (= passthrough). Trailer AA 55 at offsets 574-575 (uint16 0x55AA
    little-endian). [F108] pkg/aula/remap.go sendRemapTable step 3;
    ai-docs/key-remap-protocol.md "Layout". An all-zero table clears every
    remap ([F108] remap.go ResetKeyRemap sends nil remaps).
    """
    table = bytearray(REMAP_TABLE_SIZE)
    seen: set[int] = set()
    for remap in remaps:
        remap.validate()
        if remap.key_index in seen:
            raise ProtocolError(
                f"duplicate remap for key_index {remap.key_index}"
            )
        seen.add(remap.key_index)
        offset = remap.key_index * 4
        table[offset] = remap.action
        table[offset + 1] = remap.param1
        table[offset + 2] = remap.param2
        table[offset + 3] = remap.param3
    table[REMAP_TABLE_SIZE - 2] = TRAILER[0]
    table[REMAP_TABLE_SIZE - 1] = TRAILER[1]
    return bytes(table)


def split_remap_table(table: bytes) -> list[bytes]:
    """Split a 576-byte remap table into nine 64-byte feature payloads.

    [F108] pkg/aula/device.go sendMultiPacket (64-byte chunks, in order);
    ai-docs/key-remap-protocol.md "576-byte remap buffer sent as 9 x 64-byte
    packets".
    """
    if len(table) != REMAP_TABLE_SIZE:
        raise ProtocolError(
            f"remap table must be {REMAP_TABLE_SIZE} bytes, got {len(table)}"
        )
    return [
        table[i : i + WIRED_REPORT_SIZE]
        for i in range(0, REMAP_TABLE_SIZE, WIRED_REPORT_SIZE)
    ]


# --- Dongle commands (32-byte output reports on 0xFF60) --------------------


def build_battery_request() -> bytes:
    """Battery percent request over the 2.4G dongle.

    [OSX] AulaF75Bar/main.m BatteryFromAulaRawHID: payload[0]=0x20,
    payload[1]=0x01, payload[31]=0x21 (which equals the additive checksum).
    """
    return _dongle({0: 0x20, 1: 0x01})


def parse_battery_response(report: bytes) -> int | None:
    """Extract battery percent from a dongle input report, else None.

    [OSX] AulaF75Bar/main.m BatteryInputCallback: accepted when
    report[0]==0x20, report[1]==0x01 and 0 < report[3] <= 100.
    A leading 0x00 report-ID byte (hidapi read on some backends) is tolerated.
    Byte 2 meaning is UNKNOWN (see contracts/hid_protocol.md section 9).
    """
    data = bytes(report)
    if len(data) >= 5 and data[0] == 0x00 and data[1] == 0x20 and data[2] == 0x01:
        data = data[1:]
    if len(data) < 4:
        return None
    if data[0] == 0x20 and data[1] == 0x01 and 0 < data[3] <= 100:
        return data[3]
    return None


def build_dongle_lighting(config: LightingConfig) -> bytes:
    """All-in-one dongle lighting packet.

    [OSX] F75Probe/main.m buildWirelessRGBLEDModeReportVariant: 05@0, 10@1,
    mode@3, R@4 G@5 B@6, colorful@11, brightness@12, speed@13, direction@14,
    trailer AA 55 @17-18, checksum@31. Parameter bytes are zero when mode==0.
    [F108] ai-docs/hid-protocol.md wireless `05 10` packet matches
    field-for-field after a one-byte shift (conflict C4).
    """
    config.validate()
    fields = {0: 0x05, 1: 0x10, 3: config.mode}
    if config.mode != 0:
        fields.update(
            {
                4: config.red,
                5: config.green,
                6: config.blue,
                11: 1 if config.colorful else 0,
                12: config.brightness,
                13: config.speed,
                14: config.direction,
            }
        )
    fields.update({17: TRAILER[0], 18: TRAILER[1]})
    return _dongle(fields)


def build_dongle_commit() -> bytes:
    """Optional dongle commit packet: 0x0F@0, checksum@31.

    [OSX] F75Probe/main.m buildWirelessRGBCommitReportVariant. Probe-only in
    the source; not sent by default (contracts/hid_protocol.md section 5.2).
    """
    return _dongle({0: 0x0F})


def build_dongle_function_settings(
    fn_switch: int | None = None,
    sleep_time: int | None = None,
    response_level: int | None = None,
    game_mode: int = 0,
    disable_alt_tab: int = 0,
    disable_alt_f4: int = 0,
    disable_win: int = 0,
) -> bytes:
    """Dongle function-settings / game-mode packet (`07 10`).

    [OSX] F75Probe/main.m buildWirelessKeyResponseReportVariant and
    buildWirelessGameModeReportVariant: 07@0, 10@1, 01@4, include-flags at
    5/6/7, fn_switch@8, sleep_time@9, response_level@11, game_mode@12,
    disable_alt_tab@13, disable_alt_f4@14, disable_win@15, trailer@17-18,
    checksum@31. Fields passed as None are excluded via their include flag.
    """
    if fn_switch is not None and fn_switch not in (0, 1):
        raise ProtocolError(f"fn_switch {fn_switch} must be 0 or 1")
    if sleep_time is not None and not 0 <= sleep_time <= 3:
        raise ProtocolError(f"sleep_time {sleep_time} out of range 0..3")
    if response_level is not None and not 1 <= response_level <= 5:
        raise ProtocolError(f"response_level {response_level} out of range 1..5")
    for name, value in (
        ("game_mode", game_mode),
        ("disable_alt_tab", disable_alt_tab),
        ("disable_alt_f4", disable_alt_f4),
        ("disable_win", disable_win),
    ):
        if value not in (0, 1):
            raise ProtocolError(f"{name} {value} must be 0 or 1")

    fields = {
        0: 0x07,
        1: 0x10,
        4: 0x01,
        5: 0x01 if fn_switch is not None else 0x00,
        6: 0x01 if sleep_time is not None else 0x00,
        7: 0x01 if response_level is not None else 0x00,
        12: game_mode,
        13: disable_alt_tab,
        14: disable_alt_f4,
        15: disable_win,
        17: TRAILER[0],
        18: TRAILER[1],
    }
    if fn_switch is not None:
        fields[8] = fn_switch
    if sleep_time is not None:
        fields[9] = sleep_time
    if response_level is not None:
        fields[11] = response_level
    return _dongle(fields)
