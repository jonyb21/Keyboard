# AULA F75 Max upgrade record

Session: 2026-08-12. Hardware: AULA F75 Max Gasket, wired USB `0C45:800A`,
revision `0108`.

## Outcome

The board uses the fastest supported response setting, a one-minute idle
timeout, a firmware-accepted collision-free onboard map, and a tested host-side
productivity layer that starts hidden at logon. The repo now also has exact-F75
LCD/clock tooling, two named stock-engine lighting profiles, and capture-gated
per-key profile compilation. Focus Core Static `168BFF`, brightness 2 is the
final live lighting state after successful Aurora and Focus Core applications.

## Media and named lighting package

- `Focus Core`: deployable verified stock Static mode, `168BFF`, brightness 2.
- `Aurora`: deployable verified stock Flowing mode, `7D42FF`, brightness 4,
  colorful palette.
- Per-key Focus Core/Aurora: deterministic 80-key `light_index` compiler and
  show/dry-run output; live apply refuses with `capture_required` because the
  only known `04 23` command is F108-specific.
- LCD sources: generated Focus Core and Aurora PNGs copied to ignored
  `.local-assets/lcd/`; only prompt text, hashes, and manifests are tracked.
- Stock restore: 251 preserved decoded PNG frames copied to ignored
  `.local-assets/lcd/stock/frames`, candidate delay byte 10, 2009 output pages,
  prepared SHA256 `b2ac629ee68ca7fc929a8861d1ed851182e9bfafb2fc7ef9d3f3658fe0d32d3a`.
  The database-to-wire timing conversion is not proven. The installed `0.gif`
  is not the default because its embedded timing is zero/ambiguous.

LCD upload sends 4096-byte pages only as MI_02 output reports. It requires one
unambiguous exact MI_03/MI_02 endpoint pair and enforces the pinned `01 5A 02`
page ACK prefix. The successful 2026-08-12 test-pattern returned that prefix on
all 9 pages for stream SHA256
`d4e94333d863cdfdd7f08deae09f5eb8b9d0011375150de8a8b5cded89f342bf`;
the watchdog exited 0. The uploader records every prefix, waits 100 ms, and
attempts final `04 02` even after failure. It never sends `04 F0`. Custom image
Displayed-pixel correctness and restore timing still require an explicit visual
record.

## Hardware and software decision

The installed AULA F75 Max Gasket configurator `1.0.0.5` exactly matches the
live board. The vendor package has no firmware binary or update URL, while AULA
publishes multiple incompatible F75 firmware branches. No firmware was flashed.
`services/device_audit` now makes that decision deterministic and fail-closed.

## Onboard settings

| Setting | Before | After |
|---|---:|---:|
| Key response | Level 2, about 5-6 ms wired | Level 1, about 2-3 ms wired |
| Sleep | 5 minutes | 1 minute |
| Lighting | Rolling rainbow, brightness 5/5 | Focus Core Static `168BFF`, brightness 2/5 (final live state after Aurora verification). |

Level 1 is vendor-supported. If physical switch chatter appears, return only
the response setting to Level 2; all other improvements remain valid.

## Cross-keyboard collision repair

The running CIDOO V21 runtime globally owns bare `F16/F17` for encoder rotation.
The previous AULA map used those tokens for physical F6/F7, so those keys were
silently routed as CIDOO encoder events even before the AULA host layer ran.

The AULA range is now:

| Physical key | Token |
|---|---|
| F1, F3, F5 | F13, F14, F15 |
| F6, F7, F8 | F18, F19, F20 |
| F9, F10, F11, F12 | F21, F22, F23, F24 |

The canonical onboard map and host config carry the same exact set. Gate tests
and a policy eval reject any reintroduction of `F16/F17`.

## Productivity layer

Physical F-row actions preserve the evidence-driven order: undo/redo,
Chrome/Outlook, window switching, SolidWorks/Explorer, Claude/Remote Desktop,
media, numeric-layer toggle, paste/copy, media transport, and save/save-as.
Space and CapsLock remain untouched. The sticky right-hand num layer preserves
ordinary typing for every unmapped key.

## Rollback

Pre-change vendor profile, driver config, matching installer, and hashes are in
the ignored local directory `.rollback/2026-08-12-before-efficiency-tune/`.
The canonical board map can be reapplied with:

```powershell
.\.venv\Scripts\python.exe -m services.hid.cli remap apply services/hid/data/board_map.json
```

