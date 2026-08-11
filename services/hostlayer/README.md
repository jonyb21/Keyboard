# services/hostlayer

The host-side layer engine for the AULA F75 Max. AutoHotkey v2, driven entirely
from `layers.json`.

The board's firmware already stores its own remap in board memory, so the
keyboard types correctly on any PC with nothing installed. Part of that remap
makes the F-row emit **F13-F22** (`0x68`-`0x71`) instead of F1-F12. Those are
real USB HID codes that no physical keyboard produces, which means they can be
bound to anything without ever colliding with the laptop keyboard.

This service is the only thing that gives those ten codes meaning. It adds two
capabilities the stock firmware does not have: **tap versus hold** on a single
key, and a **sticky extra layer**.

---

## What it touches

Nothing except F13-F22 and, while the num layer is on, twelve keys on the right
hand. That is the whole surface.

**Space and CapsLock are protected.** They are not bound, not registered, and
the config validator refuses to name them. An earlier design put a nav layer on
hold-Space and Esc/Ctrl on CapsLock; both were removed because they change how
ordinary typing feels, and neither is worth that. The guard lives in
`HL_ProtectedKeys()` and is asserted by four tests, so it cannot be walked back
by accident.

---

## Bindings

| Cap | Emits | Tap | Hold | Why this pair |
|---|---|---|---|---|
| F1 | F13 | Undo (`Ctrl+Z`) | Redo (`Ctrl+Shift+Z`, or `Ctrl+Y` in SolidWorks and Excel) | Undo and redo are the same gesture at different pressures; the two apps that never adopted `Ctrl+Shift+Z` get their own chord. |
| F3 | F14 | Focus Chrome | Focus Outlook | The two most-switched-to non-CAD apps: Chrome 16.2% of focus events, Outlook 5.8%. |
| F5 | F15 | `Alt+Tab` (flip to last window) | Hold-to-browse switcher | A flip and a browse are different intents; splitting them removes the "tap Alt+Tab repeatedly and overshoot" failure. |
| F6 | F16 | Focus SolidWorks | Focus Explorer (new window if none open) | SolidWorks is 19.1% of all app switches, the single biggest target. |
| F7 | F17 | Focus InDesign (Beta) | Focus Photoshop (Beta) | The Adobe pair: InDesign 12%, Photoshop 5.5%. |
| F8 | F18 | Mute | Previous track | See "why media sits here" below. |
| F9 | F19 | Toggle the **num** layer | — | Tap-only, so it fires on key-down with no delay. |
| F10 | F20 | Paste (`Ctrl+V`) | Copy (`Ctrl+C`) | The two highest-count chords measured: paste 778 and copy 443 in 13 days. Tap gets the more frequent one. |
| F11 | F21 | Play / Pause | Next track | See below. |
| F12 | F22 | Save (`Ctrl+S`) | Save As (`Ctrl+Shift+S`) | Same gesture, escalating commitment. |

F2, F4 and Esc are untouched by the firmware remap and untouched here.

**764 app switches per day** were measured, which is why five of the ten keys are
focus-or-launch rather than chords.

### Why media sits on F18 and F21

Media keys are the two bindings with no evidence behind them. Volume and
transport keys never reach an application, so the keystroke telemetry that
ranked everything else is structurally blind to them: there is no measurement
that could rank them higher or lower. They were demoted to the two keys nothing
else wanted rather than given a prime position on a guess. If you find yourself
reaching for them, that is data, and they should move.

### focus-or-launch

1. A matching window exists, activate it. If one is already active, advance to
   the next window of that app, so repeated presses cycle.
2. No matching window, run the configured `launch` path.
3. No `launch` path, do nothing.

Explorer carries `"windowClass": "CabinetWClass"` because `explorer.exe` is also
the desktop and the taskbar. Matching on the exe alone would always find a
window and a new file browser would never open.

Resolved install paths on this machine (all verified to exist, asserted by
`test_shipped_focus_app_launch_paths_exist_on_disk`):

