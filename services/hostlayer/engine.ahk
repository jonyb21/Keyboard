#Requires AutoHotkey v2.0
; ---------------------------------------------------------------------------
; engine.ahk - the impure half of the AULA F75 Max host layer.
;
; Owns everything core.ahk refuses to touch: hotkey registration, timers,
; Send, window activation, the tray menu, the single-instance mutex, and the
; /validate CLI mode.
;
; Usage:
;   AutoHotkey64.exe engine.ahk                       run with layers.json
;   AutoHotkey64.exe engine.ahk C:\path\layers.json   run with another config
;   AutoHotkey64.exe engine.ahk /validate [path]      check a config, exit 0/1
;
; /validate registers no hotkeys, installs no hook, and leaves no process
; behind. It is the gate tools\validate.ps1 runs.
; ---------------------------------------------------------------------------

#SingleInstance Off
#Warn All, OutputDebug

#Include %A_LineFile%\..\core.ahk

; ===========================================================================
; Globals
; ===========================================================================

global gCfg := ""                 ; compiled config
global gConfigPath := ""
global gMachine := ""             ; HL_TapHold
global gActiveToggles := Map()    ; layer name -> 1
global gRegistered := []          ; [{hk, ctx}] so reload can unregister
global gHkToKey := Map()          ; normalized hotkey name -> canonical key
global gTimerFns := Map()         ; key -> BoundFunc, stable across arm/cancel
global gAltHeld := false          ; true while altTabBrowse owns the Alt key
global gMutex := 0

; ===========================================================================
; Entry point
; ===========================================================================

Main()

Main() {
    global gConfigPath
    if (A_Args.Length >= 1 && IsValidateFlag(A_Args[1])) {
        path := (A_Args.Length >= 2 && Trim(A_Args[2]) !== "") ? A_Args[2] : A_ScriptDir "\layers.json"
        ExitApp(ValidateMain(path))
    }
    gConfigPath := (A_Args.Length >= 1 && Trim(A_Args[1]) !== "") ? A_Args[1] : A_ScriptDir "\layers.json"
    RunEngine()
}

IsValidateFlag(s) {
    t := StrLower(Trim(s))
    return (t == "/validate" || t == "--validate" || t == "-validate")
}

; ===========================================================================
; /validate
; ===========================================================================

Say(text) {
    FileAppend(text "`n", "*")
}

ValidateMain(path) {
    if (!FileExist(path)) {
        Say("FAIL " path)
        Say("  file not found")
        return 1
    }
    try {
        text := HL_ReadConfigText(path)
    } catch as e {
        Say("FAIL " path)
        Say("  cannot read file: " e.Message)
        return 1
    }
    try {
        cfg := Json.Parse(text)
    } catch as e {
        Say("FAIL " path)
        Say("  " e.Message)
        return 1
    }
    errs := HL_ValidateConfig(cfg)
    if (errs.Length > 0) {
        Say("FAIL " path)
        for _, e in errs
            Say("  " e)
        return 1
    }
    try {
        c := HL_CompileConfig(cfg)
    } catch as e {
        Say("FAIL " path)
        Say("  compile: " e.Message)
        return 1
    }
    tapOnly := 0
    for k, b in c["bindings"]
        if (!b["hasHold"])
            tapOnly += 1
    layerKeyCount := 0
    for k, _ in c["layerKeys"]
        layerKeyCount += 1
    Say("PASS " path)
    Say("  tapHoldMs      " c["tapHoldMs"])
    Say("  bindings       " c["bindings"].Count " (" tapOnly " tap-only)")
    Say("  layers         " c["layers"].Count " (" layerKeyCount " mapped keys)")
    Say("  appGroups      " c["exeIndex"].Count " exe names indexed")
    return 0
}

; ===========================================================================
; Engine startup
; ===========================================================================

