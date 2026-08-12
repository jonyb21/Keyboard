# AULA F75 Max control

Complete, tested control stack for Jon's exact AULA F75 Max Gasket:
`0C45:800A`, hardware revision `0108`, wired USB.

- `services/device_audit`: fail-closed hardware, driver, and profile inventory.
- `services/hid`: stock-firmware lighting and onboard remap client.
- `services/hostlayer`: collision-free tap/hold productivity bindings and num layer.
- `startup`: hidden logon launcher for the host layer.
- `tools/validate.ps1`: deterministic gate lane for the complete repository.

Current measurable state:

- Key response level 1: approximately 2-3 ms wired.
- Sleep timeout: 1 minute.
- Static RGB at brightness 2/5 to cut animation and LED load.
- The firmware accepted a canonical map that programs physical F1/F3/F5-F12
  as `F13-F15` and `F18-F24`; `F16/F17` are reserved for the co-installed
  CIDOO encoder runtime.

Run every gate:

```powershell
powershell -ExecutionPolicy Bypass -File tools\validate.ps1
```

Run the live read-only audit:

```powershell
py -m services.device_audit --pretty
```

Firmware flashing is deliberately blocked. AULA publishes separate F75,
F75 Max, wired, HE, ISO, and regional firmware branches, and this board has no
vendor-supported firmware backup/rollback path. The installed configurator
`1.0.0.5` is the exact matching current package for `0C45:800A`.
