"""Python mirror of services/hostlayer/core.ahk.

The AHK suite cannot execute itself in a gate lane: AutoHotkey has no test
runner, no assertion API, and a load error opens a modal dialog. So the pure
half of the engine is ported here and executed instead.

This is a MIRROR, not a reimplementation. Every function below carries the name
of its core.ahk counterpart, and the two state machines carry the same step
labels (TH-L1..TH-L8, LR-L1..LR-L3) so they can be diffed side by side. If you
change one, change the other and re-run both gates.

Stdlib only. No network, no device, no real clock.
"""

from __future__ import annotations

import json
import os
import re

# ---------------------------------------------------------------------------
# Lookup tables  (core.ahk: HL_ModCanon / HL_ModPrefix / HL_ModOrder)
# ---------------------------------------------------------------------------

MOD_CANON = {
    "ctrl": "Ctrl",
    "control": "Ctrl",
    "alt": "Alt",
    "shift": "Shift",
    "win": "Win",
    "super": "Win",
    "lwin": "Win",
}

MOD_PREFIX = {"Ctrl": "^", "Alt": "!", "Shift": "+", "Win": "#"}

MOD_ORDER = ["Ctrl", "Alt", "Shift", "Win"]

# core.ahk: HL_ProtectedKeys
PROTECTED_KEYS = {
    "space": "Space must stay a plain Space key",
    "capslock": "CapsLock must stay stock",
}

# core.ahk: HL_ReservedKeys. The Cidoo V21 knob runtime owns F16/F17 globally.
RESERVED_KEYS = {
    "f16": "F16 is owned globally by the Cidoo V21 knob runtime",
    "f17": "F17 is owned globally by the Cidoo V21 knob runtime",
}

BUILTINS = {"altTabTap", "altTabBrowse"}

# core.ahk: HL_EncoderSources
ENCODER_SOURCES = {"left": "volume_down", "right": "volume_up", "press": "volume_mute"}
# core.ahk: HL_EncoderTriggers
ENCODER_DIRS = ["left", "right"]
ENCODER_TRIGGERS = [
    "left", "right", "press", "doublePress", "triplePress", "hold",
    "pressLeft", "pressRight",
]
ENCODER_PRESS_MODE = ["doublePress", "triplePress", "hold", "pressLeft", "pressRight"]


def _build_key_send():
    """core.ahk: HL_KeySend"""
    m = {}
    for i in range(26):
        ch = chr(ord("a") + i)
        m[ch] = "{" + ch + "}"
    for i in range(10):
        ch = chr(ord("0") + i)
        m[ch] = "{" + ch + "}"
    for i in range(1, 25):
        m["f%d" % i] = "{F%d}" % i
    m["esc"] = "{Escape}"
    m["escape"] = "{Escape}"
    m["enter"] = "{Enter}"
    m["return"] = "{Enter}"
    m["tab"] = "{Tab}"
    m["space"] = "{Space}"
    # CapsLock is never bindable (PROTECTED_KEYS), but the layer resolver looks
    # up a Send token for protected keys, so the table must be total.
    m["capslock"] = "{CapsLock}"
    m["backspace"] = "{Backspace}"
    m["bs"] = "{Backspace}"
    m["delete"] = "{Delete}"
    m["del"] = "{Delete}"
    m["insert"] = "{Insert}"
    m["ins"] = "{Insert}"
    m["up"] = "{Up}"
    m["down"] = "{Down}"
    m["left"] = "{Left}"
    m["right"] = "{Right}"
    m["home"] = "{Home}"
    m["end"] = "{End}"
    m["pgup"] = "{PgUp}"
    m["pageup"] = "{PgUp}"
    m["pgdn"] = "{PgDn}"
    m["pagedown"] = "{PgDn}"
    m["comma"] = "{,}"
    m["period"] = "{.}"
    m["dot"] = "{.}"
    m["semicolon"] = "{;}"
    m["slash"] = "{/}"
    m["minus"] = "{-}"
    m["equals"] = "{=}"
    m["quote"] = "{'}"
    m["lbracket"] = "{[}"
    m["rbracket"] = "{]}"
    m["backslash"] = "{\\}"
    m["printscreen"] = "{PrintScreen}"
    m["appskey"] = "{AppsKey}"
    m["volume_mute"] = "{Volume_Mute}"
    m["volume_up"] = "{Volume_Up}"
    m["volume_down"] = "{Volume_Down}"
    m["media_prev"] = "{Media_Prev}"
    m["media_next"] = "{Media_Next}"
    m["media_play_pause"] = "{Media_Play_Pause}"
    m["media_stop"] = "{Media_Stop}"
    for i in range(10):
        m["numpad%d" % i] = "{Numpad%d}" % i
    return m


