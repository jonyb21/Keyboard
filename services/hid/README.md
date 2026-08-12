# services/hid — AULA F75 Max stock-firmware protocol client

Python client for the F75 Max's HID config protocol: device discovery,
battery read (2.4G dongle), lighting control (wired feature reports and
dongle output reports), LCD clock sync, and key remapping, with the
firmware's 35 ms command pacing built in.

Wire contract: [`contracts/hid_protocol.md`](../../contracts/hid_protocol.md).
Every opcode in this package carries a comment citing the public source it
was recovered from.

## Layout

| File | Role |
|---|---|
| `protocol.py` | Pure payload builders/parsers. Deterministic, zero I/O imports (test-enforced). |
| `device.py` | Endpoint discovery/classification over hidapi-style enumeration dicts. Real enumeration only via `enumerate_hid()`. |
| `client.py` | `Client` (sequences + 35 ms `Pacer`) over an injectable `Transport`. `HidapiTransport` is the ONLY code that opens hardware. |
| `remap.py` | Mapping model: JSON mapping files -> resolved remap slots via the layout truth table. |
| `cli.py` | `status`, `battery`, `light`, `remap` commands, `--dry-run` support. |
| `data/f75max_layout.json` | F75 key_index ground truth, parsed from the vendor app's `layouts/rgb-keyboard.xml` (v1.0.0.5). |
| `data/board_map.json` | CANONICAL current firmware map: 7 keycap-truth remaps + F1,F3,F5-F12 -> F13-F15/F18-F24 for hostlayer feature bindings. F16/F17 are reserved by the co-installed CIDOO encoder runtime and are regression-tested as absent. |
| `tests/` | Gate tests: stdlib `unittest`, no network, no device, fake clock. |

## Usage

From the repo root, dry runs use only the standard library:

```
py -m services.hid.cli --dry-run battery
py -m services.hid.cli --dry-run light --mode rolling --colorful
```

Create the ignored project-local environment once, then use that exact
interpreter for enumeration and real hardware:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r services\hid\requirements.txt
.\.venv\Scripts\python.exe -m services.hid.cli status
.\.venv\Scripts\python.exe -m services.hid.cli battery
.\.venv\Scripts\python.exe -m services.hid.cli light --mode breath --brightness 5 --speed 3 --color FF0000
.\.venv\Scripts\python.exe -m services.hid.cli light --mode 11 --colorful --transport dongle
```

Modes: `0`/`off` .. `19`/`shuttle` (names in `protocol.LIGHT_MODES`;
factory default is 11 `rolling`). Brightness 0-5, speed 0-5, direction 0/1,
`--colorful` switches to the rainbow palette.

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
from services.hid.device import discover, enumerate_hid
from services.hid.protocol import LightingConfig

found = discover(enumerate_hid())
client = Client(HidapiTransport(found.wired_config))
client.set_lighting_wired(LightingConfig(mode=7, red=255, brightness=5, speed=3))
percent = Client(HidapiTransport(found.dongle_config)).read_battery()
```

## Tests

```
py -m unittest discover -s services/hid/tests -t .
```

111 gate tests, ~1.6 s, deterministic. They cover golden payload bytes,
parser round-trips, checksum behaviour, 35 ms pacing (fake clock),
transaction sequencing/ACK handling, discovery filtering against fake
enumerations, remap tables/name resolution/Fn guard/reset identity, `remap
show` output stability, a no-hardware guard on `apply_remap`, and every CLI
path via injected fakes.

Periodic policy eval (kept separate from the sub-2-second gate lane):

```
py -m unittest discover -s services/hid/evals -p "test_*.py"
```

It scores exact-model documentation, board/host agreement, token uniqueness,
and the required absence of `F16`/`F17`, which belong to the co-installed
CIDOO encoder runtime.

## Verified vs untested-on-hardware

**Verified live on Jon's exact F75 Max (`0C45:800A REV_0108`):**

- Wired config discovery on the `0xFF13` collection.
- Full wired lighting transactions, including positive required ACKs.
- The 576-byte normal-layer remap transaction (`04 11`) with 17 canonical
  mappings and a positive finalize ACK. See contract section 11.

The protocol has no settings readback. Those ACKs prove transaction acceptance,
not a host-side read of the stored slots. `services/hostlayer/tests/probe.ahk`
provides manual physical-event capture when a person is at the board.

**Verified on F75 Max hardware by the upstream source (mastercoder26/Aula-F75-Max-OSX):**

- Battery request/response over the dongle (`20 01`, percent at byte 3).
- Dongle lighting packet (`05 10`, 32 bytes, checksum at byte 31).
- Dongle function/game-mode packet (`07 10`).
- Wired transaction begin/apply (`04 18`/`04 02`) and clock sync (`04 28`).
- Discovery targets (0xFF13 / 0xFF68 / 0xFF60 usage pages).

**Verified only upstream, not on Jon's local transport:**

- FN-layer remap (`04 27`) and its `fnlayer_disable` gating are verified only
  on the F108 Pro reference implementation. Normal-layer remap is verified
  locally; wireless remap is undocumented.

**Untested anywhere / honest gaps** (details in the contract, section 9):

- Wired battery query — no such opcode exists in any source.
- Battery response byte 2 meaning (possibly charging flag).
- Per-key RGB, macro, and screen upload — documented in the contract,
  intentionally not implemented.
- Remap action types other than `02` (key), FN-layer remap, and wireless remap.

## Safety properties

- `protocol.py` imports nothing but `dataclasses` (a gate test fails
  otherwise).
- Only `HidapiTransport` (client.py) and `enumerate_hid()` (device.py) touch
  the hidapi package, both behind lazy imports; tests and dry runs never
  reach them.
- Wired mutations require all three live HID identity fields: `0C45:800A`,
  product `AULA F75Max`, and release `0x0108`. The VID/PID is shared by a
  related AULA platform, so a missing or mismatched product/revision fails
  closed before the transport opens.
- Wired readbacks are mandatory ACK checks (`AckError` on failure) because
  the firmware ignores unacknowledged command streams; `strict_ack=False`
  exists for probing.