| App | exe matched | launched from |
|---|---|---|
| Chrome | `chrome.exe` | `C:\Program Files\Google\Chrome\Application\chrome.exe` |
| Outlook | `OUTLOOK.EXE` | `C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE` |
| SolidWorks | `SLDWORKS.exe` | `C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\SLDWORKS.exe` |
| Explorer | `explorer.exe` + `CabinetWClass` | `C:\Windows\explorer.exe` |
| InDesign Beta | `InDesign (Beta).exe` | `C:\Program Files\Adobe\Adobe InDesign 2026 (Beta)\InDesign (Beta).exe` |
| Photoshop Beta | `Photoshop.exe` | `C:\Program Files\Adobe\Adobe Photoshop (Beta)\Photoshop.exe` |

Outlook is the classic client. New Outlook (`olk.exe`) is installed but unused,
so it is not the target. Photoshop Beta ships the same exe name as the shipping
builds; the Beta is the only live install here and owns the `App Paths` default.

---

## The num layer

`F19` toggles it. **Sticky**: it stays on until you tap F19 again. The tray icon
tooltip and a brief tray tip tell you which way it went, because a sticky layer
with no feedback is a trap.

```
        u   i   o           7   8   9
        j   k   l           4   5   6
        m   ,   .    -->    1   2   3
        n                   0
        /                   .   (decimal point)
        ;                   Enter
```

Two things worth knowing:

- The digits are **top-row digit codes**, not numpad scancodes. Numpad keys only
  produce digits when NumLock happens to be on; top-row codes always work.
- **Space is still Space** while the layer is on. It is not 0, it is not
  anything else. Same for every key not in the grid above: with the layer on,
  `a` is still `a`, and with the layer off none of these twelve keys is hooked
  at all, so typing is byte-identical to running no software.

---

## Editing bindings

Everything lives in `layers.json`. `layers.example.json` is a commented starter
that demonstrates every action kind.

An action is exactly one of:

| Kind | Shape | Does |
|---|---|---|
| `send` | `{"send": "Ctrl+Shift+Z"}` | Sends a chord. Modifiers are `Ctrl` / `Alt` / `Shift` / `Win`, joined with `+`. |
| `focusApp` | `{"focusApp": {"exe": "...", "launch": "...", "windowClass": "..."}}` | Focus-or-launch. `exe` is required; `launch` may be empty for focus-only. |
| `byApp` | `{"byApp": {"<group>": {...}, "default": {...}}}` | Picks a branch from the foreground exe. A `default` branch is required. Groups come from `appGroups`. |
| `builtin` | `{"builtin": "altTabTap"}` or `altTabBrowse` | The two switcher behaviours. |
| `layerToggle` | `{"layerToggle": "num"}` | Flips a sticky layer. |
| `swallow` | `{"swallow": true}` | Eats the key and emits nothing. |

A binding with a `tap` and no `hold` is **tap-only** and fires the instant the
key goes down, with zero added latency. A binding with both waits `tapHoldMs`
(default 200) to decide.

Two rules the validator enforces and will not let you break:

- **`Ctrl+Alt+Shift+V` can never be bound**, anywhere, in any action or layer
  key. It belongs to InDesign's Paste in Place and a Photoshop-internal tool.
- **`space` and `capslock` can never be bound**, in bindings or in a layer.

### Reload

`Ctrl+Alt+R` re-reads `layers.json` and re-registers every hotkey without
restarting the process. It works even while hotkeys are paused. If the new
config is invalid, the old one keeps running and a tray tip says what was wrong,
so a typo can never leave you with a dead keyboard.

The tray menu has **Pause hotkeys**, **Reload config**, and **Exit**.

---

## Running it

```powershell
& "C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe" services\hostlayer\engine.ahk
```

There is no `.ahk` file association on this machine, so always launch through
the exe. Only one instance can run; a second launch shows a tray tip and exits.

### Start at logon

```powershell
powershell -ExecutionPolicy Bypass -File startup\install-startup.ps1
powershell -ExecutionPolicy Bypass -File startup\uninstall-startup.ps1
```

