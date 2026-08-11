#Requires AutoHotkey v2.0
; ---------------------------------------------------------------------------
; core.ahk - PURE logic for the AULA F75 Max host layer.
;
; Contains: config reading/parsing, schema validation, chord compilation,
; app resolution, the tap-hold state machine, and layer mapping resolution.
;
; Contains NO: hotkey registration, GUI, tray, Send, timers, file writes.
; Nothing here runs at load time except building lookup tables. This file is
; #Include-able from anywhere and is the unit the Python mirror suite ports.
;
; Scope note: this engine only ever touches F13-F22 (the codes the board's own
; firmware emits from its F-row) plus the keys of the F19 sticky num layer.
; Space, CapsLock and every other key are protected by HL_ProtectedKeys() and
; the validator refuses to bind them.
;
; The tap-hold state machine below carries step labels (TH-L1 .. TH-L8) and the
; layer resolver carries LR-L1 .. LR-L3. The Python mirror in tests/mirror.py
; references those exact labels so the two implementations can be diffed line
; for line.
; ---------------------------------------------------------------------------

#Include %A_LineFile%\..\lib\jsonparse.ahk

; ===========================================================================
; Lookup tables
; ===========================================================================

; Canonical modifier token -> canonical name.
HL_ModCanon() {
    static m := ""
    if (IsObject(m))
        return m
    m := Map()
    m.CaseSense := "Off"
    m["ctrl"] := "Ctrl", m["control"] := "Ctrl"
    m["alt"] := "Alt"
    m["shift"] := "Shift"
    m["win"] := "Win", m["super"] := "Win", m["lwin"] := "Win"
    return m
}

; Canonical modifier name -> AutoHotkey Send prefix character.
HL_ModPrefix() {
    static m := ""
    if (IsObject(m))
        return m
    m := Map()
    m.CaseSense := "Off"
    m["Ctrl"] := "^", m["Alt"] := "!", m["Shift"] := "+", m["Win"] := "#"
    return m
}

; Modifier emission order. Fixed so a chord always compiles to one string.
HL_ModOrder() {
    return ["Ctrl", "Alt", "Shift", "Win"]
}

; Keys this engine must never bind, in bindings or in a layer. Space and
; CapsLock stay stock by explicit product decision; the guard makes that
; structural instead of a comment somebody can ignore.
HL_ProtectedKeys() {
    static m := ""
    if (IsObject(m))
        return m
    m := Map()
    m.CaseSense := "Off"
    m["space"] := "Space must stay a plain Space key"
    m["capslock"] := "CapsLock must stay stock"
    return m
}

; Canonical key token -> AutoHotkey Send token (already brace-wrapped).
HL_KeySend() {
    static m := ""
    if (IsObject(m))
        return m
    m := Map()
    m.CaseSense := "Off"
    loop 26
        m[Chr(96 + A_Index)] := "{" Chr(96 + A_Index) "}"          ; a .. z
    loop 10
        m[Chr(47 + A_Index)] := "{" Chr(47 + A_Index) "}"          ; 0 .. 9
    loop 24
        m["f" A_Index] := "{F" A_Index "}"                          ; F1 .. F24
    m["esc"] := "{Escape}", m["escape"] := "{Escape}"
    m["enter"] := "{Enter}", m["return"] := "{Enter}"
    m["tab"] := "{Tab}"
    m["space"] := "{Space}"
    ; CapsLock is never bindable (HL_ProtectedKeys), but the layer resolver
    ; looks up a Send token for protected keys, so the table must be total.
    m["capslock"] := "{CapsLock}"
    m["backspace"] := "{Backspace}", m["bs"] := "{Backspace}"
    m["delete"] := "{Delete}", m["del"] := "{Delete}"
    m["insert"] := "{Insert}", m["ins"] := "{Insert}"
    m["up"] := "{Up}", m["down"] := "{Down}", m["left"] := "{Left}", m["right"] := "{Right}"
    m["home"] := "{Home}", m["end"] := "{End}"
    m["pgup"] := "{PgUp}", m["pageup"] := "{PgUp}"
    m["pgdn"] := "{PgDn}", m["pagedown"] := "{PgDn}"
    m["comma"] := "{,}"
    m["period"] := "{.}", m["dot"] := "{.}"
    m["semicolon"] := "{" Chr(59) "}"
    m["slash"] := "{/}"
    m["minus"] := "{-}", m["equals"] := "{=}"
    m["quote"] := "{'}"
    m["lbracket"] := "{[}", m["rbracket"] := "{]}"
    m["backslash"] := "{\}"
    m["printscreen"] := "{PrintScreen}"
    m["appskey"] := "{AppsKey}"
    m["volume_mute"] := "{Volume_Mute}"
    m["volume_up"] := "{Volume_Up}", m["volume_down"] := "{Volume_Down}"
    m["media_prev"] := "{Media_Prev}", m["media_next"] := "{Media_Next}"
    m["media_play_pause"] := "{Media_Play_Pause}", m["media_stop"] := "{Media_Stop}"
    loop 10
        m["numpad" (A_Index - 1)] := "{Numpad" (A_Index - 1) "}"
    return m
}

