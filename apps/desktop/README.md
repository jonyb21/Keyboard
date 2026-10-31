# AULA desktop app

Electron front end for the AULA F75 Max. The window is the round-4 design; all
keyboard I/O goes through `services/hid/bridge.py` (one JSON request per call).

## Run

From the repo root, once per machine:

```
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r services/hid/requirements.txt
cd apps\desktop && npm install
```

Then `npm start` in `apps/desktop`, or double-click `AULA.cmd`.

The board picture is the vendor render, which is personal-use only and never
committed: put `f75max-black.png` in `.local-assets/` (ignored).

## What reaches the keyboard

Sent: normal-layer key remaps (key usages only), the lighting effect,
brightness and speed, and an uploaded screen image. Everything else (Fn layer,
media/combos/macros, host-layer triggers, knob, settings, locks) stays staged
and is listed as "not connected yet".

Sends are dry runs, showing the exact payloads, unless the app is started with
`AULA_LIVE_WRITES=1` (`AULA-live.cmd` does this). `remap reset` is not reachable from the app: it would
clear the F13-F24 board map.
