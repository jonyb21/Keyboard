# AULA F75 Max — project log

What was done, why, and what is proven. Session date: 2026-08-10.
Hardware: AULA F75 Max Gasket, wired USB `0C45:800A`, 2.4G dongle `05AC:024F`.

---

## 1. The original problem

The keyboard would not connect to its configurator app. Three AULA apps were
installed on the machine; none of them worked.

Root cause, established by reading each app's own config files and matching
them against the live USB device:

| Installed app | Binds to | Verdict |
|---|---|---|
| AULA F75 v2.0 | `258A:010C` (SinoWealth) | Wrong keyboard entirely |
| AULA F75MAX ISO v2.0.0.2 | `0C45:80B1` | Wrong hardware revision |
| **AULA F75 Max Gasket v1.0.0.5** | **`0C45:800A`** | **Correct** |

The board is the F75 **Max**, not the plain F75. The plain-F75 app scans for a
different chip vendor and can never see it. The two wrong apps were uninstalled;
the correct one was launched and confirmed bound to the board.

Also ruled out, with evidence, so nobody retries them:

- **Epomaker web hub** (`hub.epomaker.com`) — pulled its full 38-model support
  list. No F75 of any kind.
- **AULA HUB web driver** (`hub.aulacn.com`) — supports F75 **HE** (magnetic)
  and F87/F108 PRO V2 only. Not the Max.

Note: all three vendor apps ship an executable named `DeviceDriver.exe`, so
uninstalling one kills a running instance of another.

---

## 2. Research: what this hardware actually is

Two background agents ran a read-only investigation. Full reports:
`docs/report-vendor-app-analysis.md` and `docs/report-sonixqmk-feasibility.md`.

**Silicon**: SONiX (VID `0x0C45`), almost certainly **SN32F290** (Cortex-M0,
256 KB flash) plus a 16 MB SPI flash holding the TFT screen assets. Identified
via an F108 Pro teardown sharing the same `800A` platform PID. This is *not*
the STM32 or SinoWealth part that search results claim for the plain F75.