; Canonical key token -> AutoHotkey *hotkey* name (what you register).
; Distinct from Send tokens: hotkeys are bare names, never brace-wrapped.
HL_KeyHotkey() {
    static m := ""
    if (IsObject(m))
        return m
    m := Map()
    m.CaseSense := "Off"
    loop 26
        m[Chr(96 + A_Index)] := Chr(96 + A_Index)
    loop 10
        m[Chr(47 + A_Index)] := Chr(47 + A_Index)
    loop 24
        m["f" A_Index] := "F" A_Index
    m["capslock"] := "CapsLock"
    m["space"] := "Space"
    m["tab"] := "Tab"
    m["enter"] := "Enter"
    m["esc"] := "Escape", m["escape"] := "Escape"
    m["backspace"] := "Backspace"
    m["comma"] := ","
    m["period"] := "."
    m["semicolon"] := Chr(59)
    m["slash"] := "/"
    m["minus"] := "-", m["equals"] := "="
    m["quote"] := "'"
    m["lbracket"] := "[", m["rbracket"] := "]"
    m["backslash"] := "\"
    m["up"] := "Up", m["down"] := "Down", m["left"] := "Left", m["right"] := "Right"
    m["home"] := "Home", m["end"] := "End"
    m["pgup"] := "PgUp", m["pgdn"] := "PgDn"
    m["delete"] := "Delete", m["insert"] := "Insert"
    return m
}

HL_Builtins() {
    static m := ""
    if (IsObject(m))
        return m
    m := Map()
    m.CaseSense := "Off"
    m["altTabTap"] := 1, m["altTabBrowse"] := 1
    return m
}

; ===========================================================================
; Chord parsing / compilation
; ===========================================================================

; HL_ParseChord("Ctrl+Shift+Z") ->
;   Map("mods", Map("Ctrl",1,"Shift",1), "key", "z", "send", "^+{z}", "text", ...)
; Throws Error on an unknown modifier or key.
HL_ParseChord(text) {
    if (Type(text) !== "String" || Trim(text) == "")
        throw Error("chord must be a non-empty string")
    parts := StrSplit(text, "+")
    mods := Map()
    mods.CaseSense := "Off"
    keyTok := Trim(parts[parts.Length])
    canon := HL_ModCanon()
    loop parts.Length - 1 {
        raw := Trim(parts[A_Index])
        if (!canon.Has(raw))
            throw Error("unknown modifier '" raw "' in chord '" text "'")
        mods[canon[raw]] := 1
    }
    ks := HL_KeySend()
    if (!ks.Has(keyTok))
        throw Error("unknown key '" keyTok "' in chord '" text "'")
    prefix := ""
    for _, mname in HL_ModOrder()
        if (mods.Has(mname))
            prefix .= HL_ModPrefix()[mname]
    return Map("mods", mods, "key", StrLower(keyTok), "send", prefix ks[keyTok], "text", text)
}

; The one chord this engine must never emit or intercept: Ctrl+Alt+Shift+V is
; owned by InDesign (Paste in Place) and a Photoshop-internal tool.
HL_IsForbiddenChord(parsed) {
    mods := parsed["mods"]
    if (parsed["key"] !== "v")
        return false
    if (!mods.Has("Ctrl") || !mods.Has("Alt") || !mods.Has("Shift"))
        return false
    if (mods.Has("Win"))
        return false
    return true
}

; ===========================================================================
; Config reading
; ===========================================================================