def _build_key_hotkey():
    """core.ahk: HL_KeyHotkey"""
    m = {}
    for i in range(26):
        ch = chr(ord("a") + i)
        m[ch] = ch
    for i in range(10):
        ch = chr(ord("0") + i)
        m[ch] = ch
    for i in range(1, 25):
        m["f%d" % i] = "F%d" % i
    m["capslock"] = "CapsLock"
    m["space"] = "Space"
    m["tab"] = "Tab"
    m["enter"] = "Enter"
    m["esc"] = "Escape"
    m["escape"] = "Escape"
    m["backspace"] = "Backspace"
    m["comma"] = ","
    m["period"] = "."
    m["semicolon"] = ";"
    m["slash"] = "/"
    m["minus"] = "-"
    m["equals"] = "="
    m["quote"] = "'"
    m["lbracket"] = "["
    m["rbracket"] = "]"
    m["backslash"] = "\\"
    m["up"] = "Up"
    m["down"] = "Down"
    m["left"] = "Left"
    m["right"] = "Right"
    m["home"] = "Home"
    m["end"] = "End"
    m["pgup"] = "PgUp"
    m["pgdn"] = "PgDn"
    m["delete"] = "Delete"
    m["insert"] = "Insert"
    return m


KEY_SEND = _build_key_send()
KEY_HOTKEY = _build_key_hotkey()


class ChordError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Chord parsing  (core.ahk: HL_ParseChord / HL_IsForbiddenChord)
# ---------------------------------------------------------------------------


def parse_chord(text):
    """core.ahk: HL_ParseChord"""
    if not isinstance(text, str) or text.strip() == "":
        raise ChordError("chord must be a non-empty string")
    parts = text.split("+")
    mods = set()
    key_tok = parts[-1].strip()
    for raw in parts[:-1]:
        raw = raw.strip()
        if raw.lower() not in MOD_CANON:
            raise ChordError("unknown modifier '%s' in chord '%s'" % (raw, text))
        mods.add(MOD_CANON[raw.lower()])
    if key_tok.lower() not in KEY_SEND:
        raise ChordError("unknown key '%s' in chord '%s'" % (key_tok, text))
    prefix = "".join(MOD_PREFIX[m] for m in MOD_ORDER if m in mods)
    return {
        "mods": mods,
        "key": key_tok.lower(),
        "send": prefix + KEY_SEND[key_tok.lower()],
        "text": text,
    }


def is_forbidden_chord(parsed):
    """core.ahk: HL_IsForbiddenChord

    Ctrl+Alt+Shift+V belongs to InDesign Paste in Place and a Photoshop
    internal tool. The engine must never emit or intercept it.
    """
    if parsed["key"] != "v":
        return False
    if not {"Ctrl", "Alt", "Shift"}.issubset(parsed["mods"]):
        return False
    if "Win" in parsed["mods"]:
        return False
    return True


# ---------------------------------------------------------------------------
# Config reading  (core.ahk: HL_ReadConfigText)
# ---------------------------------------------------------------------------


def read_config_text(path):
    """core.ahk: HL_ReadConfigText. Strips a UTF-8 BOM, correctness rule 4."""
    with open(path, "rb") as fh:
        raw = fh.read()
    text = raw.decode("utf-8-sig")
    if text.startswith("﻿"):
        text = text[1:]
    return text


def parse_config_text(text):
    if text.startswith("﻿"):
        text = text[1:]
    return json.loads(text)


# ---------------------------------------------------------------------------
# Validation  (core.ahk: HL_ValidateConfig and friends)
# ---------------------------------------------------------------------------


def _is_boolish(v):
    """core.ahk: HL_IsBoolish"""
    return isinstance(v, bool) or (isinstance(v, int) and v in (0, 1))


def _ends_with_exe(s):
    """core.ahk: HL_EndsWithExe"""
    return isinstance(s, str) and s[-4:].lower() == ".exe"


def _looks_absolute(s):
    """core.ahk: HL_LooksAbsolute"""
    if len(s) >= 3 and s[1] == ":" and s[2] in ("\\", "/"):
        return True
    if s[:2] == "\\\\":
        return True
    return False


def _check_unknown_keys(m, allowed, where, errs):
    """core.ahk: HL_CheckUnknownKeys"""
    for k in sorted(m.keys()):
        if k not in allowed:
            errs.append("%s: unknown key '%s'" % (where, k))


ACTION_KINDS = ["send", "swallow", "layerToggle", "builtin", "byApp", "focusApp"]
ACTION_ALLOWED = ACTION_KINDS + ["blind", "note"]


