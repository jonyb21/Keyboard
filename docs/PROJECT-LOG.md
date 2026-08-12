# AULA F75 Max upgrade record

Session: 2026-08-12. Hardware: AULA F75 Max Gasket, wired USB `0C45:800A`,
revision `0108`.

## Outcome

The board now uses the fastest supported response setting, lower-power lighting,
a one-minute idle timeout, a firmware-accepted collision-free onboard map, and
a tested host-side productivity layer that starts hidden at logon.

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
| Lighting | Rolling rainbow, brightness 5/5 | Static `D9E8FF`, brightness 2/5 |

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
- HID service: 111 deterministic tests.
- HID policy eval: 4/4.
- Device audit: 17 deterministic tests; compatibility eval 9/9, score 1.0.
- Live audit: exact hardware/configurator/profile match, response level 1,
  `safe_to_configure=true`, `safe_to_flash_firmware=false`.
- Live onboard remap: 17-key transaction completed with all required ACKs.
- Readback limit: the firmware exposes no settings query. ACKs prove the table
  was accepted, but this session did not capture a physical F-row event.
  `services/hostlayer/tests/probe.ahk` is included for that manual check.
