# AULA F75 Max control

Complete, tested control stack for Jon's exact AULA F75 Max Gasket:
`0C45:800A`, hardware revision `0108`, wired USB.

- `services/device_audit`: fail-closed hardware, driver, and profile inventory.
- `services/hid`: stock-firmware lighting, named presets, LCD/clock, and onboard remap client.
- `services/hostlayer`: collision-free tap/hold productivity bindings and num layer.
- `startup`: hidden logon launcher for the host layer.
- `tools/validate.ps1`: deterministic gate lane for the complete repository.

Current measurable state:

- Key response level 1: approximately 2-3 ms wired.
- Sleep timeout: 1 minute.
- Focus Core Static RGB at brightness 2/5 is the final live lighting state,
  reducing animation and LED load. Aurora was also applied successfully live.
- Focus Core is a deployable named low-power stock preset; Aurora is a
  deployable stock-engine animated preset. Per-key profile compilation is
  available for inspection and refuses live apply until an exact-F75 capture.
- Two generated 128x128-ready LCD sources and a 251-frame stock restore are
  stored only in ignored `.local-assets/lcd/`; Git holds text prompts,
  manifests, hashes, and profile definitions.
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

Prepare and dry-run the media controls:

```powershell
.\.venv\Scripts\python.exe -m services.hid.cli --dry-run light preset "Focus Core" --transport wired
.\.venv\Scripts\python.exe -m services.hid.cli screen inspect .local-assets\lcd\focus-core.png --fit cover
.\.venv\Scripts\python.exe -m services.hid.cli --dry-run screen upload .local-assets\lcd\focus-core.png --fit cover
.\.venv\Scripts\python.exe -m services.hid.cli --dry-run screen restore --delay 10
.\.venv\Scripts\python.exe -m services.hid.cli --dry-run clock sync
```

Live battery, lighting, clock, and remap commands require one unique exact
configuration endpoint before opening it. Live screen upload is fail-closed to
exactly one `AULA F75Max` release `0108` MI_03/MI_02 pair and returns per-page
ACK prefix evidence. The committed `01 5A 02` prefix is mandatory for every
normal image upload and restore; an intentionally unpinned build fails before
discovery. The 2026-08-12 exact-board test-pattern returned that prefix for all 9 pages of
stream SHA256
`d4e94333d863cdfdd7f08deae09f5eb8b9d0011375150de8a8b5cded89f342bf`;
  the watchdog exited 0. Aurora and Focus Core then each completed a 9-page
  live upload with the pinned prefix; their stream SHA256 values are recorded
  in `docs/PROJECT-LOG.md`. Displayed-pixel correctness still needs an explicit
  visual record.

The live clock retry completed at 2026-08-12 22:28:36 after the client was
corrected to accept the exact `00 01` data echo while retaining strict status
ACKs for begin, select, and apply. Direct LCD clock observation remains pending.

The default 251-frame restore is locked to the committed dimensions, frame
count, source hash, stream hash, 2009-page count, and an explicitly selected
candidate delay. Byte `10` is only a candidate until a live visual check. A
live restore additionally requires `--allow-unverified-timing`.

Firmware flashing is deliberately blocked. AULA publishes separate F75,
F75 Max, wired, HE, ISO, and regional firmware branches, and this board has no
vendor-supported firmware backup/rollback path. The installed configurator
`1.0.0.5` is the exact matching current package for `0C45:800A`.