def validate_action(a, where, cfg, errs, allow_nested=True):
    """core.ahk: HL_ValidateAction"""
    if not isinstance(a, dict):
        errs.append("%s: action must be an object" % where)
        return
    _check_unknown_keys(a, ACTION_ALLOWED, where, errs)
    kinds = sum(1 for k in ACTION_KINDS if k in a)
    if kinds != 1:
        errs.append(
            "%s: exactly one of send/swallow/layerToggle/builtin/byApp/focusApp "
            "is required, found %d" % (where, kinds)
        )
        return
    if "blind" in a and not _is_boolish(a["blind"]):
        errs.append("%s.blind: must be true or false" % where)
    if "send" in a:
        try:
            p = parse_chord(a["send"])
            if is_forbidden_chord(p):
                errs.append(
                    "%s.send: Ctrl+Alt+Shift+V is reserved by InDesign Paste in "
                    "Place and must never be bound" % where
                )
        except ChordError as e:
            errs.append("%s.send: %s" % (where, e))
    if "swallow" in a and not _is_boolish(a["swallow"]):
        errs.append("%s.swallow: must be true or false" % where)
    if "builtin" in a and a["builtin"] not in BUILTINS:
        errs.append("%s.builtin: unknown builtin '%s'" % (where, a["builtin"]))
    if "layerToggle" in a:
        ln = a["layerToggle"]
        layers = cfg.get("layers")
        if not isinstance(layers, dict) or ln not in layers:
            errs.append("%s.layerToggle: no layer named '%s'" % (where, ln))
        elif not isinstance(layers[ln], dict) or layers[ln].get("type") != "toggle":
            errs.append(
                "%s.layerToggle: layer '%s' is not of type 'toggle'" % (where, ln)
            )
    if "focusApp" in a:
        f = a["focusApp"]
        if not isinstance(f, dict):
            errs.append("%s.focusApp: must be an object" % where)
        else:
            _check_unknown_keys(
                f,
                ["exe", "launch", "windowClass", "args", "note"],
                where + ".focusApp",
                errs,
            )
            exe = f.get("exe")
            if not isinstance(exe, str) or exe.strip() == "":
                errs.append(
                    "%s.focusApp.exe: required, must be a non-empty exe name" % where
                )
            elif not _ends_with_exe(exe):
                errs.append("%s.focusApp.exe: '%s' must end in .exe" % (where, exe))
            if "launch" in f:
                lv = f["launch"]
                if not isinstance(lv, str):
                    errs.append(
                        "%s.focusApp.launch: must be a string (empty means "
                        "focus-only)" % where
                    )
                elif lv.strip() != "" and not _looks_absolute(lv):
                    errs.append(
                        "%s.focusApp.launch: '%s' must be an absolute path"
                        % (where, lv)
                    )
                elif lv.strip() != "" and not _ends_with_exe(lv.strip()):
                    errs.append(
                        "%s.focusApp.launch: '%s' must point at an .exe" % (where, lv)
                    )
            if "windowClass" in f and not isinstance(f["windowClass"], str):
                errs.append("%s.focusApp.windowClass: must be a string" % where)
            if "args" in f and not isinstance(f["args"], str):
                errs.append("%s.focusApp.args: must be a string" % where)
    if "byApp" in a:
        if not allow_nested:
            errs.append("%s.byApp: byApp cannot be nested inside byApp" % where)
            return
        b = a["byApp"]
        if not isinstance(b, dict):
            errs.append("%s.byApp: must be an object" % where)
            return
        if "default" not in b:
            errs.append("%s.byApp: a 'default' branch is required" % where)
        for g in sorted(b.keys()):
            if g != "default":
                groups = cfg.get("appGroups")
                if not isinstance(groups, dict) or g not in groups:
                    errs.append(
                        "%s.byApp: '%s' is not a declared appGroup" % (where, g)
                    )
            validate_action(b[g], "%s.byApp.%s" % (where, g), cfg, errs, False)


ROOT_ALLOWED = [
    "schemaVersion",
    "description",
    "tapHoldMs",
    "tapWindowMs",
    "appGroups",
    "bindings",
    "layers",
    "encoder",
]