RunEngine() {
    global gCfg, gMachine, gMutex, gConfigPath

    ; Correctness rule 5: one instance only. A second launch says so and dies.
    gMutex := DllCall("CreateMutexW", "Ptr", 0, "Int", 1, "Str", "AULA_F75MAX_HOSTLAYER", "Ptr")
    if (A_LastError == 183) {                       ; ERROR_ALREADY_EXISTS
        TrayTip("Already running. Use its tray icon to reload or exit.", "AULA F75 Max host layer", 0x1)
        Sleep(1800)
        ExitApp(0)
    }

    try {
        gCfg := HL_LoadConfigFile(gConfigPath)
    } catch as e {
        detail := e.Message
        if (e.HasOwnProp("Extra") && e.Extra !== "")
            detail .= "`n" e.Extra
        MsgBox("Config could not be loaded:`n" gConfigPath "`n`n" detail, "AULA F75 Max host layer", 0x10)
        ExitApp(1)
    }

    OnError(OnEngineError)
    OnExit(OnEngineExit)

    ; Correctness rule 3: GetKeyState(key, "P") and reliable key-up delivery
    ; both need the keyboard hook. Ask for it explicitly instead of hoping a
    ; hotkey variant happens to install it.
    InstallKeybdHook(true, true)

    gMachine := HL_TapHold(gCfg)
    RegisterAll(gCfg)
    RegisterControlHotkeys()
    BuildTray()
    UpdateTray()
    Persistent(true)
}

OnEngineError(err, mode) {
    ; A modal error dialog would wedge the engine and, with a keyboard hook
    ; installed, the whole machine feels broken. Report and keep running.
    try {
        msg := (Type(err) == "String") ? err : err.Message
        TrayTip(SubStr(msg, 1, 200), "AULA host layer error", 0x3)
    }
    return 1
}

OnEngineExit(reason, code) {
    ReleaseHeld()
    return 0
}

; ===========================================================================
; Registration
; ===========================================================================

RegisterAll(c) {
    global gRegistered, gHkToKey

    ; Correctness rule 1: "$*" means every modifier combination is captured and
    ; the key is swallowed. The prior build registered bare F13..F22, so any
    ; modified press fell through and the raw F-key reached the app. Both the
    ; down and the up event are claimed, or the up leaks on its own.
    for k, b in c["bindings"] {
        hk := "$*" b["hk"]
        Hotkey(hk, MakeDownHandler(k), "On")
        gRegistered.Push(Map("hk", hk, "ctx", ""))
        hku := "$*" b["hk"] " up"
        Hotkey(hku, MakeUpHandler(k), "On")
        gRegistered.Push(Map("hk", hku, "ctx", ""))
    }

    ; Layer keys are context-sensitive: the criterion is false whenever no
    ; sticky layer claims that key, so with the num layer off these keys are
    ; never intercepted and typing is untouched.
    HotIf(LayerCtx)
    for k, _ in c["layerKeys"] {
        name := HL_KeyHotkey()[k]
        gHkToKey[NormalizeHk(name)] := k
        hk := "$*" name
        Hotkey(hk, MakeLayerHandler(k), "On")
        gRegistered.Push(Map("hk", hk, "ctx", LayerCtx))
    }
    HotIf()
}

RegisterControlHotkeys() {
    global gRegistered
    HotIf()
    ; Suspend-exempt so Ctrl+Alt+R still reloads while hotkeys are paused.
    try {
        Hotkey("^!r", ReloadConfig, "On S")
    } catch {
        Hotkey("^!r", ReloadConfig, "On")
    }
    gRegistered.Push(Map("hk", "^!r", "ctx", ""))
}

UnregisterAll() {
    global gRegistered
    for _, r in gRegistered {
        try {
            if (r["ctx"] !== "")
                HotIf(r["ctx"])
            else
                HotIf()
            Hotkey(r["hk"], , "Off")
        }
    }
    HotIf()
    gRegistered := []
}

NormalizeHk(name) {
    s := name
    while (StrLen(s) > 0 && InStr("$*~!^+#", SubStr(s, 1, 1)))
        s := SubStr(s, 2)
    if (StrLen(s) > 3 && StrLower(SubStr(s, -3)) == " up")
        s := SubStr(s, 1, StrLen(s) - 3)
    return s
}