; Reads a config file as UTF-8 and strips a leading BOM. Correctness rule 4:
; a BOM in front of "{" makes every naive JSON parser fail.
HL_ReadConfigText(path) {
    text := FileRead(path, "UTF-8")
    if (SubStr(text, 1, 1) == Chr(0xFEFF))
        text := SubStr(text, 2)
    return text
}

; ===========================================================================
; Validation
; ===========================================================================

HL_KeysOf(m) {
    out := []
    for k, _ in m
        out.Push(k)
    ; Map iteration order is not specified; sort so error output is stable
    return HL_SortStrings(out)
}

HL_SortStrings(arr) {
    i := 2
    while (i <= arr.Length) {
        v := arr[i]
        j := i - 1
        while (j >= 1 && StrCompare(arr[j], v) > 0) {
            arr[j + 1] := arr[j]
            j -= 1
        }
        arr[j + 1] := v
        i += 1
    }
    return arr
}

HL_CheckUnknownKeys(m, allowed, where, errs) {
    for _, k in HL_KeysOf(m) {
        ok := false
        for _, a in allowed {
            if (k == a) {
                ok := true
                break
            }
        }
        if (!ok)
            errs.Push(where ": unknown key '" k "'")
    }
}

HL_IsMap(v) {
    return (v is Map)
}

HL_IsArray(v) {
    return (v is Array)
}

HL_IsBoolish(v) {
    return (Type(v) == "Integer") && (v == 0 || v == 1)
}

HL_EndsWithExe(s) {
    ; SubStr with a negative start counts back from the end: -4 is the last
    ; four characters, which is what ".exe" needs.
    return StrLower(SubStr(s, -4)) == ".exe"
}