def validate_config(cfg):
    """core.ahk: HL_ValidateConfig. Returns a list of error strings."""
    errs = []
    if not isinstance(cfg, dict):
        return ["root: config must be a JSON object"]
    _check_unknown_keys(cfg, ROOT_ALLOWED, "root", errs)

    if cfg.get("schemaVersion") != 1:
        errs.append("root.schemaVersion: must be 1")

    if "tapHoldMs" not in cfg:
        errs.append("root.tapHoldMs: required")
    else:
        t = cfg["tapHoldMs"]
        if not isinstance(t, int) or isinstance(t, bool):
            errs.append("root.tapHoldMs: must be an integer number of milliseconds")
        elif t < 50 or t > 1000:
            errs.append("root.tapHoldMs: must be between 50 and 1000 (got %s)" % t)

    if "tapWindowMs" in cfg:
        t = cfg["tapWindowMs"]
        if not isinstance(t, int) or isinstance(t, bool):
            errs.append("root.tapWindowMs: must be an integer number of milliseconds")
        elif t < 50 or t > 1000:
            errs.append("root.tapWindowMs: must be between 50 and 1000 (got %s)" % t)

    # ---- appGroups -------------------------------------------------------
    seen_exe = {}
    if "appGroups" in cfg:
        if not isinstance(cfg["appGroups"], dict):
            errs.append("root.appGroups: must be an object")
        else:
            for g in sorted(cfg["appGroups"].keys()):
                lst = cfg["appGroups"][g]
                if not isinstance(lst, list):
                    errs.append("appGroups.%s: must be an array of exe names" % g)
                    continue
                if len(lst) == 0:
                    errs.append("appGroups.%s: must list at least one exe name" % g)
                for exe in lst:
                    if not _ends_with_exe(exe):
                        errs.append(
                            "appGroups.%s: entries must be exe names ending in .exe" % g
                        )
                        continue
                    low = exe.lower()
                    if low in seen_exe:
                        errs.append(
                            "appGroups.%s: '%s' is already claimed by group '%s'"
                            % (g, exe, seen_exe[low])
                        )
                    else:
                        seen_exe[low] = g

    # ---- layers ----------------------------------------------------------
    if not isinstance(cfg.get("layers"), dict):
        errs.append("root.layers: required object")
    else:
        for ln in sorted(cfg["layers"].keys()):
            L = cfg["layers"][ln]
            if not isinstance(L, dict):
                errs.append("layers.%s: must be an object" % ln)
                continue
            _check_unknown_keys(L, ["type", "blind", "keys", "note"], "layers." + ln, errs)
            if L.get("type") != "toggle":
                errs.append("layers.%s.type: must be 'toggle'" % ln)
            if "blind" in L and not _is_boolish(L["blind"]):
                errs.append("layers.%s.blind: must be true or false" % ln)
            if not isinstance(L.get("keys"), dict):
                errs.append("layers.%s.keys: required object" % ln)
                continue
            if len(L["keys"]) == 0:
                errs.append("layers.%s.keys: must map at least one key" % ln)
            for k in sorted(L["keys"].keys()):
                kl = k.lower()
                if kl in PROTECTED_KEYS:
                    errs.append(
                        "layers.%s.keys: '%s' is protected -- %s"
                        % (ln, k, PROTECTED_KEYS[kl])
                    )
                    continue
                if kl in RESERVED_KEYS:
                    errs.append(
                        "layers.%s.keys: '%s' is reserved -- %s"
                        % (ln, k, RESERVED_KEYS[kl])
                    )
                    continue
                if kl not in KEY_HOTKEY:
                    errs.append(
                        "layers.%s.keys: '%s' is not a bindable physical key name"
                        % (ln, k)
                    )
                    continue
                try:
                    p = parse_chord(L["keys"][k])
                    if is_forbidden_chord(p):
                        errs.append(
                            "layers.%s.keys.%s: Ctrl+Alt+Shift+V is reserved and "
                            "must never be bound" % (ln, k)
                        )
                except ChordError as e:
                    errs.append("layers.%s.keys.%s: %s" % (ln, k, e))

    # ---- bindings --------------------------------------------------------
    if not isinstance(cfg.get("bindings"), dict):
        errs.append("root.bindings: required object")
    else:
        if len(cfg["bindings"]) == 0:
            errs.append("root.bindings: must define at least one binding")
        for k in sorted(cfg["bindings"].keys()):
            B = cfg["bindings"][k]
            where = "bindings." + k
            kl = k.lower()
            if kl in PROTECTED_KEYS:
                errs.append("%s: '%s' is protected -- %s" % (where, k, PROTECTED_KEYS[kl]))
            elif kl in RESERVED_KEYS:
                errs.append("%s: '%s' is reserved -- %s" % (where, k, RESERVED_KEYS[kl]))
            elif kl not in KEY_HOTKEY:
                errs.append("%s: '%s' is not a bindable physical key name" % (where, k))
            if not isinstance(B, dict):
                errs.append("%s: must be an object" % where)
                continue
            _check_unknown_keys(
                B, ["note", "tap", "hold", "doubleTap", "tripleTap", "tapHold"], where, errs
            )
            if "tap" not in B:
                errs.append("%s.tap: required" % where)
            else:
                validate_action(B["tap"], where + ".tap", cfg, errs)
            for trig in ("hold", "doubleTap", "tripleTap", "tapHold"):
                if trig in B:
                    validate_action(B[trig], where + "." + trig, cfg, errs)

    # ---- encoder ----
    if "encoder" in cfg:
        E = cfg["encoder"]
        if not isinstance(E, dict):
            errs.append("root.encoder: must be an object")
        else:
            _check_unknown_keys(E, ["note", "devices"] + ENCODER_TRIGGERS, "encoder", errs)
            devs = E.get("devices")
            if not isinstance(devs, list) or not devs:
                errs.append("encoder.devices: required, a non-empty array like VID_0C45&PID_800A")
            else:
                for d in devs:
                    if not isinstance(d, str) or not re.match(r"(?i)^VID_[0-9A-F]{4}&PID_[0-9A-F]{4}$", d):
                        errs.append("encoder.devices: every entry must look like VID_0C45&PID_800A")
            for d in ENCODER_TRIGGERS:
                if d in E:
                    validate_action(E[d], "encoder." + d, cfg, errs)

    return errs


# ---------------------------------------------------------------------------
# Compilation  (core.ahk: HL_CompileAction / HL_CompileConfig)
# ---------------------------------------------------------------------------