; HotIf criterion for every layer key. Receives the hotkey name.
LayerCtx(hkName) {
    global gCfg, gActiveToggles, gHkToKey
    if (gActiveToggles.Count == 0)
        return false
    n := NormalizeHk(hkName)
    if (!gHkToKey.Has(n))
        return false
    key := gHkToKey[n]
    for _, ln in gCfg["toggleLayers"]
        if (gActiveToggles.Has(ln) && gCfg["layers"][ln]["keys"].Has(key))
            return true
    return false
}

; Closure factories. A closure built inside a loop would capture the shared
; loop variable; a factory parameter gives each handler its own binding.
MakeDownHandler(key) {
    return (*) => OnKeyDown(key)
}

MakeUpHandler(key) {
    return (*) => OnKeyUp(key)
}

MakeLayerHandler(key) {
    return (*) => OnLayerKey(key)
}

; ===========================================================================
; Event plumbing
; ===========================================================================

OnKeyDown(key) {
    global gMachine
    ExecEvents(gMachine.OnDown(key, A_TickCount))
}

OnKeyUp(key) {
    global gMachine
    ExecEvents(gMachine.OnUp(key, A_TickCount))
}

OnHoldTimer(key) {
    global gMachine
    ExecEvents(gMachine.OnHoldTimer(key, A_TickCount))
}

TimerFn(key) {
    global gTimerFns
    if (!gTimerFns.Has(key))
        gTimerFns[key] := OnHoldTimer.Bind(key)
    return gTimerFns[key]
}

ExecEvents(evs) {
    for _, ev in evs {
        t := ev["type"]
        if (t == "armTimer")
            SetTimer(TimerFn(ev["key"]), -ev["ms"])
        else if (t == "cancelTimer")
            SetTimer(TimerFn(ev["key"]), 0)
        else if (t == "builtin")
            DoBuiltin(ev["name"], ev["phase"])
        else if (t == "action")
            DoAction(ev["action"])
    }
}

DoAction(a) {
    global gCfg
    a := HL_ResolveApp(a, ActiveExe(), gCfg["exeIndex"])
    k := a["kind"]
    if (k == "swallow")
        return
    if (k == "layerToggle") {
        ToggleLayer(a["layer"])
        return
    }
    if (k == "builtin") {
        DoBuiltin(a["builtin"], "tap")
        return
    }
    if (k == "focusApp") {
        FocusApp(a)
        return
    }
    if (k == "send")
        Send((a["blind"] ? "{Blind}" : "") . a["send"])
}

ActiveExe() {
    try
        return WinGetProcessName("A")
    catch
        return ""
}

DoBuiltin(name, phase) {
    global gAltHeld
    if (name == "altTabTap") {
        Send("!{Tab}")
        return
    }
    if (name == "altTabBrowse") {
        if (phase == "start") {
            Send("{Alt down}{Tab}")
            gAltHeld := true
        } else if (phase == "repeat") {
            if (!gAltHeld) {
                Send("{Alt down}")
                gAltHeld := true
            }
            Send("{Tab}")
        } else if (phase == "end") {
            ReleaseHeld()
        }
    }
}

ReleaseHeld() {
    global gAltHeld
    if (gAltHeld) {
        try Send("{Alt up}")
        gAltHeld := false
    }
}

