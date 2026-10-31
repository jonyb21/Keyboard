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
; Scope note: the shipped config only touches F13-F15 and F18-F24 (the
; collision-free codes the board emits from its F-row) plus the keys of the F21
; sticky num layer. F16/F17 belong to the global CIDOO runtime. Space, CapsLock
; and every other key are protected by HL_ProtectedKeys() and the validator
; refuses to bind them.
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

; Keys owned by another runtime. The Cidoo V21 knob runtime claims F16 and F17
; (HID 0x6B/0x6C) globally and AutoHotkey's hook cannot tell keyboards apart,
; so binding them would hijack that knob. Refused wherever a key is named.
HL_ReservedKeys() {
    static m := ""
    if (IsObject(m))
        return m
    m := Map()
    m.CaseSense := "Off"
    m["f16"] := "F16 is owned globally by the Cidoo V21 knob runtime"
    m["f17"] := "F17 is owned globally by the Cidoo V21 knob runtime"
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

; Encoder direction -> the key the AULA knob emits for it (canonical names).
; shortcut: assumed stock codes (knob = volume keys), not yet confirmed on the
; board; run encoder-probe.ahk and change this table if they differ.
HL_EncoderSources() {
    static m := ""
    if (IsObject(m))
        return m
    m := Map("left", "volume_down", "right", "volume_up", "press", "volume_mute")
    return m
}

; Every action slot the encoder block accepts. left/right are turns, press and
; the rest are presses of the knob button, pressLeft/pressRight are turns while
; the button is held down.
HL_EncoderTriggers() {
    return ["left", "right", "press", "doublePress", "triplePress", "hold", "pressLeft", "pressRight"]
}

HL_EncoderAllowed() {
    a := ["note", "devices"]
    for _, t in HL_EncoderTriggers()
        a.Push(t)
    return a
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
    HL_CheckUnknownKeys(a, ["send", "swallow", "layerToggle", "builtin", "byApp", "focusApp", "run", "ahk", "blind", "note"], where, errs)
    kinds := 0
    for _, k in ["send", "swallow", "layerToggle", "builtin", "byApp", "focusApp", "run", "ahk"]
        if (a.Has(k))
            kinds += 1
    if (kinds !== 1) {
        errs.Push(where ": exactly one of send/swallow/layerToggle/builtin/byApp/focusApp/run/ahk is required, found " kinds)
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
    if (a.Has("run")) {
        r := a["run"]
        if (!HL_IsMap(r)) {
            errs.Push(where ".run: must be an object")
        } else {
            HL_CheckUnknownKeys(r, ["path", "args", "hide", "note"], where ".run", errs)
            if (!r.Has("path") || Type(r["path"]) !== "String" || Trim(r["path"]) == "")
                errs.Push(where ".run.path: required, must be a non-empty absolute path")
            else if (!HL_LooksAbsolute(r["path"]))
                errs.Push(where ".run.path: '" r["path"] "' must be an absolute path")
            else if (InStr(r["path"], '"'))
                errs.Push(where ".run.path: must not contain a double quote")
            if (r.Has("args") && Type(r["args"]) !== "String")
                errs.Push(where ".run.args: must be a string")
            if (r.Has("hide") && !HL_IsBoolish(r["hide"]))
                errs.Push(where ".run.hide: must be true or false")
        }
    }
    if (a.Has("ahk")) {
        if (Type(a["ahk"]) !== "String" || Trim(a["ahk"]) == "")
            errs.Push(where ".ahk: must be a non-empty AutoHotkey v2 snippet string")
        else if (StrPut(a["ahk"], "UTF-8") - 1 > 8192)
            errs.Push(where ".ahk: snippet is over the 8 KB limit")
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
    HL_CheckUnknownKeys(cfg, ["schemaVersion", "description", "tapHoldMs", "tapWindowMs", "appGroups", "apps", "bindings", "layers", "encoder"], "root", errs)

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

    if (cfg.Has("tapWindowMs")) {
        t := cfg["tapWindowMs"]
        if (Type(t) !== "Integer")
            errs.Push("root.tapWindowMs: must be an integer number of milliseconds")
        else if (t < 50 || t > 1000)
            errs.Push("root.tapWindowMs: must be between 50 and 1000 (got " t ")")
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
                if (HL_ReservedKeys().Has(k)) {
                    errs.Push("layers." ln ".keys: '" k "' is reserved -- " HL_ReservedKeys()[k])
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
            else if (HL_ReservedKeys().Has(k))
                errs.Push(where ": '" k "' is reserved -- " HL_ReservedKeys()[k])
            else if (!HL_KeyHotkey().Has(k))
                errs.Push(where ": '" k "' is not a bindable physical key name")
            if (!HL_IsMap(B)) {
                errs.Push(where ": must be an object")
                continue
            }
            HL_CheckUnknownKeys(B, ["note", "tap", "hold", "doubleTap", "tripleTap", "tapHold"], where, errs)
            if (!B.Has("tap"))
                errs.Push(where ".tap: required")
            else
                HL_ValidateAction(B["tap"], where ".tap", cfg, errs)
            for _, trig in ["hold", "doubleTap", "tripleTap", "tapHold"]
                if (B.Has(trig))
                    HL_ValidateAction(B[trig], where "." trig, cfg, errs)
        }
    }

    ; ---- apps (per-app profiles; merged into byApp at compile time) ------
    if (cfg.Has("apps")) {
        if (!HL_IsMap(cfg["apps"])) {
            errs.Push("root.apps: must be an object")
        } else {
            for _, g in HL_KeysOf(cfg["apps"]) {
                P := cfg["apps"][g]
                where := "apps." g
                if (!cfg.Has("appGroups") || !HL_IsMap(cfg["appGroups"]) || !cfg["appGroups"].Has(g))
                    errs.Push(where ": '" g "' is not a declared appGroup")
                if (!HL_IsMap(P)) {
                    errs.Push(where ": must be an object")
                    continue
                }
                HL_CheckUnknownKeys(P, ["note", "bindings", "encoder"], where, errs)
                if (P.Has("bindings")) {
                    if (!HL_IsMap(P["bindings"])) {
                        errs.Push(where ".bindings: must be an object")
                    } else {
                        for _, k in HL_KeysOf(P["bindings"]) {
                            kw := where ".bindings." k
                            Bo := P["bindings"][k]
                            if (HL_ProtectedKeys().Has(k))
                                errs.Push(kw ": '" k "' is protected -- " HL_ProtectedKeys()[k])
                            else if (HL_ReservedKeys().Has(k))
                                errs.Push(kw ": '" k "' is reserved -- " HL_ReservedKeys()[k])
                            else if (!HL_KeyHotkey().Has(k))
                                errs.Push(kw ": '" k "' is not a bindable physical key name")
                            else if (!RegExMatch(k, "i)^F(1[3-9]|2[0-4])$") && !(cfg.Has("bindings") && HL_IsMap(cfg["bindings"]) && HL_FindKey(cfg["bindings"], k) != ""))
                                errs.Push(kw ": '" k "' is not bound globally; only F13-F24 may be profile-only")
                            if (!HL_IsMap(Bo)) {
                                errs.Push(kw ": must be an object")
                                continue
                            }
                            HL_CheckUnknownKeys(Bo, ["note", "tap", "hold", "doubleTap", "tripleTap", "tapHold"], kw, errs)
                            if (Bo.Count == 0 || (Bo.Count == 1 && Bo.Has("note")))
                                errs.Push(kw ": must override at least one of tap/hold/doubleTap/tripleTap/tapHold")
                            for _, trig in ["tap", "hold", "doubleTap", "tripleTap", "tapHold"]
                                if (Bo.Has(trig))
                                    HL_ValidateAction(Bo[trig], kw "." trig, cfg, errs, false)
                        }
                    }
                }
                if (P.Has("encoder")) {
                    if (!HL_IsMap(P["encoder"])) {
                        errs.Push(where ".encoder: must be an object")
                    } else {
                        if (!cfg.Has("encoder"))
                            errs.Push(where ".encoder: needs a top-level encoder block (it supplies the devices)")
                        encAllowed := ["note"]
                        for _, t in HL_EncoderTriggers()
                            encAllowed.Push(t)
                        HL_CheckUnknownKeys(P["encoder"], encAllowed, where ".encoder", errs)
                        for _, t in HL_EncoderTriggers()
                            if (P["encoder"].Has(t))
                                HL_ValidateAction(P["encoder"][t], where ".encoder." t, cfg, errs, false)
                    }
                }
            }
        }
    }

    ; ---- encoder ---------------------------------------------------------
    if (cfg.Has("encoder")) {
        E := cfg["encoder"]
        if (!HL_IsMap(E)) {
            errs.Push("root.encoder: must be an object")
        } else {
            HL_CheckUnknownKeys(E, HL_EncoderAllowed(), "encoder", errs)
            if (!E.Has("devices") || !HL_IsArray(E["devices"]) || E["devices"].Length == 0) {
                errs.Push("encoder.devices: required, a non-empty array like VID_0C45&PID_800A")
            } else {
                for _, d in E["devices"]
                    if (Type(d) !== "String" || !RegExMatch(d, "i)^VID_[0-9A-F]{4}&PID_[0-9A-F]{4}$"))
                        errs.Push("encoder.devices: every entry must look like VID_0C45&PID_800A")
            }
            for _, dir in HL_EncoderTriggers()
                if (E.Has(dir))
                    HL_ValidateAction(E[dir], "encoder." dir, cfg, errs)
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
    } else if (a.Has("run")) {
        out["kind"] := "run"
        r := a["run"]
        out["path"] := Trim(r["path"])
        out["args"] := r.Get("args", "")
        out["hide"] := r.Has("hide") ? (r["hide"] ? 1 : 0) : 0
    } else if (a.Has("ahk")) {
        out["kind"] := "ahk"
        out["code"] := a["ahk"]
    } else if (a.Has("byApp")) {
        out["kind"] := "byApp"
        br := Map()
        for g, sub in a["byApp"]
            br[g] := HL_CompileAction(sub)
        out["branches"] := br
    }
    return out
}

; Compiled action for m[name], or "" when absent.
HL_CompileOpt(m, name) {
    return m.Has(name) ? HL_CompileAction(m[name]) : ""
}

; Fills the trigger fields of a binding entry. multi means a tap cannot fire on
; key-down because a later tap, or a tap-then-hold, may still follow; a plain
; tap/hold binding keeps the original zero-latency path.
HL_FillTriggers(entry, tap, hold, dbl, trp, th, forceMulti) {
    entry["tap"] := tap
    entry["hasHold"] := IsObject(hold) ? 1 : 0
    entry["hold"] := hold
    entry["doubleTap"] := dbl
    entry["tripleTap"] := trp
    entry["tapHold"] := th
    entry["multi"] := (forceMulti || IsObject(dbl) || IsObject(trp) || IsObject(th)) ? 1 : 0
    entry["maxTaps"] := IsObject(trp) ? 3 : (IsObject(dbl) ? 2 : 1)
}

; ---- per-app profiles ------------------------------------------------------
; "apps" is sugar. Each profile override is folded into the matching global
; action as a byApp branch (global action becomes the default branch), so the
; runtime keeps its single foreground-exe lookup. App override beats global.

; Existing key in m matching name case-insensitively, else "".
HL_FindKey(m, name) {
    for k, _ in m
        if (StrLower(k) == StrLower(name))
            return k
    return ""
}

; One trigger: base is the global action ("" when absent), ov the app's.
HL_MergeApp(base, g, ov, dflt) {
    if (!IsObject(base))
        base := dflt
    if (base.Has("byApp")) {
        nb := Map()
        for k, v in base["byApp"]
            nb[k] := v
        nb[g] := ov
        return Map("byApp", nb)
    }
    return Map("byApp", Map(g, ov, "default", base))
}

HL_ExpandApps(cfg) {
    if (!cfg.Has("apps"))
        return cfg
    out := Map()
    for k, v in cfg
        out[k] := v
    bind := Map()
    for k, v in cfg["bindings"]
        bind[k] := v
    enc := Map()
    hasEnc := cfg.Has("encoder")
    if (hasEnc)
        for k, v in cfg["encoder"]
            enc[k] := v
    swallow := Map("swallow", true)
    for g, P in cfg["apps"] {
        if (P.Has("bindings"))
            for k, ov in P["bindings"] {
                real := HL_FindKey(bind, k)
                if (real == "")
                    real := k
                nb := Map()
                if (bind.Has(real))
                    for t, x in bind[real]
                        nb[t] := x
                ; A trigger new to this key falls through to the global tap elsewhere.
                gk := HL_FindKey(cfg["bindings"], k)
                dflt := (gk != "" && cfg["bindings"][gk].Has("tap")) ? cfg["bindings"][gk]["tap"] : swallow
                for _, t in ["tap", "hold", "doubleTap", "tripleTap", "tapHold"]
                    if (ov.Has(t))
                        nb[t] := HL_MergeApp(nb.Has(t) ? nb[t] : "", g, ov[t], dflt)
                if (!nb.Has("tap"))
                    nb["tap"] := swallow
                bind[real] := nb
            }
        if (hasEnc && P.Has("encoder"))
            for t, ov in P["encoder"] {
                if (t == "note")
                    continue
                ; An absent left/right/press must keep its stock key outside the app.
                dflt := HL_EncoderSources().Has(t) ? Map("send", HL_EncoderSources()[t]) : swallow
                enc[t] := HL_MergeApp(enc.Has(t) ? enc[t] : "", g, ov, dflt)
            }
    }
    out["bindings"] := bind
    if (hasEnc)
        out["encoder"] := enc
    return out
}

; ---- run / ahk actions -----------------------------------------------------

; Command lines to try, in order, for a compiled "run" action. Pure: the
; running AutoHotkey exe and %ComSpec% are passed in. Path is always quoted;
; args are appended as given.
HL_RunCommands(a, ahkPath, comSpec) {
    p := a["path"]
    q := '"' p '"'
    ar := Trim(a["args"])
    tail := (ar !== "") ? (" " ar) : ""
    ext := ""
    if (RegExMatch(p, "i)\.([^.\\/]+)$", &m))
        ext := StrLower(m[1])
    if (ext == "ps1")
        return ['powershell.exe -NoProfile -ExecutionPolicy Bypass -File ' q tail]
    if (ext == "py")
        return ['py.exe ' q tail, 'python.exe ' q tail]
    if (ext == "bat" || ext == "cmd")
        return ['"' comSpec '" /c "' q tail '"']
    if (ext == "ahk")
        return ['"' ahkPath '" ' q tail]
    return [q tail]
}

; 64-bit FNV-1a over UTF-16 code units, as 16 hex digits.
HL_SnippetHash(s) {
    h := -3750763034362895579  ; 0xCBF29CE484222325 as signed 64-bit
    Loop Parse, s {
        h := (h ^ Ord(A_LoopField)) * 1099511628211
    }
    return Format("{:016x}", h)
}

HL_SnippetSource(code) {
    return "#Requires AutoHotkey v2.0`n" code "`n"
}

HL_CompileConfig(cfg) {
    cfg := HL_ExpandApps(cfg)
    c := Map()
    c["tapHoldMs"] := cfg["tapHoldMs"]
    c["tapWindowMs"] := cfg.Get("tapWindowMs", 250)

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
        HL_FillTriggers(entry, HL_CompileAction(src["tap"]), HL_CompileOpt(src, "hold"), HL_CompileOpt(src, "doubleTap"), HL_CompileOpt(src, "tripleTap"), HL_CompileOpt(src, "tapHold"), false)
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

    ; Encoder: only directions remapped away from the knob's own key get an
    ; entry, so a stock (or absent) encoder block registers nothing at all.
    ; Press mode: any multi-press, hold or turn-while-held slot makes the button
    ; a tap-hold key of its own (a binding named "press", driven by HL_TapHold)
    ; instead of a single-shot remap. A direction with a turn-while-held action
    ; is hooked even when its own action is stock; "action" is then "" and the
    ; engine passes the original key through.
    enc := Map()
    press := ""
    if (cfg.Has("encoder")) {
        E := cfg["encoder"]
        pressMode := false
        for _, t in ["doublePress", "triplePress", "hold", "pressLeft", "pressRight"]
            if (E.Has(t))
                pressMode := true
        for _, dir in ["left", "right", "press"] {
            if (dir == "press" && pressMode)
                continue
            src := HL_EncoderSources()[dir]
            act := E.Has(dir) ? HL_CompileAction(E[dir]) : ""
            stock := (!IsObject(act)) || (act["kind"] == "send" && act["send"] == HL_KeySend()[src])
            pAct := (dir == "left" && E.Has("pressLeft")) ? HL_CompileAction(E["pressLeft"]) : ((dir == "right" && E.Has("pressRight")) ? HL_CompileAction(E["pressRight"]) : "")
            if (stock && !IsObject(pAct))
                continue
            enc[dir] := Map("hk", HL_KeySendName(src), "src", HL_KeySend()[src], "action", stock ? "" : act, "pressAction", pAct)
        }
        if (pressMode) {
            src := HL_EncoderSources()["press"]
            tap := E.Has("press") ? HL_CompileAction(E["press"]) : HL_CompileAction(Map("send", "Volume_Mute"))
            ent := Map("key", "press", "hk", HL_KeySendName(src))
            HL_FillTriggers(ent, tap, HL_CompileOpt(E, "hold"), HL_CompileOpt(E, "doublePress"), HL_CompileOpt(E, "triplePress"), "", true)
            press := Map("hk", HL_KeySendName(src), "src", HL_KeySend()[src], "entry", ent)
        }
    }
    c["encoder"] := Map("devices", cfg.Has("encoder") ? cfg["encoder"]["devices"] : [], "dirs", enc, "press", press)

    return c
}

; "{Volume_Up}" -> "Volume_Up": the hotkey name for a KeySend token.
HL_KeySendName(key) {
    return SubStr(HL_KeySend()[key], 2, -1)
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
; Config path + change watching
; ===========================================================================

; The app writes %APPDATA%\aula-desktop\hostlayer.json; when it exists it wins,
; otherwise the hand-edited layers.json next to the script is used.
HL_ResolveConfigPath(appData, scriptDir) {
    if (appData !== "") {
        p := appData "\aula-desktop\hostlayer.json"
        if (FileExist(p))
            return p
    }
    return scriptDir "\layers.json"
}

; Debounced change detector for the polling reload. sig is any string that
; changes when the config does (path + mtime + size). A change must be seen on
; two consecutive polls before Step returns true, so a file still being written
; is never read half done.
class HL_ConfigWatch {
    __New(sig) {
        this.applied := sig
        this.seen := sig
    }

    Mark(sig) {
        this.applied := sig
        this.seen := sig
    }

    Step(sig) {
        if (sig == this.applied) {
            this.seen := sig
            return false
        }
        if (sig == this.seen) {
            this.applied := sig
            return true
        }
        this.seen := sig
        return false
    }
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
;         "waiting"   -> multi-tap key released, waiting tapWindowMs for more taps
;         "consumed"  -> down, but a turn-while-held already used this press
;
; A binding with doubleTap, tripleTap or tapHold is "multi": its tap cannot fire
; on key-down or even on key-up, because a further tap may follow. It fires when
; tapWindowMs passes with no new press, or at once when no further tap is
; possible. A binding without them keeps the original paths unchanged.
;
; Emitted events (the engine executes them; core never touches the keyboard):
;   Map("type","action",     "action",<compiled>, "phase","tap"|"hold"|"doubleTap"|"tripleTap"|"tapHold")
;   Map("type","builtin",    "name",<str>, "phase","start"|"repeat"|"end")
;   Map("type","armTimer",   "key",<k>, "ms",<n>)      hold timer
;   Map("type","cancelTimer","key",<k>)
;   Map("type","armWait",    "key",<k>, "ms",<n>)      multi-tap window timer
;   Map("type","cancelWait", "key",<k>)
; ===========================================================================

class HL_TapHold {
    __New(compiled) {
        this.cfg := compiled
        this.tapHoldMs := compiled["tapHoldMs"]
        this.tapWindowMs := compiled.Has("tapWindowMs") ? compiled["tapWindowMs"] : 250
        this.state := Map()
        this.state.CaseSense := "Off"
        for k, _ in compiled["bindings"]
            this.state[k] := Map("phase", "idle", "downAt", 0, "count", 0, "holdAction", "")
    }

    Phase(key) {
        return this.state[key]["phase"]
    }

    Reset() {
        for k, _ in this.state {
            this.state[k]["phase"] := "idle"
            this.state[k]["downAt"] := 0
            this.state[k]["count"] := 0
            this.state[k]["holdAction"] := ""
        }
    }

    _IsBrowse(action) {
        return (action !== "" && action["kind"] == "builtin" && action["builtin"] == "altTabBrowse")
    }

    ; The hold action that press number n (1-based) can become, or "".
    _HoldFor(b, n) {
        if (n == 1)
            return b["hold"]
        if (n == 2)
            return b["tapHold"]
        return ""
    }

    ; The action for n completed taps. A missing doubleTap falls back to tap.
    _TapFor(b, n) {
        if (n >= 3 && b["tripleTap"] !== "")
            return b["tripleTap"]
        if (n >= 2 && b["doubleTap"] !== "")
            return b["doubleTap"]
        return b["tap"]
    }

    _TapPhase(b, n) {
        if (n >= 3 && b["tripleTap"] !== "")
            return "tripleTap"
        if (n >= 2 && b["doubleTap"] !== "")
            return "doubleTap"
        return "tap"
    }

    ; True when, after n taps, another tap or a tap-then-hold can still follow.
    _WaitMore(b, n) {
        return (n < b["maxTaps"] || (n == 1 && b["tapHold"] !== ""))
    }

    ; True while the button is physically down (turn-while-held checks this).
    IsDown(key) {
        p := this.state[key]["phase"]
        return (p == "pending" || p == "held" || p == "consumed" || p == "downfired")
    }

    ; TH-L9  A turn happened while the key was down: this press belongs to the
    ;        turn, so it must neither tap nor hold on release.
    Consume(key) {
        ev := []
        st := this.state[key]
        if (st["phase"] == "pending") {
            ev.Push(Map("type", "cancelTimer", "key", key))
        } else if (st["phase"] == "held") {
            if (this._IsBrowse(st["holdAction"]))
                ev.Push(Map("type", "builtin", "name", "altTabBrowse", "phase", "end"))
        } else {
            return ev
        }
        st["phase"] := "consumed"
        st["count"] := 0
        return ev
    }

    ; TH-L1 .. TH-L4, TH-L10
    OnDown(key, now) {
        ev := []
        b := this.cfg["bindings"][key]
        st := this.state[key]

        ; TH-L1  Already resolved as a hold. The only thing a further key-down
        ;        may do is advance a hold-to-browse switcher. Never re-fire tap.
        if (st["phase"] == "held") {
            if (this._IsBrowse(st["holdAction"]))
                ev.Push(Map("type", "builtin", "name", "altTabBrowse", "phase", "repeat"))
            return ev
        }

        ; TH-L2  Autorepeat arriving while still undecided, after a tap-only
        ;        key already fired, or after a turn consumed the press. Swallow
        ;        it: correctness rule 2, no autorepeat of the tap action.
        if (st["phase"] == "pending" || st["phase"] == "downfired" || st["phase"] == "consumed")
            return ev

        ; TH-L10 A new press inside the tap window: the pending single/double
        ;        tap is not final yet, so stop the window timer and count on.
        if (st["phase"] == "waiting") {
            ev.Push(Map("type", "cancelWait", "key", key))
            st["downAt"] := now
            st["phase"] := "pending"
            if (this._HoldFor(b, st["count"] + 1) !== "")
                ev.Push(Map("type", "armTimer", "key", key, "ms", this.tapHoldMs))
            return ev
        }

        ; TH-L3  Fresh press of a tap-only key: fire immediately on key-down,
        ;        no discrimination delay at all.
        st["downAt"] := now
        st["count"] := 0
        if (!b["hasHold"] && !b["multi"]) {
            st["phase"] := "downfired"
            ev.Push(Map("type", "action", "action", b["tap"], "phase", "tap"))
            return ev
        }

        ; TH-L4  Fresh press of a tap-hold key: nothing is emitted yet, arm the
        ;        discriminator (only if a hold is possible for this press).
        st["phase"] := "pending"
        if (this._HoldFor(b, 1) !== "")
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

        ; TH-L6  Threshold crossed: the hold fires exactly once. The hold is
        ;        tapHold when this is the second press of a tap-then-hold.
        st["phase"] := "held"
        h := this._HoldFor(b, st["count"] + 1)
        st["holdAction"] := h
        ph := (st["count"] == 0) ? "hold" : "tapHold"
        st["count"] := 0
        if (this._IsBrowse(h))
            ev.Push(Map("type", "builtin", "name", "altTabBrowse", "phase", "start"))
        else
            ev.Push(Map("type", "action", "action", h, "phase", ph))
        return ev
    }

    ; TH-L7 .. TH-L8, TH-L11
    OnUp(key, now) {
        ev := []
        b := this.cfg["bindings"][key]
        st := this.state[key]

        ; TH-L7  Released before the threshold: it was a tap. Kill the pending
        ;        timer first so the hold can never also fire.
        if (st["phase"] == "pending") {
            ev.Push(Map("type", "cancelTimer", "key", key))
            if (!b["multi"]) {
                ev.Push(Map("type", "action", "action", b["tap"], "phase", "tap"))
            } else {
                ; TH-L11 Multi-tap: count it. If another tap can still follow,
                ;        open the window; otherwise this tap count is final.
                n := st["count"] + 1
                if (this._WaitMore(b, n)) {
                    st["count"] := n
                    st["phase"] := "waiting"
                    ev.Push(Map("type", "armWait", "key", key, "ms", this.tapWindowMs))
                    return ev
                }
                ev.Push(Map("type", "action", "action", this._TapFor(b, n), "phase", this._TapPhase(b, n)))
            }
        } else if (st["phase"] == "held") {
            ; TH-L8  Hold already fired. Only a browse-style hold has a release
            ;        phase (it must let go of Alt to commit the selection).
            if (this._IsBrowse(st["holdAction"]))
                ev.Push(Map("type", "builtin", "name", "altTabBrowse", "phase", "end"))
        }
        st["phase"] := "idle"
        st["count"] := 0
        st["holdAction"] := ""
        return ev
    }

    ; TH-L12 The tap window closed with no further press: the count is final.
    OnWaitTimer(key, now) {
        ev := []
        b := this.cfg["bindings"][key]
        st := this.state[key]
        if (st["phase"] !== "waiting")
            return ev
        n := st["count"]
        st["phase"] := "idle"
        st["count"] := 0
        ev.Push(Map("type", "action", "action", this._TapFor(b, n), "phase", this._TapPhase(b, n)))
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