def compile_action(a):
    """core.ahk: HL_CompileAction"""
    out = {"blind": 1 if a.get("blind") else 0}
    if "send" in a:
        out["kind"] = "send"
        out["send"] = parse_chord(a["send"])["send"]
        out["chord"] = a["send"]
    elif "swallow" in a:
        out["kind"] = "swallow"
    elif "layerToggle" in a:
        out["kind"] = "layerToggle"
        out["layer"] = a["layerToggle"]
    elif "builtin" in a:
        out["kind"] = "builtin"
        out["builtin"] = a["builtin"]
    elif "focusApp" in a:
        f = a["focusApp"]
        out["kind"] = "focusApp"
        out["exe"] = f["exe"]
        out["launch"] = f.get("launch", "")
        out["windowClass"] = f.get("windowClass", "")
        out["args"] = f.get("args", "")
    elif "byApp" in a:
        out["kind"] = "byApp"
        out["branches"] = {g: compile_action(sub) for g, sub in a["byApp"].items()}
    return out


def compile_opt(m, name):
    """core.ahk: HL_CompileOpt"""
    return compile_action(m[name]) if name in m else ""


def fill_triggers(entry, tap, hold, dbl, trp, th, force_multi):
    """core.ahk: HL_FillTriggers"""
    entry["tap"] = tap
    entry["hasHold"] = 1 if hold != "" else 0
    entry["hold"] = hold
    entry["doubleTap"] = dbl
    entry["tripleTap"] = trp
    entry["tapHold"] = th
    entry["multi"] = 1 if (force_multi or dbl != "" or trp != "" or th != "") else 0
    entry["maxTaps"] = 3 if trp != "" else (2 if dbl != "" else 1)


def compile_config(cfg):
    """core.ahk: HL_CompileConfig"""
    c = {"tapHoldMs": cfg["tapHoldMs"], "tapWindowMs": cfg.get("tapWindowMs", 250)}

    exe_index = {}
    for g, lst in cfg.get("appGroups", {}).items():
        for exe in lst:
            exe_index[exe.lower()] = g
    c["exeIndex"] = exe_index

    layers = {}
    for ln, L in cfg["layers"].items():
        keys = {k.lower(): parse_chord(v)["send"] for k, v in L["keys"].items()}
        layers[ln] = {
            "type": L["type"],
            "blind": 1 if L.get("blind") else 0,
            "keys": keys,
        }
    c["layers"] = layers

    bindings = {}
    for k, src in cfg["bindings"].items():
        entry = {"key": k, "hk": KEY_HOTKEY[k.lower()]}
        fill_triggers(
            entry,
            compile_action(src["tap"]),
            compile_opt(src, "hold"),
            compile_opt(src, "doubleTap"),
            compile_opt(src, "tripleTap"),
            compile_opt(src, "tapHold"),
            False,
        )
        entry["note"] = src.get("note", "")
        bindings[k.lower()] = entry
    c["bindings"] = bindings

    layer_keys = {}
    for ln, L in layers.items():
        for k in L["keys"]:
            layer_keys[k] = 1
    c["layerKeys"] = layer_keys
    c["rawSend"] = {k: KEY_SEND[k] for k in layer_keys}
    c["toggleLayers"] = sorted(layers.keys())

    enc = {}
    press = ""
    E = cfg.get("encoder", {})
    press_mode = any(t in E for t in ENCODER_PRESS_MODE)
    for d in ["left", "right", "press"]:
        if d == "press" and press_mode:
            continue
        src = ENCODER_SOURCES[d]
        act = compile_action(E[d]) if d in E else ""
        stock = act == "" or (act["kind"] == "send" and act["send"] == KEY_SEND[src])
        p_name = {"left": "pressLeft", "right": "pressRight"}.get(d)
        p_act = compile_action(E[p_name]) if p_name and p_name in E else ""
        if stock and p_act == "":
            continue
        enc[d] = {
            "hk": KEY_SEND[src][1:-1],
            "src": KEY_SEND[src],
            "action": "" if stock else act,
            "pressAction": p_act,
        }
    if press_mode:
        src = ENCODER_SOURCES["press"]
        tap = compile_action(E["press"]) if "press" in E else compile_action({"send": "Volume_Mute"})
        ent = {"key": "press", "hk": KEY_SEND[src][1:-1]}
        fill_triggers(
            ent, tap, compile_opt(E, "hold"), compile_opt(E, "doublePress"),
            compile_opt(E, "triplePress"), "", True,
        )
        press = {"hk": KEY_SEND[src][1:-1], "src": KEY_SEND[src], "entry": ent}
    c["encoder"] = {"devices": E.get("devices", []), "dirs": enc, "press": press}
    return c


class ConfigError(ValueError):
    def __init__(self, errors):
        super().__init__("config validation failed:\n" + "\n".join(errors))
        self.errors = errors


def load_config_file(path):
    """core.ahk: HL_LoadConfigFile"""
    cfg = parse_config_text(read_config_text(path))
    errs = validate_config(cfg)
    if errs:
        raise ConfigError(errs)
    return compile_config(cfg)


def load_config_obj(cfg):
    errs = validate_config(cfg)
    if errs:
        raise ConfigError(errs)
    return compile_config(cfg)


# ---------------------------------------------------------------------------
# App resolution  (core.ahk: HL_ResolveApp)
# ---------------------------------------------------------------------------


