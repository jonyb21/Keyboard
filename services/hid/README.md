# services/hid — AULA F75 Max stock-firmware protocol client

Python client for the F75 Max's HID config protocol: device discovery,
battery read (2.4G dongle), lighting control (wired feature reports and
dongle output reports), named stock-engine effects, LCD image upload/restore,
LCD clock sync, and key remapping, with the firmware's required pacing built in.

Wire contract: [`contracts/hid_protocol.md`](../../contracts/hid_protocol.md).
Every opcode in this package carries a comment citing the public source it
was recovered from.

## Layout

| File | Role |
|---|---|
| `protocol.py` | Pure payload builders/parsers. Deterministic, zero I/O imports (test-enforced). |
| `device.py` | Endpoint discovery/classification over hidapi-style enumeration dicts. Real enumeration only via `enumerate_hid()`. |
| `client.py` | `Client` (sequences + 35 ms `Pacer`) over an injectable `Transport`. `HidapiTransport` is the ONLY code that opens hardware. |
| `screen.py` | Lazy-Pillow image decoder, fit engine, RGB565LE encoder, restore-sequence loader, and page-aligned stream metadata. |
| `custom_lighting.py` | Focus Core/Aurora stock presets plus an exact-layout per-key compiler whose live path is capture-gated. |
| `remap.py` | Mapping model: JSON mapping files -> resolved remap slots via the layout truth table. |
| `cli.py` | `status`, `battery`, `light`, `clock`, `screen`, and `remap` commands with `--dry-run`. |
| `data/f75max_layout.json` | Exact-vendor F75 `key_index` and `light_index` ground truth for 80 physical keys. |
| `data/lighting_profiles.json` | Text-only stock and per-key definitions for Focus Core and Aurora. |
| `data/lcd_assets.json`, `lcd_prompts.md` | Text-only hashes, preparation settings, restore metadata, and generation prompts. |
| `data/board_map.json` | CANONICAL current firmware map: 7 keycap-truth remaps + F1,F3,F5-F12 -> F13-F15/F18-F24 for hostlayer feature bindings. F16/F17 are reserved by the co-installed CIDOO encoder runtime and are regression-tested as absent. |
| `tests/` | Gate tests: stdlib `unittest`, no network, no device, fake clock. |

## Usage