; ---------------------------------------------------------------------------
; focus-or-launch
;
; 1. A matching window exists -> activate it. If the active window is already
;    one of them, advance to the next, so repeated presses cycle that app's
;    windows.
; 2. No matching window -> run the launch path.
; 3. No launch path -> do nothing (focus-only binding).
;
; windowClass matters for explorer.exe: the shell process always runs and owns
; the desktop and taskbar, so "ahk_exe explorer.exe" would always match and a
; new file browser window would never open.
; ---------------------------------------------------------------------------
FocusApp(a) {
    exe := a["exe"]
    cls := a["windowClass"]
    launch := Trim(a["launch"])
    args := Trim(a["args"])

    crit := (cls !== "") ? ("ahk_class " cls " ahk_exe " exe) : ("ahk_exe " exe)

    DetectHiddenWindows(false)
    list := []
    try list := WinGetList(crit)

    if (list.Length > 0) {
        act := 0
        try act := WinExist("A")
        idx := 0
        for i, id in list {
            if (id == act) {
                idx := i
                break
            }
        }
        target := (idx > 0) ? list[Mod(idx, list.Length) + 1] : list[1]
        try {
            WinActivate("ahk_id " target)
            return
        }
    }

    if (launch !== "") {
        try {
            Run((args !== "") ? ('"' launch '" ' args) : launch)
        } catch as e {
            TrayTip("Could not launch " exe ": " e.Message, "AULA host layer", 0x3)
        }
        return
    }

    TrayTip(exe " is not running and has no launch path configured.", "AULA host layer", 0x1)
}

; ===========================================================================
; Layers
; ===========================================================================

OnLayerKey(key) {
    global gCfg, gActiveToggles
    r := HL_ResolveLayerKey(gCfg, key, {activeToggles: gActiveToggles})
    Send((r["blind"] ? "{Blind}" : "") . r["send"])
}

ToggleLayer(name) {
    global gActiveToggles
    if (gActiveToggles.Has(name))
        gActiveToggles.Delete(name)
    else
        gActiveToggles[name] := 1
    UpdateTray()
    ; A sticky layer with no feedback is a trap, so say which way it went.
    TrayTip("layer '" name "' " (gActiveToggles.Has(name) ? "ON" : "off"), "AULA host layer", 0x1)
    SetTimer(HideTip, -900)
}

HideTip() {
    try TrayTip()
}

; ===========================================================================
; Tray
; ===========================================================================

BuildTray() {
    A_TrayMenu.Delete()
    A_TrayMenu.Add("Pause hotkeys", TogglePause)
    A_TrayMenu.Add("Reload config" . A_Tab . "Ctrl+Alt+R", ReloadConfig)
    A_TrayMenu.Add()
    A_TrayMenu.Add("Exit", ExitEngine)
    A_TrayMenu.Default := "Pause hotkeys"
}

TogglePause(*) {
    global gMachine
    Suspend(-1)
    ; Suspending mid-hold would strand Alt down and reset nothing.
    ReleaseHeld()
    gMachine.Reset()
    UpdateTray()
}

ExitEngine(*) {
    ExitApp(0)
}

ReloadConfig(*) {
    global gCfg, gMachine, gConfigPath, gHkToKey, gTimerFns, gActiveToggles
    try {
        nc := HL_LoadConfigFile(gConfigPath)
    } catch as e {
        detail := e.Message
        if (e.HasOwnProp("Extra") && e.Extra !== "")
            detail .= ": " StrReplace(e.Extra, "`n", " | ")
        TrayTip("Config NOT reloaded. " SubStr(detail, 1, 180), "AULA host layer", 0x3)
        return
    }
    ReleaseHeld()
    UnregisterAll()
    gCfg := nc
    gMachine := HL_TapHold(nc)
    gTimerFns := Map()
    gHkToKey := Map()
    gActiveToggles := Map()
    RegisterAll(nc)
    RegisterControlHotkeys()
    UpdateTray()
    TrayTip("Config reloaded.", "AULA host layer", 0x1)
    SetTimer(HideTip, -900)
}

UpdateTray() {
    global gCfg, gActiveToggles
    on := ""
    for _, ln in gCfg["toggleLayers"]
        if (gActiveToggles.Has(ln))
            on .= (on == "" ? "" : ", ") . ln
    tip := "AULA F75 Max host layer"
    tip .= "`n" (A_IsSuspended ? "PAUSED" : "active")
    tip .= "`nlayers: " (on == "" ? "none" : on)
    A_IconTip := SubStr(tip, 1, 126)
    try {
        if (A_IsSuspended)
            A_TrayMenu.Check("Pause hotkeys")
        else
            A_TrayMenu.Uncheck("Pause hotkeys")
    }
}