def resolve_app(action, active_exe, exe_index):
    """core.ahk: HL_ResolveApp"""
    if action["kind"] != "byApp":
        return action
    br = action["branches"]
    if active_exe:
        g = exe_index.get(active_exe.lower())
        if g is not None and g in br:
            return br[g]
    return br["default"]


# ---------------------------------------------------------------------------
# Tap-hold state machine  (core.ahk: class HL_TapHold)
# ---------------------------------------------------------------------------


class TapHold:
    """core.ahk: HL_TapHold. Step labels match one for one.

    Phases: idle, pending, held, downfired, waiting (multi-tap window open),
    consumed (a turn-while-held used this press)."""

    def __init__(self, compiled):
        self.cfg = compiled
        self.tapHoldMs = compiled["tapHoldMs"]
        self.tapWindowMs = compiled.get("tapWindowMs", 250)
        self.state = {
            k: {"phase": "idle", "downAt": 0, "count": 0, "holdAction": ""}
            for k in compiled["bindings"]
        }

    def phase(self, key):
        return self.state[key.lower()]["phase"]

    def reset(self):
        for st in self.state.values():
            st["phase"] = "idle"
            st["downAt"] = 0
            st["count"] = 0
            st["holdAction"] = ""

    @staticmethod
    def _is_browse(action):
        return (
            action != ""
            and action.get("kind") == "builtin"
            and action.get("builtin") == "altTabBrowse"
        )

    @staticmethod
    def _hold_for(b, n):
        """The hold action press number n (1-based) can become, or ""."""
        if n == 1:
            return b["hold"]
        if n == 2:
            return b["tapHold"]
        return ""

    @staticmethod
    def _tap_for(b, n):
        """The action for n completed taps. A missing doubleTap falls back to tap."""
        if n >= 3 and b["tripleTap"] != "":
            return b["tripleTap"]
        if n >= 2 and b["doubleTap"] != "":
            return b["doubleTap"]
        return b["tap"]

    @staticmethod
    def _tap_phase(b, n):
        if n >= 3 and b["tripleTap"] != "":
            return "tripleTap"
        if n >= 2 and b["doubleTap"] != "":
            return "doubleTap"
        return "tap"

    @staticmethod
    def _wait_more(b, n):
        """After n taps, can another tap or a tap-then-hold still follow?"""
        return n < b["maxTaps"] or (n == 1 and b["tapHold"] != "")

    def is_down(self, key):
        return self.state[key.lower()]["phase"] in (
            "pending", "held", "consumed", "downfired"
        )

    def consume(self, key):
        """core.ahk: HL_TapHold.Consume, TH-L9"""
        key = key.lower()
        ev = []
        st = self.state[key]
        if st["phase"] == "pending":
            ev.append({"type": "cancelTimer", "key": key})
        elif st["phase"] == "held":
            if self._is_browse(st["holdAction"]):
                ev.append({"type": "builtin", "name": "altTabBrowse", "phase": "end"})
        else:
            return ev
        st["phase"] = "consumed"
        st["count"] = 0
        return ev

    def on_down(self, key, now):
        """core.ahk: HL_TapHold.OnDown, TH-L1 .. TH-L4, TH-L10"""
        key = key.lower()
        ev = []
        b = self.cfg["bindings"][key]
        st = self.state[key]

        # TH-L1  Already resolved as a hold. The only thing a further key-down
        #        may do is advance a hold-to-browse switcher. Never re-fire tap.
        if st["phase"] == "held":
            if self._is_browse(st["holdAction"]):
                ev.append(
                    {"type": "builtin", "name": "altTabBrowse", "phase": "repeat"}
                )
            return ev

        # TH-L2  Autorepeat arriving while still undecided, after a tap-only
        #        key already fired, or after a turn consumed the press. Swallow
        #        it: correctness rule 2, no autorepeat of the tap action.
        if st["phase"] in ("pending", "downfired", "consumed"):
            return ev

        # TH-L10 A new press inside the tap window: the pending single/double
        #        tap is not final yet, so stop the window timer and count on.
        if st["phase"] == "waiting":
            ev.append({"type": "cancelWait", "key": key})
            st["downAt"] = now
            st["phase"] = "pending"
            if self._hold_for(b, st["count"] + 1) != "":
                ev.append({"type": "armTimer", "key": key, "ms": self.tapHoldMs})
            return ev

        # TH-L3  Fresh press of a tap-only key: fire immediately on key-down,
        #        no discrimination delay at all.
        st["downAt"] = now
        st["count"] = 0
        if not b["hasHold"] and not b["multi"]:
            st["phase"] = "downfired"
            ev.append({"type": "action", "action": b["tap"], "phase": "tap"})
            return ev

        # TH-L4  Fresh press of a tap-hold key: nothing is emitted yet, arm the
        #        discriminator (only if a hold is possible for this press).
        st["phase"] = "pending"
        if self._hold_for(b, 1) != "":
            ev.append({"type": "armTimer", "key": key, "ms": self.tapHoldMs})
        return ev

    def on_hold_timer(self, key, now):
        """core.ahk: HL_TapHold.OnHoldTimer, TH-L5 .. TH-L6"""
        key = key.lower()
        ev = []
        b = self.cfg["bindings"][key]
        st = self.state[key]

        # TH-L5  A cancelled timer can still land (a one-shot already in
        #        flight). Only a key still pending may become a hold.
        if st["phase"] != "pending":
            return ev

        # TH-L6  Threshold crossed: the hold fires exactly once. The hold is
        #        tapHold when this is the second press of a tap-then-hold.
        st["phase"] = "held"
        h = self._hold_for(b, st["count"] + 1)
        st["holdAction"] = h
        ph = "hold" if st["count"] == 0 else "tapHold"
        st["count"] = 0
        if self._is_browse(h):
            ev.append({"type": "builtin", "name": "altTabBrowse", "phase": "start"})
        else:
            ev.append({"type": "action", "action": h, "phase": ph})
        return ev

    def on_up(self, key, now):
        """core.ahk: HL_TapHold.OnUp, TH-L7 .. TH-L8, TH-L11"""
        key = key.lower()
        ev = []
        b = self.cfg["bindings"][key]
        st = self.state[key]

        # TH-L7  Released before the threshold: it was a tap. Kill the pending
        #        timer first so the hold can never also fire.
        if st["phase"] == "pending":
            ev.append({"type": "cancelTimer", "key": key})
            if not b["multi"]:
                ev.append({"type": "action", "action": b["tap"], "phase": "tap"})
            else:
                # TH-L11 Multi-tap: count it. If another tap can still follow,
                #        open the window; otherwise this tap count is final.
                n = st["count"] + 1
                if self._wait_more(b, n):
                    st["count"] = n
                    st["phase"] = "waiting"
                    ev.append({"type": "armWait", "key": key, "ms": self.tapWindowMs})
                    return ev
                ev.append(
                    {
                        "type": "action",
                        "action": self._tap_for(b, n),
                        "phase": self._tap_phase(b, n),
                    }
                )
        elif st["phase"] == "held":
            # TH-L8  Hold already fired. Only a browse-style hold has a release
            #        phase (it must let go of Alt to commit the selection).
            if self._is_browse(st["holdAction"]):
                ev.append({"type": "builtin", "name": "altTabBrowse", "phase": "end"})
        st["phase"] = "idle"
        st["count"] = 0
        st["holdAction"] = ""
        return ev

    def on_wait_timer(self, key, now):
        """core.ahk: HL_TapHold.OnWaitTimer, TH-L12"""
        key = key.lower()
        ev = []
        b = self.cfg["bindings"][key]
        st = self.state[key]
        if st["phase"] != "waiting":
            return ev
        n = st["count"]
        st["phase"] = "idle"
        st["count"] = 0
        ev.append(
            {
                "type": "action",
                "action": self._tap_for(b, n),
                "phase": self._tap_phase(b, n),
            }
        )
        return ev


