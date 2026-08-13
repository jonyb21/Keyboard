# AULA F75 Max — HID wire protocol contract (v1.3)

Status: the wired config endpoint, stock lighting flow, and normal-layer remap
flow are verified on Jon's exact `0C45:800A REV_0108` board. The 9-page screen
test-pattern plus Aurora and Focus Core custom-image transfers are verified
locally, as is the corrected clock transaction. Displayed-pixel correctness,
restore timing, and direct clock-display observation still await visual proof.
Wireless, per-key RGB, FN-layer remapping, and features explicitly marked
UNKNOWN remain unverified locally. Every
opcode below is traceable to a cited source and the live runs are logged in
section 11.

Sources (fetched 2026-08-10, read at source level):

- **[OSX]** github.com/mastercoder26/Aula-F75-Max-OSX — targets exactly this
  hardware (wired 0C45:800A, dongle 05AC:024F). Files cited:
  `Sources/AulaF75Bar/main.m`, `Sources/F75Probe/main.m`.
- **[F108]** github.com/parsiya/f108-pro — same 800A Sonix platform (F108 Pro).
  Files cited: `ai-docs/hid-protocol.md`, `pkg/aula/device.go`,
  `pkg/aula/lighting.go`, `pkg/aula/transport_windows.go`.
- **[F108RE]** github.com/AuRoN89/AULA-F108-PRO-Reverse-Engineering — hardware
  notes (SN32F290 MCU + SPI flash). Used for context only; its tools cover the
  bootloader/SPI-flash path, not the runtime config protocol.
- **[VENDOR]** exact matching Windows app v1.0.0.5 on Jon's machine — read-only
  analysis of `config.xml`, `layouts/rgb-keyboard.xml`, registry interface map,
  language resources, and `DeviceDriver.exe`. Package identity and target are
  rechecked by `services/device_audit` before configuration changes.

Conflict rule applied per task: where sources disagree, the F75-Max-specific
source [OSX] wins; conflicts are listed in section 8.

---

## 1. Identity and interface selection

| Mode | VID:PID | Config channel | Evidence |
|---|---|---|---|
| USB wired | 0C45:800A | HID collection usage page **0xFF13**, usage 0x0001 — 64-byte **feature** reports | [OSX] main.m `OpenScreenHID` matches `HIDMatchDictionary(kWiredVendorID, kWiredProductID, 0xff13)`; [F108] transport_windows.go `findHIDDevice(VendorID, ProductID, 0xFF13)` |
| USB wired (screen data) | 0C45:800A | HID collection usage page **0xFF68** — 4096-byte **output** reports, 64-byte input acks | [OSX] main.m screenPipe match 0xff68; [F108] hid-protocol.md interface table |
| 2.4G dongle | 05AC:024F | HID collection usage page **0xFF60**, usage 0x61 — 32-byte **output** reports, input reports for replies | [OSX] main.m `IsDongleEndpoint` + `HIDMatchDictionary(kDongleVendorID, kDongleProductID, 0xff60)`, endpoint check `usagePage == 0xff60 && usage == 0x61` |

Interface-number fallback (only when the HID backend does not expose usage
pages, e.g. Linux hidraw) is limited to wired config MI_03 and wired screen
MI_02. Dongle MI_03 alone is not enough identity: live dongle paths require
the exact 05AC:024F, usage page 0xFF60, usage 0x61 collection.

The wired VID/PID is also used by a related AULA platform. Before opening a
wired mutation transport, `services/hid` additionally requires the live HID
product string `AULA F75Max` and release number `0x0108`. Missing or mismatched
identity fails closed; enumeration alone never authorizes a write. Battery,
lighting, clock, and remap live paths each require exactly one matching config
candidate. Duplicate candidates fail before a handle opens.