The vendor response/sleep settings can be restored to Level 2 / 5 minutes in
the exact `1.0.0.5` configurator. No firmware rollback is needed because no
firmware flash occurred.

## Verification

- Hostlayer gate: 139 tests plus AHK syntax, config, and hotkey acceptance.
- HID service: 192 deterministic tests, under two seconds in the repo venv.
- HID policy eval: 12/12.
- Device audit: 17 deterministic tests; compatibility eval 9/9, score 1.0.
- Live audit: exact hardware/configurator/profile match, response level 1,
  `safe_to_configure=true`, `safe_to_flash_firmware=false`.
- Live onboard remap: 17-key transaction completed with all required ACKs.
- Readback limit: the firmware exposes no settings query. ACKs prove the table
  was accepted, but this session did not capture a physical F-row event.
  `services/hostlayer/tests/probe.ahk` is included for that manual check.
- Pre-live safety incident: the exact command
  `.\.venv\Scripts\python.exe -m services.hid.cli light --mode 1 --transport wired`
  was accidentally invoked once. It returned exit 0 and applied Static with
  the raw-command defaults: white, brightness 5, speed 3. No screen, clock, or
  remap command ran. The later controlled sequence restored Focus Core
  brightness 2.
- Custom-image upload and clock sync have now completed live. Default restore
  timing, displayed-pixel correctness, and direct clock observation still need
  explicit visual confirmation.
- First controlled LCD test-pattern attempt: stale-input drain called hidapi
  `read(64, 0)`. With hidapi 0.15, zero is an unbounded timeout, so it blocked
  before page 1. The shell timed out and left PIDs 34212 and 33032 alive until
  the coordinator terminated them. No LCD page output or page ACK was captured;
  the ACK prefix remained unset after that failed attempt. The regression fix
  uses at most 32 positive 1 ms polls and rejects nonpositive timeouts at the
  real transport boundary.
- Successful controlled LCD retry: all 9 test-pattern pages returned ACK prefix
  `01 5A 02` for stream SHA256
  `d4e94333d863cdfdd7f08deae09f5eb8b9d0011375150de8a8b5cded89f342bf`.
  The watchdog exited 0, and the prefix is now pinned in `protocol.py` for
  normal upload and restore.
- First controlled clock-sync attempt: strict begin and `04 28` select ACKs
  passed, then the board returned `00 01 5A 1A 08 0C 16 17 ...` for the
  `00 01` data command. That is the sent payload echoed back, with byte 3 equal
  to the 2026 year offset `0x1A`; the generic validator falsely treated byte 3
  as a status field, raised `AckError`, and stopped before apply. The clock-data
  path now requires a byte-for-byte 64-byte payload echo while begin, select,
  and apply retain strict status ACKs. Malformed and non-echo regressions are
  covered offline. Clock apply and direct screen observation still needed one
  controlled live retry at that point.
- Corrected clock retry: at 2026-08-12 22:28:36 the complete transaction,
  including strict final apply, succeeded on the exact board. This proves the
  response semantics and accepted transaction; direct LCD clock observation is
  not claimed.
- Named custom-image sequence: Aurora's first upload reached final apply but
  received an `FF` readback, so the client failed closed and did not report a
  successful upload. Aurora's retry completed 9/9 pages with pinned prefix
  `01 5A 02`, stream SHA256
  `5e9aed91787c759433597eb59c81a5f334143512e2d42129b2f3a0723c92da3f`.
  Focus Core then completed 9/9 pages with the same prefix, stream SHA256
  `283c0a20fc56b9f56d6de1287a2a4097cf2de86d949f9edf659f9179ad5aa622`.
  The runs prove transfer and fail-closed apply handling, not pixel-level visual
  correctness.
- Named stock lighting: Aurora was applied live, then Focus Core was applied as
  the final efficient state: Static `168BFF`, brightness 2.
- Final runtime hardening: real feature writes now require hidapi integer return
  `65`, dongle output requires `33`, and LCD output requires `4097`; missing,
  boolean, negative, zero, short, and overlong results fail closed. LCD cleanup
  now skips `04 02` after a definite begin-send failure. Once begin sends fully,
  even a failed begin ACK is treated as possibly active and gets the conservative
  100 ms plus apply-cleanup attempt. Injected gate tests and policy evals cover
  all three report kinds and both transaction-state branches; no hardware was
  opened for this review fix.