# ---------------------------------------------------------------------------
# Config path + change watching  (core.ahk: HL_ResolveConfigPath / HL_ConfigWatch)
# ---------------------------------------------------------------------------


def resolve_config_path(app_data, script_dir, exists=os.path.isfile):
    """core.ahk: HL_ResolveConfigPath. The app-written file wins when present."""
    if app_data:
        p = app_data + "\\aula-desktop\\hostlayer.json"
        if exists(p):
            return p
    return script_dir + "\\layers.json"


class ConfigWatch:
    """core.ahk: HL_ConfigWatch. A change must be seen on two consecutive polls
    before step() returns True, so a half-written file is never read."""

    def __init__(self, sig):
        self.applied = sig
        self.seen = sig

    def mark(self, sig):
        self.applied = sig
        self.seen = sig

    def step(self, sig):
        if sig == self.applied:
            self.seen = sig
            return False
        if sig == self.seen:
            self.applied = sig
            return True
        self.seen = sig
        return False


# ---------------------------------------------------------------------------
# Layer mapping resolution  (core.ahk: HL_ResolveLayerKey)
# ---------------------------------------------------------------------------


def resolve_layer_key(c, key, active_toggles):
    """core.ahk: HL_ResolveLayerKey, LR-L1 .. LR-L3"""
    key = key.lower()

    # LR-L1  Protected keys can never reach here: the validator rejects them,
    #        so the engine never registers a hotkey for them. Space stays Space
    #        even while a sticky layer is on.
    if key in PROTECTED_KEYS:
        return {
            "kind": "passthrough",
            "layer": "",
            "send": KEY_SEND[key],
            "blind": 1,
        }

    # LR-L2  Sticky layers, alphabetical so two active toggles resolve
    #        deterministically.
    for ln in c["toggleLayers"]:
        if ln in active_toggles and key in c["layers"][ln]["keys"]:
            return {
                "kind": "toggle",
                "layer": ln,
                "send": c["layers"][ln]["keys"][key],
                "blind": c["layers"][ln]["blind"],
            }

    # LR-L3  Nothing claims it.
    return {
        "kind": "passthrough",
        "layer": "",
        "send": c["rawSend"].get(key, KEY_SEND.get(key, "")),
        "blind": 1,
    }


# ---------------------------------------------------------------------------
# Test harness: the engine.ahk side, with a fake clock
# ---------------------------------------------------------------------------


