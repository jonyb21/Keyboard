# services/hostlayer

The host-side layer engine for the AULA F75 Max. AutoHotkey v2, driven entirely
from `layers.json`.

The board's firmware accepted the canonical remap transaction in board memory,
so the map travels with the keyboard. That table programs physical F1, F3 and
F5-F12 as the ten tokens **F13-F15 and F18-F24**.
Those are real USB HID codes that a normal keyboard does not produce, so they
do not collide with the laptop keyboard. F16 and F17 are deliberately absent:
the live CIDOO runtime owns those two tokens globally, and AutoHotkey's hook
cannot distinguish which keyboard produced them.

This service is the only thing that gives those ten codes meaning. It adds two
capabilities the stock firmware does not have: **tap versus hold** on a single
key, and a **sticky extra layer**.

---

## What it touches

Nothing except F13-F15, F18-F24 and, while the num layer is on, twelve keys on
the right hand. That is the whole surface. The knob adds its own volume keys, but only when you remap a
direction (see "The encoder knob").

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
| F6 | F18 | Focus SolidWorks | Focus Explorer (new window if none open) | SolidWorks is 19.1% of all app switches, the single biggest target. |
| F7 | F19 | Focus Claude | Focus Remote Desktop | The two live tools on this workstation replace removed Adobe Beta installs. |
| F8 | F20 | Mute | Previous track | See "why media sits here" below. |
| F9 | F21 | Toggle the **num** layer | — | Tap-only, so it fires on key-down with no delay. |
| F10 | F22 | Paste (`Ctrl+V`) | Copy (`Ctrl+C`) | The two highest-count chords measured: paste 778 and copy 443 in 13 days. Tap gets the more frequent one. |
| F11 | F23 | Play / Pause | Next track | See below. |
| F12 | F24 | Save (`Ctrl+S`) | Save As (`Ctrl+Shift+S`) | Same gesture, escalating commitment. |

F2, F4 and Esc are untouched by the firmware remap and untouched here.

**764 app switches per day** were measured, which is why five of the ten keys are
focus-or-launch rather than chords.

### Why media sits on F20 and F23

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
| Claude | `claude.exe` | `C:\Users\jonbr\AppData\Local\AnthropicClaude\claude.exe` |
| Remote Desktop | `mstsc.exe` | `C:\Windows\System32\mstsc.exe` |

Outlook is the classic client. New Outlook (`olk.exe`) is installed but unused,
so it is not the target. Claude uses its stable per-user launcher; Remote
Desktop uses the Windows system executable.

---

## The num layer

`F21` toggles it. **Sticky**: it stays on until you tap F21 again. The tray icon
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

### Multi-tap triggers

A binding may carry five triggers, each any action kind in the table above:

| Trigger | Fires when |
|---|---|
| `tap` | one press and release (required) |
| `hold` | the first press is held past `tapHoldMs` |
| `doubleTap` | two presses, each released, with a gap under `tapWindowMs` |
| `tripleTap` | three presses likewise |
| `tapHold` | tap, then press again within `tapWindowMs` and hold past `tapHoldMs` |

```json
"F20": {
  "tap": { "send": "Ctrl+C" },
  "doubleTap": { "send": "Ctrl+V" },
  "tripleTap": { "send": "Ctrl+A" },
  "hold": { "send": "Ctrl+X" },
  "tapHold": { "send": "Ctrl+Shift+V" }
}
```

`tapWindowMs` is a top-level setting (default 250, range 50-1000), next to
`tapHoldMs`. Timing rules:

- A binding **without** `doubleTap`, `tripleTap` and `tapHold` behaves exactly
  as before: tap-only fires on key-down, tap+hold fires on key-up or at the hold
  threshold. No latency is added.
- A binding **with** any of them cannot fire `tap` on key-down. After a release
  it waits up to `tapWindowMs` for another press. The count is final, and fires,
  as soon as no further tap is possible (third tap with `tripleTap`, second tap
  with only `doubleTap`) or when the window closes.
- A missing slot falls back to `tap`: with `tripleTap` but no `doubleTap`, two
  taps fire `tap` once. A third quick press after a final count starts a new
  sequence.
- `hold` only applies to the first press and `tapHold` only to the second. A long
  first press with no `hold` is simply a tap.

### Reserved keys

`F16` and `F17` can never be bound, as a binding or as a layer key: the Cidoo V21
knob runtime owns them globally. The validator refuses them with a clear error.

Two rules the validator enforces and will not let you break:

- **`Ctrl+Alt+Shift+V` can never be bound**, anywhere, in any action or layer
  key. It belongs to InDesign's Paste in Place and a Photoshop-internal tool.
- **`space` and `capslock` can never be bound**, in bindings or in a layer.

### Config file location

The engine loads, in order:

1. a path passed on the command line (`engine.ahk C:\path\x.json`), if any;
2. `%APPDATA%\aula-desktop\hostlayer.json`, the file the desktop app writes,
   when it exists;
3. `services\hostlayer\layers.json`.

The choice is re-evaluated on every reload, so deleting the app's file falls
back to `layers.json`. `engine.ahk /validate` with no path checks whichever file
the engine would load.

### Reload

The engine polls the config file once a second (path, modified time and size).
When it changes and stays unchanged for one further poll, it reloads itself, so
the app only has to write the file; it does not need to send keys. That is a
latency of one to two seconds, and a half-written file is never read. A broken
file is reported once in a tray tip and the previous config keeps running.

