#Requires AutoHotkey v2.0
; ---------------------------------------------------------------------------
; hotkeynames.ahk - gate test, NOT production code.
;
; /validate proves a config is well formed. It cannot prove the hotkey names
; that config compiles to are ones AutoHotkey will actually accept, because it
; deliberately registers nothing. Punctuation keys are the risk: "," ";" "/"
; are legal key names but only if the Hotkey() name string is built correctly.
;
; This script builds every hotkey string engine.ahk would register and creates
; each one in the "Off" state. An Off hotkey validates the name and installs no
; hook, so this is safe to run in CI. Anything AutoHotkey rejects throws here
; instead of at 9am on a Monday.
;
; Usage: AutoHotkey64.exe tests\hotkeynames.ahk [config ...]
; Exits 0 on success, 1 on the first bad name.
; ---------------------------------------------------------------------------

#Include %A_LineFile%\..\..\core.ahk

Main()

Main() {
    paths := []
    if (A_Args.Length > 0) {
        for _, a in A_Args
            paths.Push(a)
    } else {
        base := A_ScriptDir "\.."
        paths.Push(base "\layers.json")
        paths.Push(base "\layers.example.json")
    }

    total := 0
    for _, p in paths {
        if (!FileExist(p)) {
            Out("FAIL " p " (not found)")
            ExitApp(1)
        }
        try {
            c := HL_LoadConfigFile(p)
        } catch as e {
            Out("FAIL " p)
            Out("  " e.Message)
            if (e.HasOwnProp("Extra"))
                Out("  " e.Extra)
            ExitApp(1)
        }

        names := []
        for k, entry in c["bindings"] {
            names.Push("$*" entry["hk"])
            names.Push("$*" entry["hk"] " up")
        }
        for k, _ in c["layerKeys"]
            names.Push("$*" HL_KeyHotkey()[k])
        names.Push("^!r")

        for _, n in names {
            try {
                Hotkey(n, NoOp, "Off")
            } catch as e {
                Out("FAIL " p)
                Out("  hotkey name rejected by AutoHotkey: '" n "' -- " e.Message)
                ExitApp(1)
            }
            total += 1
        }
        Out("ok " p " (" names.Length " hotkey names)")
    }
    Out("PASS " total " hotkey names accepted")
    ExitApp(0)
}

NoOp(*) {
}

Out(s) {
    FileAppend(s "`n", "*")
}