Create the ignored project-local environment once, then use that exact
interpreter for dry runs, tests, and real hardware:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r services\hid\requirements.txt
.\.venv\Scripts\python.exe -m services.hid.cli status
.\.venv\Scripts\python.exe -m services.hid.cli battery
.\.venv\Scripts\python.exe -m services.hid.cli light --mode breath --brightness 5 --speed 3 --color FF0000
.\.venv\Scripts\python.exe -m services.hid.cli light --mode 11 --colorful --transport dongle
.\.venv\Scripts\python.exe -m services.hid.cli --dry-run clock sync
```

`status` is itself a live read-only enumeration. `--dry-run status` is rejected
instead of printing a misleading device-free result.

Modes: `0`/`off` .. `19`/`shuttle` (names in `protocol.LIGHT_MODES`;
factory default is 11 `rolling`). Brightness 0-5, speed 0-5, direction 0/1,
`--colorful` switches to the rainbow palette.

## Named lighting

Focus Core is the efficient default: verified stock Static mode, `168BFF`,
brightness 2. Aurora uses the verified stock Flowing engine with its colorful
palette. These are deployable now:

```powershell
.\.venv\Scripts\python.exe -m services.hid.cli --dry-run light preset "Focus Core" --transport wired
.\.venv\Scripts\python.exe -m services.hid.cli light preset "Focus Core" --transport wired
.\.venv\Scripts\python.exe -m services.hid.cli light preset Aurora --transport wired
```

The same profiles include per-key color layers compiled by exact
`light_index`. They are inspectable but cannot mutate the board:

```powershell
.\.venv\Scripts\python.exe -m services.hid.cli light custom "Focus Core" --show
.\.venv\Scripts\python.exe -m services.hid.cli --dry-run light custom Aurora
```

A live `light custom` call returns `capture_required`. The F108-only `04 23`
path is not enabled for the F75 Max without an exact-model capture.

## LCD images and clock

Supported inputs: PNG, JPEG, GIF, BMP, TIFF, and WebP; fits: `contain`,
`cover`, and `stretch`. `inspect`, `prepare`, and every `--dry-run` path open
no HID handle.

```powershell
.\.venv\Scripts\python.exe -m services.hid.cli screen inspect .local-assets\lcd\focus-core.png --fit cover
.\.venv\Scripts\python.exe -m services.hid.cli screen prepare .local-assets\lcd\focus-core.png --fit cover
.\.venv\Scripts\python.exe -m services.hid.cli --dry-run screen upload .local-assets\lcd\focus-core.png --fit cover
.\.venv\Scripts\python.exe -m services.hid.cli screen upload .local-assets\lcd\focus-core.png --fit cover
.\.venv\Scripts\python.exe -m services.hid.cli --dry-run screen restore --delay 10
.\.venv\Scripts\python.exe -m services.hid.cli --dry-run screen test-pattern
.\.venv\Scripts\python.exe -m services.hid.cli clock sync
```

Upload requires exactly one exact `AULA F75Max` release `0x0108` MI_03 control
endpoint and one MI_02 screen endpoint. It reports the first three bytes from
every page ACK and requires the pinned `01 5A 02` prefix across the transfer.
The exact-board test-pattern run on 2026-08-12 returned `01 5A 02` for all 9
pages of stream SHA256
`d4e94333d863cdfdd7f08deae09f5eb8b9d0011375150de8a8b5cded89f342bf`;
the watchdog exited 0. Normal upload and restore require that shipped prefix.
They still fail before discovery if it is intentionally unset.

Default restore uses 251 cached numbered PNG frames in ignored
`.local-assets/lcd/stock/frames` (with the ignored `.rollback` copy as fallback)
and requires an explicit candidate `--delay`. The manifest locks dimensions,
251-frame count, source/stream hashes, and 2009-page geometry before discovery.
Candidate byte `10` is not proven until live visual confirmation, so a live
restore also requires `--allow-unverified-timing`. The installed vendor `0.gif`
has zero/ambiguous embedded timing and is accepted only as an explicit override
with a warning.

## Key remapping

Wired only (the wireless remap sender is undocumented — contract 6.4).
Mapping files are JSON: a list of `{"position": ..., "send": ...}` (bare or
under a `"mappings"` key). `position` is a physical key name from
`data/f75max_layout.json`; `send` is a usage name (layout names plus
`PrintScreen`, `KeypadAsterisk`, `KeypadSlash`, `KeypadMinus`, `Ctrl_R`,
`Win_R`, `Menu`, keypad digits, ...) or hex like `0x46`.

```powershell
.\.venv\Scripts\python.exe -m services.hid.cli remap show services/hid/data/board_map.json
.\.venv\Scripts\python.exe -m services.hid.cli remap apply services/hid/data/board_map.json
.\.venv\Scripts\python.exe -m services.hid.cli remap reset
```

`show` resolves the file and prints every slot and wire payload without
touching hardware. `reset` sends the all-zero table, restoring firmware
defaults for every key. The Fn key (key_index 96) is the hardware layer key
and can never be remapped — the builders refuse.

Transaction: `04 18` begin, `04 11` init (byte8=0x09), the 576-byte slot
table as 9 feature reports, `04 02` apply, `04 F0` finalize
([F108] pkg/aula/remap.go, hardware-verified there; contract section 6.4).

Library use:

```python
from services.hid.client import Client, HidapiTransport
from services.hid.device import (
    discover,
    enumerate_hid,
    require_exact_dongle_config,
    require_exact_wired_config,
)
from services.hid.protocol import LightingConfig

