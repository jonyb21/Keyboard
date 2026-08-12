# `services/device_audit`

Read-only, deterministic inventory for one hardware/software contract:

| Evidence | Required value |
|---|---|
| USB identity | `0C45:800A` |
| USB hardware revision | `REV_0108` |
| Bus-reported product | `AULA F75Max` |
| Vendor configurator | `AULA F75 Max Gasket Mechanical Keyboard Driver` |
| Configurator version | `1.0.0.5` |
| Configurator USB target | `0C45:800A`, product `AULA F75Max` |
| Profile database | SQLite `quick_check=ok`, exactly one active profile |
| Response state | active profile has integer `key_respondtime` from 1 through 5 |

The measurable outcome is `verdict.ready_for_efficiency_changes`. It is `true`
only when all independent evidence sources match. A similar-looking F75, an
ISO `80B1` board, a plain-F75 `258A:010C` board, the wrong AULA app, a damaged
profile database, or an unreadable evidence source all produce a fail-closed
recommendation.

## Run

From the repository root:

```powershell
py -m services.device_audit --pretty
```

The complete result is emitted as JSON on stdout. Exit code `0` means the exact
hardware, configurator, and profile contract matched. Exit code `2` means it did
not. The service does not write a report file.

Useful diagnostic overrides:

```powershell
py -m services.device_audit --config-xml C:\path\config.xml --profile-db C:\path\profile.db --pretty
```

## What is inspected

1. `Get-PnpDevice` and `Get-PnpDeviceProperty` identify the present USB root,
   its `REV_0108` hardware ID, bus product name, and Microsoft HID driver.
2. The three Windows uninstall registry views identify installed AULA software.
   Only the full `F75 Max Gasket` product name is eligible. The adjacent F75,
   F75MAX ISO, HE, wired, Ultra, and retailer-named Pro branches are never
   treated as substitutes.
3. The selected installation's `config.xml` proves what VID/PID and product name
   the app actually targets. Its SHA-256 is included in the output.
4. The vendor SQLite database is opened with `mode=ro` and `query_only=ON`.
   The audit reports integrity, aggregate row counts, active profile metadata,
   selected non-secret config values, and raw response level. Macro bodies and
   application bindings are not emitted.

The default profile path is:

```text
%LOCALAPPDATA%\AULA F75 Max Gasket Mechanical Keyboard Driver Files\db\AULA F75 Max Gasket Mechanical Keyboard_datav1.db
```

The profile's `version` value is reported as profile data. It is not confused
with the installed configurator version `1.0.0.5`.

## Safety contract

- No HID device is opened.
- No firmware updater is invoked.
- No registry, config, profile, or database state is changed.
- `safe_to_flash_firmware` is always `false`; this audit never authorizes a flash.
- Missing or ambiguous evidence cannot produce a safe recommendation.

If the service reports `stop_wrong_hardware` or `stop_wrong_configurator`, stop.
Cross-flashing another F75 branch can break mappings, RGB, software detection,
or the board itself. `repair_profile_state_before_changes` means restore the
known backup or let the exact matching configurator recreate its local profile,
then audit again.

## Gate tests

```powershell
py -m unittest discover -s services/device_audit/tests -t .
```

The tests use temporary XML and SQLite fixtures. They make no device, registry,
network, or vendor-app calls.

## Periodic compatibility eval

```powershell
py services/device_audit/evals/score.py
```

The fixture covers the exact board plus wrong-model, wrong-revision,
wrong-driver-version, wrong-driver-target, and unhealthy-profile cases. The
threshold is `1.0`: every case must choose the expected action and safety bit.
The scorer emits machine-readable JSON and exits nonzero below threshold.

## Failure modes

- PowerShell or the PnP cmdlets unavailable: PnP evidence fails closed.
- Keyboard connected only through Bluetooth/2.4G: no matching wired USB root,
  so the audit stops.
- Registry entry missing but files remain: the configurator is not considered
  installed.
- `config.xml` missing/malformed or targeting another VID/PID: wrong
  configurator.
- SQLite missing, corrupt, locked, without one active profile, or without a
  valid response level: profile repair required.

Nothing needs restarting after adding or running this service.