**Stock firmware ceiling** (from the vendor app's own data files):

| Capability | Stock support |
|---|---|
| Layers | Exactly 2 (Top + Fn) |
| Tap-hold / mod-tap | None |
| Debounce | One global 5-level setting (~2–18 ms wired), not per-key |
| Key remap | All 79 keys, both layers |
| Macros | Yes, finite device storage |
| RGB | 20 preset modes, per-key static, animated custom |
| TFT screen | 128×128, ≤255 frames, 256-byte header |

**QMK verdict: feasible, but pioneering.** No SonixQMK or QMK port exists for
any AULA board. The SN32F290 board definition exists in SonixQMK but no shipped
keyboard has ever used it. Flashing would permanently kill wireless and the TFT
screen, and **no public stock firmware dump exists** for this board, so there is
nothing to restore from. An SWD dump (ST-Link) before flashing is mandatory.

**Decision: do not flash.** Without the dump tool the operation is
irreversible, and the trade (lose wireless + screen to gain layers) is bad.
The chosen path is host-side control of the stock firmware's own protocol.

---

## 3. The wire protocol (reverse-engineered, then verified)

Full contract: `contracts/hid_protocol.md`.

Established by reading three public implementations targeting this exact
platform (`mastercoder26/Aula-F75-Max-OSX`, `parsiya/f108-pro`,
`AuRoN89/AULA-F108-PRO-Reverse-Engineering`), then confirmed on the real board.

Key facts:

- **Transport**: 64-byte HID **feature reports** on the `0xFF13` vendor
  collection, paced **35 ms** apart, each step requiring an ACK readback.
- **The vendor's own `config.xml` is wrong.** It claims the config interface is
  `MI_00`. The real one is `MI_03` / `0xFF13`. Two independent implementations
  found the same thing; live discovery on this machine confirmed it.
- **Trailer byte order is `AA 55`**, not `55 AA`. One source's code contradicted
  its own docs; the F75-specific source settled it. Getting this wrong makes the
  firmware silently ignore commands.
- **Write-only protocol**: there is no "read current settings" command. The host
  must track state.
- Dongle uses a different channel: 32-byte output reports on `0xFF60` with an
  8-bit additive checksum. Battery is **dongle-only** — no wired battery opcode
  exists in any implementation.

### Live hardware verification

- **Lighting**: full transaction (begin → init → payload → apply → finalize)
  sent to the board, Breath/red applied and **visually confirmed**, factory
  Rolling restored. All ACKs positive. This confirms both the `AA 55` trailer
  and the `MI_03`/`0xFF13` path on this exact model — a first for this board.
- **Remap**: 17-key map applied, all ACKs positive.

---

## 4. What was built

Services-first layout. Each service has its own contract, tests, and README.

### `services/hid` — Python protocol client

- `protocol.py` — pure builders/parsers, zero I/O (enforced by a test). Every
  opcode carries a citation comment naming the source repo and file.
- `device.py` — endpoint discovery (wired config, wired screen, dongle).
- `client.py` — paced transaction machinery with ACK checking.
  `HidapiTransport` is the **only** code path that opens hardware.
- `remap.py` — mapping model resolving human names against the layout truth
  table.
- `cli.py` — `status`, `battery`, `light`, `remap show|apply|reset`.
- `data/f75max_layout.json` — the 80-key ground truth (key_index, HID usage,
  row#col), parsed deterministically from the vendor app's
  `layouts/rgb-keyboard.xml`.

**Safety guard**: the protocol layer refuses to remap key_index 96 (Fn). It is
the hardware layer key; remapping it away would be unrecoverable from software.

**101 gate tests**, stdlib `unittest`, no network, no device, fake clock.

### `services/hostlayer` — AHK v2 layer engine

Gives the board what its firmware cannot: **tap-hold** and **unlimited extra
layers**, driven entirely from `layers.json`. Same pattern as the existing
CIDOO runtime, so both engines run side by side.

- `core.ahk` — pure deterministic core (config parsing, mapping resolution,
  tap-hold state machine).
- `engine.ahk` — hotkey registration, threshold timers, tray menu
  (Pause / Reload / Exit), `/validate` CLI mode, single-instance mutex.
- Shared **tap-hold decision table** (rows TH-01..TH-14) documented in
  `contracts/hostlayer_config.md` and asserted by both test suites.

**27 mirror tests** in Python (the AHK suite cannot self-execute in CI, so the
state machine is mirrored and executed); `engine.ahk /validate` passes against
real AutoHotkey v2.

Honest limitation, documented not hidden: AHK hooks are **global**. Mappings
apply to every attached keyboard, not just this one. Per-device filtering would
need Interception/AHI drivers.

---

## 5. The board's current configuration

Canonical file: `services/hid/data/board_map.json`. Applied live, all ACKs
positive. **The board stores this in its own memory, so it types correctly on
every PC with no software installed.**

### Keycap truth fixes

The keycaps did not match what the keys emitted. Corrected in firmware:

| Physical position | Now sends |
|---|---|
| Backtick (top-left) | PrintScreen |
| Win_L | Alt_L |
| Alt_L | Win_L |
| Alt_R | Ctrl_R |
| PageUp | Keypad `*` |
| PageDown | Keypad `/` |
| End | Keypad `-` |

Note: F7/F9 were also mismatched. Rather than remap them, the **keycaps were
physically swapped**, so no firmware entry exists for them.

### Repurposed F-row

F1, F3, F5–F12 were unused. They now emit **F13–F22** (`0x68`–`0x71`) — codes
that exist in USB HID but that no physical keyboard produces. This gives them
unique identities the host layer can bind to anything without ever colliding
with the laptop keyboard. F2, F4, and Esc are untouched.

| Cap | Emits | Tap | Hold |
|---|---|---|---|
| F1 | F13 | Play/Pause | Next track |
| F3 | F14 | Snip (`Win+Shift+S`) | Clipboard (`Win+V`) |
| F5 | F15 | Explorer | Run dialog |
| F6 | F16 | Task view | Show desktop |
| F7 | F17 | Task Manager | `Win+X` menu |
| F8 | F18 | Mute | Previous track |
| F9 | F19 | *toggles the num layer* | |
| F10 | F20 | Snap left | Snap right |
| F11 | F21 | Maximize | Minimize |
| F12 | F22 | Emoji picker | Emoji picker |

Plus, from the host layer: **CapsLock** tap = Esc / hold = Ctrl, and
**hold-Space** activates a nav layer (ijkl arrows, Home/End/PgUp/PgDn, word
jumps). The num layer toggle sits on F19 because the RAlt position now sends
RCtrl.

Everything above is edited in `services/hostlayer/layers.json` and reloaded
with `Ctrl+Alt+R`. No firmware changes needed for feature changes.

**Full undo of the firmware map**: `remap reset` returns every key to factory.

---

## 6. Setting this up on another PC

The board's remap is in its own memory — it types correctly immediately with
nothing installed. The steps below only add the F13–F22 features and lighting
control.

1. `git clone <this repo>`
2. Install [AutoHotkey v2](https://www.autohotkey.com/).
3. `py -m venv .venv` then `.venv\Scripts\pip install hidapi` (needed only for
   lighting/remap commands, not for the layer engine).
4. Copy `services/hostlayer/layers.example.json` to
   `services/hostlayer/layers.json` and edit to taste.
5. Run: `AutoHotkey64.exe services\hostlayer\engine.ahk`
6. Optional, only if the board was ever reset:
   `py -m services.hid.cli remap apply services/hid/data/board_map.json`

---

## 7. Known gaps

- **Wired battery**: impossible. No wired battery opcode exists in any
  implementation; battery is a dongle-only feature.
- **Per-key RGB ordering**: the `light_index` map for the 79-key matrix is not
  verified (the F108's 144-slot table does not transfer directly).
- **TFT screen upload**: protocol documented (256-byte header, ≤255 frames of
  128×128) but not implemented; wire pixel format unconfirmed.
- **Remap is wired-only**: the wireless remap sender is undocumented.
- **Fn-layer remap**: exposed in the library, not the CLI; the vendor's
  `fnlayer_disable` flags were not extracted.
- Macro wire format, polling rate, and knob configuration: no trace in any
  source.

---

## 8. Test status

| Suite | Count | Command |
|---|---|---|
| `services/hid` | 101 | `py -m unittest discover -s services/hid/tests -t .` |
| `services/hostlayer` | 27 | `py -m unittest discover -s services/hostlayer/tests` |
| AHK config validation | — | `AutoHotkey64.exe engine.ahk /validate layers.json` |

All green as of the final commit.