Screen upload has a stricter two-handle boundary: enumeration must contain
exactly one exact MI_03 control endpoint and exactly one exact MI_02 screen
endpoint. Duplicates, missing endpoints, wrong interfaces, mismatched product
or release, and mismatched backend serial/pair tokens fail before either handle
opens.

Note: the vendor config.xml binds the wired app to `MI_00`, but both public
implementations independently found that the actual feature-report collection
is the 0xFF13 vendor collection (USB interface 3). [F108] hid-protocol.md:
"The Windows software uses MI_00 in its HID interface filter string, but on
the USB level, the feature report descriptor is on interface 3." We follow the
implementations.

## 2. Pacing

35 ms between successive commands (sends and readbacks). Source: [VENDOR]
`<cmd_delaytime value="35"/>`; [F108] device.go `cmdDelay = 35 *
time.Millisecond` applied after every SetFeature and GetFeature.

## 3. Framing

### 3.1 Wired feature reports (usage page 0xFF13)

- 64 bytes on the wire; the collection declares no report ID. With Windows
  HID APIs / hidapi, prepend a 0x00 report-ID byte (65-byte buffer).
  Source: [F108] hid-protocol.md "Packet Format", transport_windows.go.
- A feature send succeeds only when hidapi returns integer `65`. Boolean,
  missing, negative, zero, short, and overlong return values are failures.
- Commands that are marked readback below REQUIRE a GET_REPORT (feature) after
  the SET; without it the firmware ignores subsequent commands. The response
  echoes bytes 0-1 and has byte[3] = 0x01 as ACK. Source: [F108]
  hid-protocol.md "Read-back".