HL_LooksAbsolute(s) {
    if (StrLen(s) >= 3 && SubStr(s, 2, 1) == ":" && (SubStr(s, 3, 1) == "\" || SubStr(s, 3, 1) == "/"))
        return true
    if (SubStr(s, 1, 2) == "\\")
        return true
    return false
}

; Validates one action node, appending to errs.
; allowNested = false forbids a byApp inside a byApp branch.
HL_ValidateAction(a, where, cfg, errs, allowNested := true) {
    if (!HL_IsMap(a)) {
        errs.Push(where ": action must be an object")
        return
    }
    HL_CheckUnknownKeys(a, ["send", "swallow", "layerToggle", "builtin", "byApp", "focusApp", "blind", "note"], where, errs)
    kinds := 0
    for _, k in ["send", "swallow", "layerToggle", "builtin", "byApp", "focusApp"]
        if (a.Has(k))
            kinds += 1
    if (kinds !== 1) {
        errs.Push(where ": exactly one of send/swallow/layerToggle/builtin/byApp/focusApp is required, found " kinds)
        return
    }
    if (a.Has("blind") && !HL_IsBoolish(a["blind"]))
        errs.Push(where ".blind: must be true or false")
    if (a.Has("send")) {
        try {
            p := HL_ParseChord(a["send"])
            if (HL_IsForbiddenChord(p))
                errs.Push(where ".send: Ctrl+Alt+Shift+V is reserved by InDesign Paste in Place and must never be bound")
        } catch as e {
            errs.Push(where ".send: " e.Message)
        }
    }
    if (a.Has("swallow") && !HL_IsBoolish(a["swallow"]))
        errs.Push(where ".swallow: must be true or false")
    if (a.Has("builtin")) {
        if (!HL_Builtins().Has(a["builtin"]))
            errs.Push(where ".builtin: unknown builtin '" a["builtin"] "'")
    }
    if (a.Has("layerToggle")) {
        ln := a["layerToggle"]
        if (!cfg.Has("layers") || !HL_IsMap(cfg["layers"]) || !cfg["layers"].Has(ln))
            errs.Push(where ".layerToggle: no layer named '" ln "'")
        else if (!HL_IsMap(cfg["layers"][ln]) || cfg["layers"][ln].Get("type", "") !== "toggle")
            errs.Push(where ".layerToggle: layer '" ln "' is not of type 'toggle'")
    }
    if (a.Has("focusApp")) {
        f := a["focusApp"]
        if (!HL_IsMap(f)) {
            errs.Push(where ".focusApp: must be an object")
        } else {
            HL_CheckUnknownKeys(f, ["exe", "launch", "windowClass", "args", "note"], where ".focusApp", errs)
            if (!f.Has("exe") || Type(f["exe"]) !== "String" || Trim(f["exe"]) == "")
                errs.Push(where ".focusApp.exe: required, must be a non-empty exe name")
            else if (!HL_EndsWithExe(f["exe"]))
                errs.Push(where ".focusApp.exe: '" f["exe"] "' must end in .exe")
            if (f.Has("launch")) {
                if (Type(f["launch"]) !== "String")
                    errs.Push(where ".focusApp.launch: must be a string (empty means focus-only)")
                else if (Trim(f["launch"]) !== "" && !HL_LooksAbsolute(f["launch"]))
                    errs.Push(where ".focusApp.launch: '" f["launch"] "' must be an absolute path")
                else if (Trim(f["launch"]) !== "" && !HL_EndsWithExe(Trim(f["launch"])))
                    errs.Push(where ".focusApp.launch: '" f["launch"] "' must point at an .exe")
            }
            if (f.Has("windowClass") && Type(f["windowClass"]) !== "String")
                errs.Push(where ".focusApp.windowClass: must be a string")
            if (f.Has("args") && Type(f["args"]) !== "String")
                errs.Push(where ".focusApp.args: must be a string")
        }
    }
    if (a.Has("byApp")) {
        if (!allowNested) {
            errs.Push(where ".byApp: byApp cannot be nested inside byApp")
            return
        }
        b := a["byApp"]
        if (!HL_IsMap(b)) {
            errs.Push(where ".byApp: must be an object")
            return
        }
        if (!b.Has("default"))
            errs.Push(where ".byApp: a 'default' branch is required")
        for _, g in HL_KeysOf(b) {
            if (g !== "default") {
                if (!cfg.Has("appGroups") || !HL_IsMap(cfg["appGroups"]) || !cfg["appGroups"].Has(g))
                    errs.Push(where ".byApp: '" g "' is not a declared appGroup")
            }
            HL_ValidateAction(b[g], where ".byApp." g, cfg, errs, false)
        }
    }
}

; HL_ValidateConfig(cfg) -> Array of human-readable error strings (empty = OK).
HL_ValidateConfig(cfg) {
    errs := []
    if (!HL_IsMap(cfg)) {
        errs.Push("root: config must be a JSON object")
        return errs
    }
    HL_CheckUnknownKeys(cfg, ["schemaVersion", "description", "tapHoldMs", "appGroups", "bindings", "layers"], "root", errs)

    if (!cfg.Has("schemaVersion") || cfg["schemaVersion"] !== 1)
        errs.Push("root.schemaVersion: must be 1")

    if (!cfg.Has("tapHoldMs")) {
        errs.Push("root.tapHoldMs: required")
    } else {
        t := cfg["tapHoldMs"]
        if (Type(t) !== "Integer")
            errs.Push("root.tapHoldMs: must be an integer number of milliseconds")
        else if (t < 50 || t > 1000)
            errs.Push("root.tapHoldMs: must be between 50 and 1000 (got " t ")")
    }

    ; ---- appGroups -------------------------------------------------------
    seenExe := Map()
    seenExe.CaseSense := "Off"
    if (cfg.Has("appGroups")) {
        if (!HL_IsMap(cfg["appGroups"])) {
            errs.Push("root.appGroups: must be an object")
        } else {
            for _, g in HL_KeysOf(cfg["appGroups"]) {
                lst := cfg["appGroups"][g]
                if (!HL_IsArray(lst)) {
                    errs.Push("appGroups." g ": must be an array of exe names")
                    continue
                }
                if (lst.Length == 0)
                    errs.Push("appGroups." g ": must list at least one exe name")
                for _, exe in lst {
                    if (Type(exe) !== "String" || !HL_EndsWithExe(exe)) {
                        errs.Push("appGroups." g ": entries must be exe names ending in .exe")
                        continue
                    }
                    if (seenExe.Has(exe))
                        errs.Push("appGroups." g ": '" exe "' is already claimed by group '" seenExe[exe] "'")
                    else
                        seenExe[exe] := g
                }
            }
        }
    }

    ; ---- layers ----------------------------------------------------------
    if (!cfg.Has("layers") || !HL_IsMap(cfg["layers"])) {
        errs.Push("root.layers: required object")
    } else {
        for _, ln in HL_KeysOf(cfg["layers"]) {
            L := cfg["layers"][ln]
            if (!HL_IsMap(L)) {
                errs.Push("layers." ln ": must be an object")
                continue
            }
            HL_CheckUnknownKeys(L, ["type", "blind", "keys", "note"], "layers." ln, errs)
            if (L.Get("type", "") !== "toggle")
                errs.Push("layers." ln ".type: must be 'toggle'")
            if (L.Has("blind") && !HL_IsBoolish(L["blind"]))
                errs.Push("layers." ln ".blind: must be true or false")
            if (!L.Has("keys") || !HL_IsMap(L["keys"])) {
                errs.Push("layers." ln ".keys: required object")
                continue
            }
            if (L["keys"].Count == 0)
                errs.Push("layers." ln ".keys: must map at least one key")
            for _, k in HL_KeysOf(L["keys"]) {
                if (HL_ProtectedKeys().Has(k)) {
                    errs.Push("layers." ln ".keys: '" k "' is protected -- " HL_ProtectedKeys()[k])
                    continue
                }
                if (!HL_KeyHotkey().Has(k)) {
                    errs.Push("layers." ln ".keys: '" k "' is not a bindable physical key name")
                    continue
                }
                try {
                    p := HL_ParseChord(L["keys"][k])
                    if (HL_IsForbiddenChord(p))
                        errs.Push("layers." ln ".keys." k ": Ctrl+Alt+Shift+V is reserved and must never be bound")
                } catch as e {
                    errs.Push("layers." ln ".keys." k ": " e.Message)
                }
            }
        }
    }

    ; ---- bindings --------------------------------------------------------
    if (!cfg.Has("bindings") || !HL_IsMap(cfg["bindings"])) {
        errs.Push("root.bindings: required object")
    } else {
        if (cfg["bindings"].Count == 0)
            errs.Push("root.bindings: must define at least one binding")
        for _, k in HL_KeysOf(cfg["bindings"]) {
            B := cfg["bindings"][k]
            where := "bindings." k
            if (HL_ProtectedKeys().Has(k))
                errs.Push(where ": '" k "' is protected -- " HL_ProtectedKeys()[k])
            else if (!HL_KeyHotkey().Has(k))
                errs.Push(where ": '" k "' is not a bindable physical key name")
            if (!HL_IsMap(B)) {
                errs.Push(where ": must be an object")
                continue
            }
            HL_CheckUnknownKeys(B, ["note", "tap", "hold"], where, errs)
            if (!B.Has("tap"))
                errs.Push(where ".tap: required")
            else
                HL_ValidateAction(B["tap"], where ".tap", cfg, errs)
            if (B.Has("hold"))
                HL_ValidateAction(B["hold"], where ".hold", cfg, errs)
        }
    }

    return errs
}

; ===========================================================================
; Compilation (assumes HL_ValidateConfig returned no errors)
; ===========================================================================

HL_CompileAction(a) {
    out := Map()
    out["blind"] := a.Has("blind") ? (a["blind"] ? 1 : 0) : 0
    if (a.Has("send")) {
        out["kind"] := "send"
        out["send"] := HL_ParseChord(a["send"])["send"]
        out["chord"] := a["send"]
    } else if (a.Has("swallow")) {
        out["kind"] := "swallow"
    } else if (a.Has("layerToggle")) {
        out["kind"] := "layerToggle"
        out["layer"] := a["layerToggle"]
    } else if (a.Has("builtin")) {
        out["kind"] := "builtin"
        out["builtin"] := a["builtin"]
    } else if (a.Has("focusApp")) {
        out["kind"] := "focusApp"
        f := a["focusApp"]
        out["exe"] := f["exe"]
        out["launch"] := f.Get("launch", "")
        out["windowClass"] := f.Get("windowClass", "")
        out["args"] := f.Get("args", "")
    } else if (a.Has("byApp")) {
        out["kind"] := "byApp"
        br := Map()
        for g, sub in a["byApp"]
            br[g] := HL_CompileAction(sub)
        out["branches"] := br
    }
    return out
}

HL_CompileConfig(cfg) {
    c := Map()
    c["tapHoldMs"] := cfg["tapHoldMs"]

    exeIndex := Map()
    exeIndex.CaseSense := "Off"
    if (cfg.Has("appGroups"))
        for g, lst in cfg["appGroups"]
            for _, exe in lst
                exeIndex[exe] := g
    c["exeIndex"] := exeIndex

    layers := Map()
    for ln, L in cfg["layers"] {
        keys := Map()
        keys.CaseSense := "Off"
        for k, chord in L["keys"]
            keys[k] := HL_ParseChord(chord)["send"]
        layers[ln] := Map("type", L["type"], "blind", L.Has("blind") ? (L["blind"] ? 1 : 0) : 0, "keys", keys)
    }
    c["layers"] := layers

    bindings := Map()
    bindings.CaseSense := "Off"
    ; NOTE: AHK variable names are case-insensitive, so a loop variable "B"
    ; and a local "b" are the same variable. Keep these names distinct.
    for k, src in cfg["bindings"] {
        entry := Map()
        entry["key"] := k
        entry["hk"] := HL_KeyHotkey()[k]
        entry["tap"] := HL_CompileAction(src["tap"])
        entry["hasHold"] := src.Has("hold") ? 1 : 0
        entry["hold"] := src.Has("hold") ? HL_CompileAction(src["hold"]) : ""
        entry["note"] := src.Get("note", "")
        bindings[k] := entry
    }
    c["bindings"] := bindings

    ; Every physical key the layer engine must intercept.
    layerKeys := Map()
    layerKeys.CaseSense := "Off"
    for ln, L in c["layers"]
        for k, _ in L["keys"]
            layerKeys[k] := 1
    c["layerKeys"] := layerKeys

    ; Raw pass-through Send token per intercepted key.
    raw := Map()
    raw.CaseSense := "Off"
    for k, _ in layerKeys
        raw[k] := HL_KeySend()[k]
    c["rawSend"] := raw

    toggles := []
    for ln, L in c["layers"]
        toggles.Push(ln)
    c["toggleLayers"] := HL_SortStrings(toggles)

    return c
}

; Read + parse + validate + compile. Throws an Error whose .Extra holds the
; joined validation errors when the config is structurally wrong.
HL_LoadConfigFile(path) {
    text := HL_ReadConfigText(path)
    cfg := Json.Parse(text)
    errs := HL_ValidateConfig(cfg)
    if (errs.Length > 0) {
        msg := ""
        for _, e in errs
            msg .= (msg == "" ? "" : "`n") . e
        err := Error("config validation failed")
        err.Extra := msg
        throw err
    }
    return HL_CompileConfig(cfg)
}

; ===========================================================================
; App resolution
; ===========================================================================

; Collapses a byApp action down to the branch matching the foreground exe.
; Deterministic: exe -> group is a precomputed 1:1 index, so branch order in
; the JSON never affects the outcome.
HL_ResolveApp(action, activeExe, exeIndex) {
    if (action["kind"] !== "byApp")
        return action
    br := action["branches"]
    if (activeExe !== "" && exeIndex.Has(activeExe)) {
        g := exeIndex[activeExe]
        if (br.Has(g))
            return br[g]
    }
    return br["default"]
}

; ===========================================================================
; Tap-hold state machine  (mirrored line-for-line in tests/mirror.py)
;
; Phases: "idle"      -> key is not down
;         "pending"   -> down, threshold not yet elapsed, undecided
;         "held"      -> hold action already fired
;         "downfired" -> tap-only key already fired on key-down
;
; Emitted events (the engine executes them; core never touches the keyboard):
;   Map("type","action",     "action",<compiled>, "phase","tap"|"hold")
;   Map("type","builtin",    "name",<str>, "phase","start"|"repeat"|"end")
;   Map("type","armTimer",   "key",<k>, "ms",<n>)
;   Map("type","cancelTimer","key",<k>)
; ===========================================================================

class HL_TapHold {
    __New(compiled) {
        this.cfg := compiled
        this.tapHoldMs := compiled["tapHoldMs"]
        this.state := Map()
        this.state.CaseSense := "Off"
        for k, _ in compiled["bindings"]
            this.state[k] := Map("phase", "idle", "downAt", 0)
    }

    Phase(key) {
        return this.state[key]["phase"]
    }

    Reset() {
        for k, _ in this.state {
            this.state[k]["phase"] := "idle"
            this.state[k]["downAt"] := 0
        }
    }

    _IsBrowse(action) {
        return (action !== "" && action["kind"] == "builtin" && action["builtin"] == "altTabBrowse")
    }

    ; TH-L1 .. TH-L4
    OnDown(key, now) {
        ev := []
        b := this.cfg["bindings"][key]
        st := this.state[key]

        ; TH-L1  Already resolved as a hold. The only thing a further key-down
        ;        may do is advance a hold-to-browse switcher. Never re-fire tap.
        if (st["phase"] == "held") {
            if (this._IsBrowse(b["hold"]))
                ev.Push(Map("type", "builtin", "name", "altTabBrowse", "phase", "repeat"))
            return ev
        }

        ; TH-L2  Autorepeat arriving while still undecided, or after a tap-only
        ;        key already fired. Swallow it: correctness rule 2, no
        ;        autorepeat of the tap action.
        if (st["phase"] == "pending" || st["phase"] == "downfired")
            return ev

        ; TH-L3  Fresh press of a tap-only key: fire immediately on key-down,
        ;        no discrimination delay at all.
        st["downAt"] := now
        if (!b["hasHold"]) {
            st["phase"] := "downfired"
            ev.Push(Map("type", "action", "action", b["tap"], "phase", "tap"))
            return ev
        }

        ; TH-L4  Fresh press of a tap-hold key: nothing is emitted yet, arm the
        ;        discriminator.
        st["phase"] := "pending"
        ev.Push(Map("type", "armTimer", "key", key, "ms", this.tapHoldMs))
        return ev
    }

    ; TH-L5 .. TH-L6
    OnHoldTimer(key, now) {
        ev := []
        b := this.cfg["bindings"][key]
        st := this.state[key]

        ; TH-L5  A cancelled timer can still land (a one-shot already in
        ;        flight). Only a key still pending may become a hold.
        if (st["phase"] !== "pending")
            return ev

        ; TH-L6  Threshold crossed: the hold fires exactly once.
        st["phase"] := "held"
        if (this._IsBrowse(b["hold"]))
            ev.Push(Map("type", "builtin", "name", "altTabBrowse", "phase", "start"))
        else
            ev.Push(Map("type", "action", "action", b["hold"], "phase", "hold"))
        return ev
    }

    ; TH-L7 .. TH-L8
    OnUp(key, now) {
        ev := []
        b := this.cfg["bindings"][key]
        st := this.state[key]

        ; TH-L7  Released before the threshold: it was a tap. Kill the pending
        ;        timer first so the hold can never also fire.
        if (st["phase"] == "pending") {
            ev.Push(Map("type", "cancelTimer", "key", key))
            ev.Push(Map("type", "action", "action", b["tap"], "phase", "tap"))
        } else if (st["phase"] == "held") {
            ; TH-L8  Hold already fired. Only a browse-style hold has a release
            ;        phase (it must let go of Alt to commit the selection).
            if (this._IsBrowse(b["hold"]))
                ev.Push(Map("type", "builtin", "name", "altTabBrowse", "phase", "end"))
        }
        st["phase"] := "idle"
        return ev
    }
}

; ===========================================================================
; Layer mapping resolution  (mirrored in tests/mirror.py)
;
; Precedence: active toggle layers (alphabetical) > base pass-through.
;
; st is an object with: activeToggles (Map name -> 1)
;
; Returns Map("kind", ..., "layer", ..., "send", ..., "blind", ...)
;   kind "toggle"      -> a sticky-layer chord
;   kind "passthrough" -> the key is not claimed; send it as-is
; ===========================================================================

HL_ResolveLayerKey(c, key, st) {
    ; LR-L1  Protected keys can never reach here: the validator rejects them,
    ;        so the engine never registers a hotkey for them. Space stays Space
    ;        even while a sticky layer is on.
    if (HL_ProtectedKeys().Has(key))
        return Map("kind", "passthrough", "layer", "", "send", HL_KeySend()[key], "blind", 1)

    ; LR-L2  Sticky layers, alphabetical so two active toggles resolve
    ;        deterministically.
    for _, ln in c["toggleLayers"] {
        if (st.activeToggles.Has(ln) && c["layers"][ln]["keys"].Has(key))
            return Map("kind", "toggle", "layer", ln, "send", c["layers"][ln]["keys"][key], "blind", c["layers"][ln]["blind"])
    }

    ; LR-L3  Nothing claims it.
    return Map("kind", "passthrough", "layer", "", "send", c["rawSend"].Has(key) ? c["rawSend"][key] : HL_KeySend()[key], "blind", 1)
}