class FakeEngine:
    """Mirrors engine.ahk's ExecEvents / DoAction / OnLayerKey.

    The clock is a plain integer advanced by the test. Timers are a dict of
    key -> due time, exactly what SetTimer(fn, -ms) and SetTimer(fn, 0) model.
    Nothing here touches a keyboard, a window, or a real clock.
    """

    def __init__(self, compiled, active_exe=""):
        self.cfg = compiled
        self.machine = TapHold(compiled)
        self.now = 0
        self.timers = {}
        self.emitted = []
        self.active_exe = active_exe
        self.active_toggles = {}
        self.alt_held = False

    # -- clock ----------------------------------------------------------
    def advance(self, ms):
        """Move the clock, firing any timer that comes due on the way.

        Timers are keyed by key for the tap-hold discriminator and "wait:"+key
        for the multi-tap window, like TimerFn/WaitFn in engine.ahk."""
        target = self.now + ms
        while True:
            due = [(t, k) for k, t in self.timers.items() if t <= target]
            if not due:
                break
            due.sort()
            t, k = due[0]
            del self.timers[k]
            self.now = t
            if k.startswith("wait:"):
                self._exec(self.machine.on_wait_timer(k[5:], self.now))
            else:
                self._exec(self.machine.on_hold_timer(k, self.now))
        self.now = target

    # -- input ----------------------------------------------------------
    def press(self, key):
        self._exec(self.machine.on_down(key, self.now))

    def release(self, key):
        self._exec(self.machine.on_up(key, self.now))

    def tap(self, key, ms=10):
        self.press(key)
        self.advance(ms)
        self.release(key)

    # -- engine.ahk: ExecEvents ------------------------------------------
    def _exec(self, evs):
        for ev in evs:
            t = ev["type"]
            if t == "armTimer":
                self.timers[ev["key"]] = self.now + ev["ms"]
            elif t == "cancelTimer":
                self.timers.pop(ev["key"], None)
            elif t == "armWait":
                self.timers["wait:" + ev["key"]] = self.now + ev["ms"]
            elif t == "cancelWait":
                self.timers.pop("wait:" + ev["key"], None)
            elif t == "builtin":
                self._builtin(ev["name"], ev["phase"])
            elif t == "action":
                self._action(ev["action"])

    # -- engine.ahk: DoAction --------------------------------------------
    def _action(self, a):
        a = resolve_app(a, self.active_exe, self.cfg["exeIndex"])
        kind = a["kind"]
        if kind == "swallow":
            self.emitted.append("swallow")
        elif kind == "layerToggle":
            self._toggle(a["layer"])
        elif kind == "builtin":
            self._builtin(a["builtin"], "tap")
        elif kind == "focusApp":
            self.emitted.append("focus:" + a["exe"])
        elif kind == "send":
            self.emitted.append(
                "send:" + ("{Blind}" if a["blind"] else "") + a["send"]
            )

    # -- engine.ahk: DoBuiltin -------------------------------------------
    def _builtin(self, name, phase):
        if name == "altTabTap":
            self.emitted.append("send:!{Tab}")
            return
        if name == "altTabBrowse":
            if phase == "start":
                self.emitted.append("send:{Alt down}{Tab}")
                self.alt_held = True
            elif phase == "repeat":
                if not self.alt_held:
                    self.emitted.append("send:{Alt down}")
                    self.alt_held = True
                self.emitted.append("send:{Tab}")
            elif phase == "end":
                if self.alt_held:
                    self.emitted.append("send:{Alt up}")
                    self.alt_held = False

    # -- engine.ahk: ToggleLayer -----------------------------------------
    def _toggle(self, name):
        if name in self.active_toggles:
            del self.active_toggles[name]
        else:
            self.active_toggles[name] = 1
        self.emitted.append(
            "layer:%s=%s" % (name, "on" if name in self.active_toggles else "off")
        )

    # -- engine.ahk: OnLayerKey / LayerCtx --------------------------------
    def layer_ctx(self, key):
        """engine.ahk: LayerCtx. False means the key is never intercepted."""
        key = key.lower()
        if not self.active_toggles:
            return False
        if key not in self.cfg["layerKeys"]:
            return False
        for ln in self.cfg["toggleLayers"]:
            if ln in self.active_toggles and key in self.cfg["layers"][ln]["keys"]:
                return True
        return False

    def type_key(self, key):
        """Press a normal key. Goes through the layer engine only if the
        context says so, exactly like the real hotkey criterion."""
        if not self.layer_ctx(key):
            self.emitted.append("passthru:" + key.lower())
            return
        r = resolve_layer_key(self.cfg, key, self.active_toggles)
        self.emitted.append(
            "send:" + ("{Blind}" if r["blind"] else "") + r["send"]
        )

    def drain(self):
        out = self.emitted
        self.emitted = []
        return out


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SERVICE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAYERS_JSON = os.path.join(SERVICE_DIR, "layers.json")
LAYERS_EXAMPLE_JSON = os.path.join(SERVICE_DIR, "layers.example.json")