found = discover(enumerate_hid())
client = Client(HidapiTransport(require_exact_wired_config(found)))
client.set_lighting_wired(LightingConfig(mode=7, red=255, brightness=5, speed=3))
percent = Client(HidapiTransport(require_exact_dongle_config(found))).read_battery()
```

## Tests

```
.\.venv\Scripts\python.exe -m unittest discover -s services/hid/tests -t .
```

192 gate tests, under 2 seconds on this workstation, deterministic. They cover
RGB565 primaries, fit geometry, 1/251/255-frame headers, zero padding,
sequence and manifest-locked restore, malformed-stream rejection before any
transfer, unique config and dual-endpoint rejection, opt-in ACK learning,
page ACK timeout/prefix/cleanup, definite begin-send versus uncertain begin-ACK
cleanup, exact 65/33/4097-byte hidapi write results, golden payload bytes,
parser round-trips, checksum behaviour, 35 ms pacing (fake clock),
transaction sequencing/ACK handling, exact clock-data echo validation while
control ACKs remain strict, discovery filtering against fake
enumerations, remap tables/name resolution/Fn guard/reset identity, `remap
show` output stability, a no-hardware guard on `apply_remap`, and every CLI
path via injected fakes.

Periodic policy eval (kept separate from the sub-2-second gate lane):

```
.\.venv\Scripts\python.exe -m unittest discover -s services/hid/evals -p "test_*.py"
```

Twelve policy evals score exact-model documentation, board/host agreement,
token uniqueness, F16/F17 isolation, named-profile safety, LCD manifest
integrity, bounded positive stale-input polling, clock echo/strict-ACK policy,
exact real-write return counts, screen transaction-state cleanup, and the
absence of tracked or staged media/binaries.

## Verified vs untested-on-hardware

**Verified live on Jon's exact F75 Max (`0C45:800A REV_0108`):**

- Wired config discovery on the `0xFF13` collection.
- Full wired lighting transactions, including positive required ACKs.
- The 576-byte normal-layer remap transaction (`04 11`) with 17 canonical
  mappings and a positive finalize ACK. See contract section 11.
- The 9-page LCD test-pattern transport. All 9 MI_02 page ACKs matched the
  pinned `01 5A 02` prefix and the watchdog exited 0. This proves transfer
  acceptance, not visual content correctness.
- Aurora and Focus Core custom images, each as a complete 9-page transfer with
  pinned `01 5A 02` ACKs. Their exact stream hashes are in the project log. An
  earlier Aurora final-apply `FF` response was rejected before the successful
  retry, proving the client does not report success on a bad apply ACK.
- The corrected four-step clock transaction at 2026-08-12 22:28:36. The data
  step used an exact payload echo; begin, select, and apply used strict status
  ACKs. Direct screen-clock observation remains separate.

The protocol has no settings readback. Those ACKs prove transaction acceptance,
not a host-side read of the stored slots. `services/hostlayer/tests/probe.ahk`
provides manual physical-event capture when a person is at the board.

**Verified on F75 Max hardware by the upstream source (mastercoder26/Aula-F75-Max-OSX):**

- Battery request/response over the dongle (`20 01`, percent at byte 3).
- Dongle lighting packet (`05 10`, 32 bytes, checksum at byte 31).
- Dongle function/game-mode packet (`07 10`).
- Wired transaction begin/apply (`04 18`/`04 02`) and clock sync (`04 28`).
- Wired LCD transfer using MI_03 control and MI_02 4096-byte pages (`04 72`).
- Discovery targets (0xFF13 / 0xFF68 / 0xFF60 usage pages).

**Verified only upstream, not on Jon's local transport:**

- FN-layer remap (`04 27`) and its `fnlayer_disable` gating are verified only
  on the F108 Pro reference implementation. Normal-layer remap is verified
  locally; wireless remap is undocumented.

**Remaining live/visual proof:**

- The 251-frame restore candidate timing still needs a controlled live run and
  visual confirmation. Custom Aurora/Focus Core transfer is verified, but an
  explicit displayed-pixel correctness record is still needed.
- Clock sync is transaction-verified after the corrected live retry. Direct
  observation of the LCD clock value is still needed.

**Untested anywhere / honest gaps** (details in the contract, section 9):

- Wired battery query — no such opcode exists in any source.
- Battery response byte 2 meaning (possibly charging flag).
- Per-key RGB live apply and macro storage. Per-key text compilation is
  implemented, but exact-F75 wire mutation is capture-gated.
- Remap action types other than `02` (key), FN-layer remap, and wireless remap.

## Safety properties

- `protocol.py` imports nothing but `dataclasses` (a gate test fails
  otherwise).
- Only `HidapiTransport` (client.py) and `enumerate_hid()` (device.py) touch
  the hidapi package, both behind lazy imports; tests and dry runs never
  reach them.
- Every live battery, lighting, clock, and remap path requires exactly one
  matching config endpoint before opening it. Dongle identity requires
  `05AC:024F`, usage page `0xFF60`, and usage `0x61`; usage-page-zero MI_03 is
  never accepted as a dongle fallback. Wired mutations require all three live
  HID identity fields: `0C45:800A`,
  product `AULA F75Max`, and release `0x0108`. The VID/PID is shared by a
  related AULA platform, so a missing or mismatched product/revision fails
  closed before the transport opens.
- Wired readbacks are mandatory ACK checks (`AckError` on failure) because
  the firmware ignores unacknowledged command streams; `strict_ack=False`
  exists for probing.
- LCD pages can only be 4096-byte MI_02 output reports. They are never feature
  reports. After any started upload, the client waits 100 ms and attempts final
  `04 02`; it never sends `04 F0` for screen data.
- MI_02 stale-input drain uses at most 32 one-millisecond reads. Timeout zero is
  rejected at the real transport boundary because hidapi 0.15 defines it as an
  unbounded wait.
- Real hidapi writes must return the complete report-ID-prefixed byte count:
  feature `65`, dongle output `33`, LCD output `4097`. Any other type or count
  raises before the caller can report success.
- Screen apply cleanup runs only after begin was fully sent. A failed begin ACK
  remains possibly active and gets conservative cleanup; a definite begin-send
  failure does not send `04 02`.
- `.local-assets/` is ignored. Generated PNGs, 251 restore frames, and prepared
  streams never enter Git; committed manifests and prompts are text only.