`Ctrl+Alt+R` re-reads the config at once and re-registers every hotkey without
restarting the process. It works even while hotkeys are paused. If the new
config is invalid, the old one keeps running and a tray tip says what was wrong,
so a typo can never leave you with a dead keyboard.

The tray menu has **Pause hotkeys**, **Reload config**, and **Exit**.

---

## The encoder knob

The knob has no setting in the board's own protocol, so it is remapped here.
The `encoder` block in `layers.json` maps `left`, `right` and `press` to any
action kind (`send`, `byApp`, `focusApp`, `layerToggle`, `builtin`, `swallow`).
Five more optional slots take the same action kinds:

| Slot | Fires when |
|---|---|
| `doublePress`, `triplePress` | the button is pressed twice or three times inside `tapWindowMs` |
| `hold` | the button is held past `tapHoldMs` |
| `pressLeft`, `pressRight` | the knob is turned while the button is held down |

Any of these five switches the button into **press mode**: it becomes a tap-hold
key like the F-row ones (same `tapHoldMs` and `tapWindowMs`), so `press` then
fires on release or when the window closes instead of at once. With none of them
present, `press` is the original single-shot remap. If `press` is omitted in
press mode, a single press sends the stock `Volume_Mute`. A turn while the
button is held runs `pressLeft` / `pressRight` and cancels that press's own tap
and hold; turning with no matching slot behaves normally and does not cancel it.
`pressLeft` / `pressRight` hook their direction even when it is otherwise stock,
passing the key through unchanged when the button is not down.

```json
"encoder": {
  "devices": ["VID_0C45&PID_800A", "VID_05AC&PID_024F"],
  "left":  { "send": "Volume_Down" },
  "right": { "send": "Volume_Up" },
  "press": { "send": "Media_Play_Pause" }
}
```

The knob sends ordinary volume keys, the same ones the laptop keyboard sends,
so a plain hotkey would hijack every keyboard. Instead:

- A direction left at its stock key registers nothing. The shipped config is
  stock, so out of the box the engine hooks no volume key at all.
- A remapped direction blocks its key for about 30 ms while raw input reports
  which device sent it. If the device ID matches `devices` (wired or the 2.4G
  dongle), the mapped action runs. Otherwise the original key is sent on
  unchanged, so other keyboards keep working.
- Limit: matching is by time. Two keyboards pressing the same volume key within
  60 ms of each other can be confused. Exact per-device blocking would need a
  filter driver, which this deliberately does not install.

The stock codes (left `Volume_Down`, right `Volume_Up`, press `Volume_Mute`) are
assumed, not yet measured on this board. To check them, run the read-only probe
(no hotkeys, no hook, safe beside other keyboard tools) from the PC's own
desktop session (not over SSH), turn and press the knob without typing, and
read `encoder-probe.log`. It listens on the keyboard and consumer collections
only, so a knob that reported as a mouse wheel would not show up:

```powershell
& "C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe" services\hostlayer\encoder-probe.ahk
```

If the codes differ, change `HL_EncoderSources()` in `core.ahk` and
`ENCODER_SOURCES` in `tests/mirror.py`.

Press mode is **unverified on the hardware**. It assumes the knob reports a held
press as a real key-down and a later key-up. If the board only sends a
down/up pulse, `doublePress`, `triplePress` and `press` still work, but `hold`,
`pressLeft` and `pressRight` will never fire. The press is attributed to the
AULA at key-down by the same time-matching as the turns; the release follows it.

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

Install creates a hidden-launch shortcut in your Startup folder pointing at
`AutoHotkey64.exe` with `engine.ahk` as its argument. It validates the config
first and refuses to install a broken one. Both scripts are idempotent.
Uninstall leaves a running engine alone unless you pass `-StopRunning`.

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
(`TH-L1`..`TH-L12` for the tap-hold machine, `LR-L1`..`LR-L3` for the layer
resolver) so the two can be diffed side by side. **Change one, change the
other.**

### Tests

```powershell
cd services\hostlayer
py -m unittest discover -s tests
```

204 tests, no network, no device, fake clock, well under a second.
`test_shipped_focus_app_launch_paths_exist_on_disk` fails on a machine where the
Claude launcher path recorded in `layers.json` does not exist; it is a check of
the machine, not of the engine.

### Bugs this design exists to prevent

1. **F-row leakage.** A previous build registered its tokens as bare hotkeys, so
   any modified press fell through and the raw F-key reached the application;
   seven leaks were measured. Every configured token is now registered with the
   `$*` prefix on both edges, for example `$*F13` and `$*F13 up`.
   `InstallKeybdHook` is called explicitly rather than left to chance.
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

In practice this barely bites, because the only base keys bound are F13-F15 and
F18-F24, which no normal keyboard can produce. F16/F17 are excluded because the
CIDOO runtime already owns them globally. The exception is the num layer: while
it is on, its twelve keys are remapped on **every** keyboard, including the
laptop's built-in one. Turn the layer off and the interception stops completely.

Real per-device filtering needs a driver-level input stack (Interception, or AHK
via AutoHotInterception). That is a separate dependency with its own install and
its own failure modes, and it was judged not worth it for a ten-key surface.
This is a known limitation, not an oversight.

Two smaller ones:

- **Pause really pauses.** While hotkeys are suspended, F13-F15 and F18-F24 are
  unbound and will reach applications as raw F-key codes. Most apps ignore them.
- **Media key usage is unmeasurable.** See above.