Install creates a shortcut in your Startup folder pointing at `AutoHotkey64.exe`
with `engine.ahk` as its argument. It validates the config first and refuses to
install a broken one. Both scripts are idempotent. Uninstall leaves a running
engine alone unless you pass `-StopRunning`.

No admin rights, no Run key, no scheduled task: a Startup shortcut survives on a
managed machine and you can see and delete it yourself.

### Validate

```powershell
& "C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe" services\hostlayer\engine.ahk /validate services\hostlayer\layers.json
```

Prints `PASS`/`FAIL` plus every error to stdout, exits 0 or 1, registers no
hotkeys, installs no hook, and leaves no process running.

The full gate lane, one command:

```powershell
powershell -ExecutionPolicy Bypass -File tools\validate.ps1
```

That runs the AutoHotkey syntax gate on every script, `/validate` on both
configs, the hotkey-name acceptance test, and the Python suite.

---

## How it is built

| File | Role |
|---|---|
| `core.ahk` | **Pure.** Config parsing, validation, chord compilation, app resolution, the tap-hold state machine, layer resolution. No hotkeys, no Send, no GUI, no writes. |
| `engine.ahk` | **Impure.** Registration, timers, Send, window activation, tray, mutex, `/validate`. |
| `lib/jsonparse.ahk` | Recursive-descent JSON parser. Pure AHK, no COM, no DLL. |
| `tests/mirror.py` | Python port of `core.ahk`, step labels matched one for one. |
| `tests/hotkeynames.ahk` | Proves the hotkey names the configs compile to are ones AutoHotkey accepts. |

The split exists so the logic can be tested. AutoHotkey has no test runner and a
load error opens a modal dialog, so the pure half is mirrored into Python and
executed there. `core.ahk` and `mirror.py` carry the same step labels
(`TH-L1`..`TH-L8` for the tap-hold machine, `LR-L1`..`LR-L3` for the layer
resolver) so the two can be diffed side by side. **Change one, change the
other.**

### Tests

```powershell
cd services\hostlayer
py -m unittest discover -s tests
```

137 tests, no network, no device, fake clock, well under a second.

### Bugs this design exists to prevent

1. **F-row leakage.** The previous build registered bare `F13`..`F22`, so any
   modified press fell through and the raw F-key reached the application; seven
   leaks were measured. Everything is now registered as `$*F13` and `$*F13 up`,
   which claims every modifier combination and both edges. `InstallKeybdHook` is
   called explicitly rather than left to chance.
2. **Autorepeat firing the tap action.** Holding a key past the threshold makes
   Windows deliver repeated key-down events with no key-up. The state machine
   treats any key-down while `pending`, `held` or `downfired` as a repeat and
   swallows it. Six tests cover this.
3. **The late hold.** A tap has to cancel its pending timer, and a one-shot
   timer already in flight has to be ignored on arrival. Both paths are tested.
4. **UTF-8 BOM.** A BOM in front of `{` breaks every naive JSON parser. Stripped
   on read, tested.
5. **Stranded Alt.** The hold-to-browse switcher holds Alt down. It is released
   on key-up, on pause, on reload, and on exit.

---

## The limitation you should know about

**AutoHotkey's keyboard hook is global.** It sees every attached keyboard and
cannot tell them apart. So while these bindings are aimed at the AULA, they
apply to whatever is plugged in.

In practice this barely bites, because the only keys bound are F13-F22, which no
normal keyboard can produce. The exception is the num layer: while it is on, its
twelve keys are remapped on **every** keyboard, including the laptop's built-in
one. Turn the layer off and the interception stops completely.

Real per-device filtering needs a driver-level input stack (Interception, or AHK
via AutoHotInterception). That is a separate dependency with its own install and
its own failure modes, and it was judged not worth it for a ten-key surface.
This is a known limitation, not an oversight.

Two smaller ones:

- **Pause really pauses.** While hotkeys are suspended, F13-F22 are unbound and
  will reach applications as raw F13-F22 codes. Most apps ignore them.
- **Media key usage is unmeasurable.** See above.