- Trailer: many data payloads end with the marker bytes `AA 55` (uint16
  0x55AA little-endian) at a command-specific offset. Wire order confirmed by
  USB capture in [F108] hid-protocol.md ("the Windows software sends bytes
  AA 55, not 55 AA; getting this wrong causes the keyboard to silently ignore
  the command") and matched by [OSX] (`timeCommand[62]=0xaa; timeCommand[63]=0x55`).
- The protocol is write-only: there is no "read current settings" command.
  Host tracks state. Source: [F108] hid-protocol.md "No State Query Commands".

### 3.2 Dongle output reports (usage page 0xFF60)

- 32 bytes on the wire, no report ID (prepend 0x00 for hidapi write()).
- A dongle output succeeds only when hidapi returns integer `33` for that
  report-ID-prefixed buffer. The same exact-count rule applies to the LCD pipe:
  4096-byte page plus report ID must return integer `4097`.
- Byte 31 is an 8-bit additive checksum: sum of bytes 0..31 with byte 31
  zeroed. Source: [OSX] F75Probe/main.m `applyRawChecksum`.
- Trailer `AA 55` at offsets 17-18 for command packets that carry parameters.
  Source: [OSX] F75Probe/main.m builders.
- Replies arrive as input reports on the same collection.

## 4. Battery (dongle only)

Request — 32-byte output report [OSX] AulaF75Bar/main.m `BatteryFromAulaRawHID`:

```
offset 0: 0x20
offset 1: 0x01
offset 2..30: 0x00
offset 31: checksum (= 0x21 for this fixed request)
```

Response — input report on the same collection, accepted when
`report[0]==0x20 && report[1]==0x01 && 1 <= report[3] <= 100`:

```
offset 0: 0x20   echo
offset 1: 0x01   echo
offset 2: UNKNOWN (observed field, meaning not established; possibly charging flag)
offset 3: battery percent 1..100
```

[OSX] polls with a ~1.25 s response deadline.

Wired battery query: UNKNOWN — no wired battery opcode exists in any source
(wired mode implies charging; the vendor app shows battery for wireless only).

## 5. Lighting

### 5.1 Wired (feature reports, 0xFF13) — sequence per [F108] hid-protocol.md "Lighting Protocol", device.go, lighting.go

| Step | Payload bytes | Readback |
|---|---|---|
| 1 begin | `04 18` + zero padding | yes |
| 2 lighting init | `04 13`, byte[8]=`01` | yes |
| 3 data | layout below | no |
| 4 apply | `04 02` | yes |
| 5 finalize | `04 F0` | no |

Step 3 data layout (64-byte payload):

```
offset 0     mode        0=off, 1..19 = effect ID (table below)
offset 1     R           0-255   \
offset 2     G           0-255    | only when mode != 0
offset 3     B           0-255   /
offset 4-7   0x00
offset 8     colorful    0=single color, 1=rainbow   (mode != 0)
offset 9     brightness  0-5                          (mode != 0)
offset 10    speed       0-5                          (mode != 0)
offset 11    direction   0-1                          (mode != 0)
offset 12-13 0x00
offset 14-15 trailer     AA 55   (see conflict C1)
offset 16-63 0x00
```

### 5.2 Dongle (32-byte output report, 0xFF60) — [OSX] F75Probe/main.m `buildWirelessRGBLEDModeReportVariant`

```
offset 0     0x05
offset 1     0x10
offset 2     0x00
offset 3     mode        0=off, 1..19
offset 4     R          \
offset 5     G           | only when mode != 0
offset 6     B          /
offset 7-10  0x00
offset 11    colorful    (mode != 0)
offset 12    brightness  0-5 (mode != 0)
offset 13    speed       0-5 (mode != 0)
offset 14    direction   0-1 (mode != 0)
offset 15-16 0x00
offset 17-18 trailer AA 55
offset 19-30 0x00
offset 31    checksum
```

Optional "commit" packet (byte[0]=0x0F, rest zero, checksum at 31) exists in
[OSX] F75Probe (`buildWirelessRGBCommitReport`); it is probed there, not
required by the shipped menu-bar flow. We expose it but do not send by default.

The same single-packet layout matches [F108] hid-protocol.md's wireless
`05 10` packet (offsets shifted by one because that doc includes a leading
unused byte).

### 5.3 Effect mode IDs (both transports) — [F108] ai-docs/hid-protocol.md + [VENDOR] 1033.lan strings 521-540

0 Off, 1 Static, 2 SingleOn, 3 SingleOff, 4 Glittering, 5 Falling,
6 Colourful, 7 Breath, 8 Spectrum, 9 Outward, 10 Scrolling, 11 Rolling
(factory default), 12 Rotating, 13 Explode, 14 Launch, 15 Ripples,
16 Flowing, 17 Pulsating, 18 Tilt, 19 Shuttle.

Brightness 0-5, speed 0-5 ([VENDOR] `brightness_max`/`speed_max`).

### 5.4 Named profiles and per-key safety boundary

`Focus Core` and `Aurora` each have a deployable stock-engine layer and an
experimental per-key text layer. Focus Core resolves to Static `168BFF`,
brightness 2; Aurora resolves to Flowing `7D42FF`, brightness 4, colorful.
Both deploy through the verified section 5.1 transaction.

The per-key compiler uses the exact vendor `light_index` values in
`data/f75max_layout.json` and emits a deterministic 576-byte data table for
inspection. Live apply always returns `capture_required`. The only available
wire reference for per-key init is the F108-only `04 23`; it must not be sent
to this F75 Max until a live exact-model capture establishes command, framing,
trailer, and ACK behavior.

## 6. Additional established commands

### 6.1 Dongle function settings / game mode — [OSX] F75Probe/main.m `buildWirelessKeyResponseReportVariant`, `buildWirelessGameModeReportVariant`

32-byte output report:

```
offset 0   0x07
offset 1   0x10
offset 4   0x01
offset 5   include-fn-switch flag (0/1)
offset 6   include-sleep-time flag (0/1)
offset 7   include-response-level flag (0/1)
offset 8   fn_switch   0=momentary, 1=toggle      (if flag 5)
offset 9   sleep_time  0-3 (off/1/5/30 min)       (if flag 6)
offset 11  response_level 1-5                     (if flag 7)
offset 12  game_mode 0/1
offset 13  disable_alt_tab 0/1
offset 14  disable_alt_f4 0/1
offset 15  disable_win 0/1
offset 17-18 trailer AA 55
offset 31  checksum
```

### 6.2 Wired LCD clock sync — [OSX] AulaF75Bar/main.m (verified working there), [F108] hid-protocol.md "LCD Clock/DateTime Sync"

Sequence: `04 18` (strict status ACK) -> `04 28` with byte[8]=0x01 (strict
status ACK) -> data (exact 64-byte payload echo) -> `04 02` (strict status
ACK). Data payload:

```
offset 0  0x00
offset 1  0x01 (profile)
offset 2  0x5A (magic)
offset 3  year - 2000
offset 4  month 1-12
offset 5  day 1-31
offset 6  hour 0-23
offset 7  minute 0-59
offset 8  second 0-59
offset 9  0x00
offset 10 weekday 0=Sunday..6=Saturday
offset 62-63 trailer AA 55
```

The clock-data response is not the generic wired ACK. On Jon's exact board it
echoes the full `00 01` command payload, so byte 3 is `year - 2000`, not a
status byte. The client requires exact length and byte-for-byte equality for
that response; malformed, truncated, extended, or non-echo data is rejected.
Begin, clock select, and apply continue to require the generic command echo
plus byte-3 `01` status ACK.

### 6.3 Wired screen upload (test-pattern and custom-image transport verified Jon-local; visual rendering pending)

[OSX] AulaF75Bar/main.m `UploadScreenStream` (verified working there on the
F75 Max 128x128 screen):

1. Feature `04 18` on MI_03/0xFF13; require ACK, then wait 200 ms.
2. Feature `04 72`, byte[2]=0x01 (image slot), bytes[8-9]=chunk count
   (uint16 LE) on MI_03; require ACK, then wait 50 ms.
3. Before each page, drain stale MI_02 input with at most 32 positive 1 ms
   polls. In hidapi 0.15, timeout 0 means no timeout and is forbidden here.
   Send exactly one 4096-byte output report on MI_02/0xFF68. Never send a page
   as a feature report.
4. Require a fresh MI_02 input report of at least 3 bytes within 350 ms, then
   wait 5 ms. The Jon-local exact-board prefix is pinned as `01 5A 02`; every
   page must match it. Normal upload/restore is disabled if the prefix is
   intentionally unset. Only the explicit live test-pattern flow may learn a
   prefix in that pre-pin state. Return all observed prefixes as evidence.
5. After a fully sent begin, success or any later failure waits 100 ms and
   attempts feature `04 02` plus ACK on MI_03. A begin ACK failure is uncertain:
   the firmware may be active, so cleanup is attempted. If the begin send
   itself definitively fails, no `04 02` is sent. Screen upload never sends
   `04 F0`.

Stream format ([OSX], F75-specific): 256-byte header (byte[0]=frame count,
bytes[1..N]=per-frame delay, units ~2 ms) + frames of 128x128 RGB565
little-endian (32768 bytes/frame), zero-padded to a 4096 multiple. The F108
uses the same structure at 240x135. [VENDOR] `gif_headlength="256"` agrees.
PNG, JPEG, GIF, BMP, TIFF, and WebP decode through lazy-loaded Pillow.
Contain, cover, and stretch fits produce a black-backed 128x128 RGB frame.
Stills use delay byte 255; animations convert milliseconds to 2 ms units with
positive half-up rounding and clamp to 1..255. Missing or nonpositive animated
durations use the exact-source 10 ms fallback, wire byte 5. Frame count is
1..255. Before any transfer, the client verifies N=1..255, nonzero N delay
bytes, zero remaining header bytes, exactly `1 + 8*N` pages (maximum 2041),
and zero tail padding.

Default stock restore does not use the installed `0.gif` because its GIF GCE
timing is zero/ambiguous. It uses the preserved 251 numbered PNG frames and
requires an explicit candidate delay. Before discovery, the committed text
manifest must match the 128x128 dimensions, 251-frame count, source hash,
prepared-stream hash, 2009-page count, and chosen candidate delay. Byte 10 is
recorded only as a candidate; DB-to-wire timing has not been established. A
live restore requires `--allow-unverified-timing`. An explicit source bypasses
the default manifest; an explicit GIF override is allowed with a warning.

### 6.4 Wired key remap (`04 11` normal layer / `04 27` FN layer) — implemented in services/hid (remap.py)

Hardware-verified on the F108 Pro by [F108] (`aula.exe remap`,
ai-docs/key-remap-protocol.md "Status: Verified working on hardware");
F75-unverified fields are marked below. Same 0C45:800A Sonix platform.

Sequence per [F108] pkg/aula/remap.go `sendRemapTable`:

| Step | Payload | Readback |
|---|---|---|
| 1 begin | `04 18` | yes |
| 2 remap init | `04 11` (normal) or `04 27` (FN layer), byte[8]=`09` | yes |
| 3 data | 576-byte table as 9 x 64-byte feature reports | no (see D1) |
| 4 apply | `04 02` | yes |
| 5 finalize | `04 F0` | yes (see D1) |

**D1 — [F108] doc/code discrepancy, code adopted.** The repo's
key-remap-protocol.md claims a readback after the last data packet and lists
finalize readback "Yes"; the hardware-verified code path
(`remap.go sendRemapTable`) passes `readback=false` to `sendMultiPacket`
(no data readback) and `readback=true` for `04 F0` (finalize readback —
unlike the lighting flow, whose `finalizeTransaction` skips it). We follow
the code.

Remap table (576 bytes = 0x240), [F108] remap.go `remapBufSize` +
ai-docs/key-remap-protocol.md "Remap Data Buffer":

```
offset key_index*4     4-byte slot [action, param1, param2, param3]
offset 0x000           slot 0, unused, always zero
offset 0x23E-0x23F     trailer AA 55 (uint16 0x55AA little-endian)
```

Valid slots are key_index 1..142 (remap.go skips slot 0 and any slot
overlapping the trailer). A slot of `00 00 00 00` means passthrough; an
all-zero table clears every remap (remap.go `ResetKeyRemap`). The whole
table is always sent.

Slot action types ([F108] ai-docs/key-remap-protocol.md "Action Types"):

| Action | Meaning | Params |
|---|---|---|
| `00` | passthrough | - |
| `01` | special function (lock keys, Alt+Tab, ...) | sub-code table in source |
| `02` | key combination | param1 = HID modifier bitmask (01=LCtrl .. 80=RWin), param2 = HID usage, param3 = 0 |
| `03` | consumer control (multimedia) | param1 = consumer usage (page 0x0C) |
| `05` | profile switch | param1=0x02, param2=sub-value |
| `06` | macro execution | param1 = macro index, param2 = loop count |
| `07` | mouse function | param1-3 = button/scroll params |

Action `02` details ([F108] remap.go `NewKeySwap`, Ghidra FUN_00451b90):
a modifier target (usage 0xE0-0xE7) is encoded as its bitmask in param1
with param2 = 0 (`0xE0`->`0x01`, `0xE1`->`0x02`, `0xE2`->`0x04`,
`0xE3`->`0x08`, `0xE4`->`0x10`, `0xE5`->`0x20`, `0xE6`->`0x40`,
`0xE7`->`0x80`); a normal key target goes in param2 with param1 = 0.
Worked example from the [F108] doc: CapsLock (key_index 55) -> Left Ctrl =
`02 01 00 00` at offset 220.

**key_index space is model-specific.** The slot index comes from the model's
`rgb-keyboard.xml` key table ([F108] ai-docs/key-remap-protocol.md "Slot
Index", built at runtime by Ghidra FUN_0041cd80). For the F75 Max OUR ground
truth is `services/hid/data/f75max_layout.json`, parsed from
`layouts/rgb-keyboard.xml` of the vendor app v1.0.0.5: 80 keys, indices
1..121 (with gaps), matching the F108's indices where the boards overlap
(F7=8, CapsLock=55, Backspace=103, PageUp=118). key_index 96 is the Fn key
(vendor pseudo-usage 0xAF): it selects the firmware FN layer and
services/hid refuses to remap it.

F75-unverified fields (honest gaps):

- Table size 0x240 / 144 slots taken from the F108; the F75's indices
  (max 121) fit, but the F75 firmware's accepted table length is unverified.
- FN-layer (`04 27`) semantics: the vendor app skips keys flagged
  `fnlayer_disable` in rgb-keyboard.xml; the F75's flag values were not
  extracted, so services/hid sends the FN layer table unfiltered.
- Action types `01`/`03`/`05`/`06`/`07` are documented above but only
  action `02` (key) is exposed by services/hid v1.1.
- Wireless remap: the vendor app uses a separate sender for 2.4G mode
  ([F108] key-remap-protocol.md, FUN_004191c0) that no source documents.
  Remap in services/hid is wired-only.

## 7. Full opcode table

| Bytes 0-1 | Transport | Meaning | Source | Verified on F75 Max hardware |
|---|---|---|---|---|
| `04 18` | wired feature | begin transaction | [F108] doc+code, [OSX] | by [OSX] (screen/clock flows) |
| `04 02` | wired feature | apply / commit | [F108], [OSX] | by [OSX] |
| `04 F0` | wired feature | finalize | [F108] | yes, normal-layer remap |
| `04 13` (byte8=01) | wired feature | lighting init | [F108] | yes, 2026-08-10 and 2026-08-12 |
| lighting data | wired feature | section 5.1 | [F108] | yes, 2026-08-10 and 2026-08-12 |
| `04 23` | wired feature | per-key RGB init (03=mono, 09=RGB) | [F108] | no — live path capture-gated |
| `04 11` / `04 27` | wired feature | remap normal / FN layer (section 6.4) | [F108] code+doc, verified there | `04 11` yes; `04 27` no |
| `04 17` + `00 01` | wired feature | function settings (wired) | [F108] | no — not implemented |
| `04 72` | wired feature | screen upload header | [F108], [OSX] | implemented; by [OSX] and Jon-local test-pattern/Aurora/Focus Core transfers |
| `04 28` | wired feature | clock sync init | [F108], [OSX] | completed Jon-local 2026-08-12; direct display observation pending |
| `04 19` / `04 15` | wired feature | macro init / data | [F108] | no — not implemented |
| `20 01` | dongle output | battery request | [OSX] | by [OSX] |
| `05 10` | dongle output | lighting all-in-one | [OSX], [F108] | by [OSX] |
| `05 01` | dongle output | legacy sidelight-style mode probe (mode+0x1F) | [OSX] F75Probe | probe only |
| `07 10` | dongle output | function settings / game mode | [OSX] | by [OSX] |
| `0F` | dongle output | commit (probe) | [OSX] F75Probe | probe only |

## 8. Conflicts between sources

- **C1 — trailer byte order.** [F108] `pkg/aula/lighting.go`, `perkey.go`,
  `clock.go` write `55 AA` on the wire, contradicting the same repo's
  `ai-docs/hid-protocol.md` (USB capture says `AA 55`) and its own
  `remap.go` (writes `AA 55`). [OSX] — the F75-Max-specific source, verified
  working on this hardware — writes `AA 55` everywhere. **Adopted: `AA 55`.**
- **C2 — wired config interface.** [VENDOR] config.xml says MI_00; [OSX] and
  [F108] both target the 0xFF13 vendor collection (USB interface 3).
  **Adopted: 0xFF13.**
- **C3 — screen header filler.** [F108] fills header bytes after the delay
  table with 0xFF; [OSX] zero-fills. F75-specific source wins: 0x00.
- **C4 — dongle protocol shape.** [F108] describes the wireless `05 10`
  packet as a 64-byte payload with a leading unused byte; [OSX] sends 32-byte
  reports with a checksum at byte 31. Layouts agree field-for-field after the
  one-byte shift. **Adopted: [OSX] 32-byte + checksum** (F75-specific, and the
  dongle's 0xFF60 collection takes 32-byte output reports).

## 9. UNKNOWN / not established from sources

- Wired battery query opcode (none exists in any source).
- Meaning of battery response byte 2 (possibly charging state).
- Bytes 4-5 of the `04 02` apply response ("may indicate mode/state", [F108]).
- Exact-F75 per-key RGB wire transaction (`04 23` is F108-only). The F75
  `light_index` ordering itself is established from the exact vendor XML:
  80 unique physical keys, indices 1..121 with gaps, in a 144-slot candidate
  table.
- Restore frame timing. Candidate wire delay byte 10 comes from the vendor
  profile database, but its DB-to-wire unit conversion needs live visual proof.
- Macro storage size / wire format details beyond `04 19`/`04 15` headers.
- FN-layer `fnlayer_disable` flag values and the wireless remap sender
  (section 6.4 gap list). The normal-layer table length is verified locally.
- Whether the wired lighting flow also works over the dongle's MI_03 feature
  path (the vendor app binds dongle config to MI_03; [OSX] uses the 0xFF60
  output-report path instead).
- Polling-rate and knob configuration commands (no trace in any source).

## 10. Changelog

- **v1.3 (2026-08-12)** — Added exact-pair LCD preparation/upload/restore and
  clock CLI, screen ACK trace evidence, generated local LCD assets, deployable
  Focus Core/Aurora stock presets, exact `light_index` data, and a per-key
  compiler whose live path is capture-gated. Added unique config selectors,
  malformed-stream rejection, test-pattern-only ACK learning, and manifest-
  locked restore with explicit unverified-timing consent. Pinned the Jon-local
  LCD page ACK prefix `01 5A 02` after a successful 9-page live test-pattern.
  Corrected clock-data readback to require the exact 64-byte `00 01` payload
  echo observed live while keeping begin/select/apply status ACKs strict.
  Verified corrected clock sync, two named 9-page custom-image transfers, and
  both named stock-lighting presets live; Focus Core brightness 2 is final.
  Hardened all hidapi write boundaries to exact 65/33/4097-byte return counts
  and stopped LCD apply cleanup after a definite begin-send failure while
  preserving conservative cleanup after an uncertain begin ACK.
- **v1.2 (2026-08-12)** — Recorded live normal-layer remap and second lighting
  verification on `0C45:800A REV_0108`; narrowed the remaining remap gaps to
  FN-layer and wireless behavior.
- **v1.1 (2026-08-10)** — Added section 6.4: wired key remap transaction
  (`04 11`/`04 27`, 576-byte slot table, action types, Fn guard, D1
  doc/code discrepancy ruling). Referenced the F75 key_index ground truth
  `services/hid/data/f75max_layout.json`. Updated opcode table and
  section 9 gaps. Sources: [F108] pkg/aula/remap.go +
  ai-docs/key-remap-protocol.md; [F108RE] checked, no remap coverage.
- **v1 (2026-08-10)** — Initial contract: identity/interfaces, pacing,
  framing, battery, lighting, function settings, clock, screen upload,
  opcode table, conflicts C1-C4.

## 11. Hardware verification log

- 2026-08-10, AULA F75 Max (0C45:800A REV_0108), wired, Windows 11:
  full wired lighting transaction (begin, init, payload, apply, finalize)
  verified live via `services.hid.cli light` — Breath/FF0000 applied and
  visually confirmed, factory Rolling restored, all ACK readbacks positive.
  Confirms trailer order AA 55 (conflict C1) and the MI_03/0xFF13 wired
  config path (conflict C2) on this exact board. Vendor DeviceDriver.exe
  was running concurrently; no interference observed.
- 2026-08-12, same board and transport: `services.hid.cli light` applied
  Static/D9E8FF at brightness 2 and speed 0. The full transaction completed
  with positive ACKs. `services.hid.cli remap apply` then applied the canonical
  17-key normal-layer table, including collision-free F13-F15/F18-F24 tokens;
  begin, init, apply, and finalize readbacks all acknowledged.
- 2026-08-12 pre-live review incident: the exact command
  `.\.venv\Scripts\python.exe -m services.hid.cli light --mode 1 --transport wired`
  was accidentally run once. It exited 0 and applied Static `FFFFFF`,
  brightness 5, speed 3. It opened only the wired lighting config path; no LCD,
  clock, or remap command ran. Focus Core brightness 2 was restored later in
  the coordinator's controlled live sequence.
- 2026-08-12 first controlled LCD test-pattern attempt: the transaction blocked
  before page 1 because stale-input drain called hidapi `read(64, 0)`. In
  hidapi 0.15, timeout 0 means no timeout, so the shell timed out and left PIDs
  34212 and 33032 running until the coordinator terminated them. No page output
  or page ACK was captured, and `SCREEN_ACK_PREFIX` remained unset after that
  attempt. The client
  now rejects nonpositive real-transport reads and uses at most 32 one-
  millisecond stale polls; gate and policy evals cover the failure path.
- 2026-08-12 controlled LCD retry on the same exact board: all 9 test-pattern
  pages returned the stable ACK prefix `01 5A 02`. The prepared stream SHA256
  was `d4e94333d863cdfdd7f08deae09f5eb8b9d0011375150de8a8b5cded89f342bf`,
  and the watchdog exited 0. `SCREEN_ACK_PREFIX` is pinned to those three bytes;
  normal upload and restore now require that exact prefix.
- 2026-08-12 first controlled clock-sync attempt on the same exact board:
  begin and `04 28` select passed their strict ACK checks. The `00 01` data
  readback was `00 01 5A 1A 08 0C 16 17 ...`, echoing the sent date/time
  payload with byte 3 equal to year `0x1A` (2026). The old generic validator
  falsely required byte 3 to be status `01`, raised `AckError`, and stopped
  before `04 02` apply. No completed clock sync or visual result is claimed.
  The client now validates an exact full payload echo only for clock data;
  malformed/non-echo responses fail closed and the three control ACKs remain
  strict. A controlled live retry and direct LCD observation were still needed
  at that point.
- 2026-08-12 22:28:36, controlled clock retry on the same exact board: the
  corrected transaction completed through strict `04 02` apply. This verifies
  the clock-data echo rule and all four transaction steps locally. Direct LCD
  clock observation is not claimed.
- 2026-08-12 custom-image and lighting sequence on the same exact board: the
  first Aurora upload reached final apply, whose readback began `FF`; the client
  failed closed and did not report success. The Aurora retry then completed all
  9 pages with prefix `01 5A 02`, stream SHA256
  `5e9aed91787c759433597eb59c81a5f334143512e2d42129b2f3a0723c92da3f`.
  Focus Core completed all 9 pages with the same prefix, stream SHA256
  `283c0a20fc56b9f56d6de1287a2a4097cf2de86d949f9edf659f9179ad5aa622`.
  These runs verify custom-image transfer and strict final-apply handling, not
  displayed-pixel correctness. Aurora stock lighting was applied live, followed
  by Focus Core; Focus Core Static `168BFF`, brightness 2 is the final state.
