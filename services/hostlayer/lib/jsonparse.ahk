#Requires AutoHotkey v2.0
; ---------------------------------------------------------------------------
; jsonparse.ahk - minimal, dependency-free JSON parser for AutoHotkey v2.
;
; Why hand-rolled: the config is a few KB of trusted local JSON. Pulling in
; cJson (a DLL) or JXON (RegEx-heavy, v1 heritage) buys nothing here and both
; add a failure mode the /validate gate cannot see. This is a straight
; recursive-descent parser over the string, no COM, no RegEx, no DLL.
;
; Mapping:
;   object -> Map (CaseSense "On", insertion is irrelevant to callers)
;   array  -> Array
;   string -> String
;   number -> Number
;   true   -> 1
;   false  -> 0
;   null   -> "" (the config schema never uses null; the validator rejects it
;                 wherever a real value is required)
;
; Errors are thrown as Error with a 1-based character offset, so /validate can
; point at the offending byte instead of saying "bad JSON".
; ---------------------------------------------------------------------------

class Json {
    ; Parse(text) -> value. Strips a leading UTF-8 BOM (U+FEFF) if present.
    static Parse(text) {
        if (SubStr(text, 1, 1) == Chr(0xFEFF))
            text := SubStr(text, 2)
        ctx := {s: text, i: 1, n: StrLen(text)}
        Json._Ws(ctx)
        v := Json._Value(ctx)
        Json._Ws(ctx)
        if (ctx.i <= ctx.n)
            throw Error("JSON: unexpected trailing content at char " ctx.i)
        return v
    }

    static _Peek(c) {
        return (c.i <= c.n) ? SubStr(c.s, c.i, 1) : ""
    }

    static _Ws(c) {
        while (c.i <= c.n) {
            ch := SubStr(c.s, c.i, 1)
            if (ch == " " || ch == "`t" || ch == "`n" || ch == "`r")
                c.i += 1
            else
                break
        }
    }

    static _Expect(c, ch) {
        if (Json._Peek(c) !== ch)
            throw Error("JSON: expected '" ch "' at char " c.i)
        c.i += 1
    }

    static _Value(c) {
        ch := Json._Peek(c)
        if (ch == "")
            throw Error("JSON: unexpected end of input")
        if (ch == "{")
            return Json._Object(c)
        if (ch == "[")
            return Json._Array(c)
        if (ch == '"')
            return Json._String(c)
        if (ch == "t") {
            Json._Lit(c, "true")
            return 1
        }
        if (ch == "f") {
            Json._Lit(c, "false")
            return 0
        }
        if (ch == "n") {
            Json._Lit(c, "null")
            return ""
        }
        return Json._Number(c)
    }

    static _Lit(c, word) {
        if (SubStr(c.s, c.i, StrLen(word)) !== word)
            throw Error("JSON: invalid literal at char " c.i)
        c.i += StrLen(word)
    }

    static _Object(c) {
        Json._Expect(c, "{")
        obj := Map()
        Json._Ws(c)
        if (Json._Peek(c) == "}") {
            c.i += 1
            return obj
        }
        loop {
            Json._Ws(c)
            if (Json._Peek(c) !== '"')
                throw Error("JSON: object key must be a string, at char " c.i)
            k := Json._String(c)
            Json._Ws(c)
            Json._Expect(c, ":")
            Json._Ws(c)
            if (obj.Has(k))
                throw Error("JSON: duplicate key '" k "' at char " c.i)
            obj[k] := Json._Value(c)
            Json._Ws(c)
            ch := Json._Peek(c)
            if (ch == ",") {
                c.i += 1
                continue
            }
            if (ch == "}") {
                c.i += 1
                return obj
            }
            throw Error("JSON: expected ',' or '}' at char " c.i)
        }
    }

    static _Array(c) {
        Json._Expect(c, "[")
        arr := []
        Json._Ws(c)
        if (Json._Peek(c) == "]") {
            c.i += 1
            return arr
        }
        loop {
            Json._Ws(c)
            arr.Push(Json._Value(c))
            Json._Ws(c)
            ch := Json._Peek(c)
            if (ch == ",") {
                c.i += 1
                continue
            }
            if (ch == "]") {
                c.i += 1
                return arr
            }
            throw Error("JSON: expected ',' or ']' at char " c.i)
        }
    }

    static _String(c) {
        Json._Expect(c, '"')
        out := ""
        while (c.i <= c.n) {
            ch := SubStr(c.s, c.i, 1)
            if (ch == '"') {
                c.i += 1
                return out
            }
            if (ch == "\") {
                c.i += 1
                esc := Json._Peek(c)
                c.i += 1
                if (esc == '"')
                    out .= '"'
                else if (esc == "\")
                    out .= "\"
                else if (esc == "/")
                    out .= "/"
                else if (esc == "b")
                    out .= Chr(8)
                else if (esc == "f")
                    out .= Chr(12)
                else if (esc == "n")
                    out .= "`n"
                else if (esc == "r")
                    out .= "`r"
                else if (esc == "t")
                    out .= "`t"
                else if (esc == "u") {
                    hex := SubStr(c.s, c.i, 4)
                    if (StrLen(hex) < 4)
                        throw Error("JSON: truncated \u escape at char " c.i)
                    out .= Chr(Integer("0x" hex))
                    c.i += 4
                } else
                    throw Error("JSON: invalid escape at char " (c.i - 1))
                continue
            }
            out .= ch
            c.i += 1
        }
        throw Error("JSON: unterminated string")
    }

    static _Number(c) {
        start := c.i
        if (Json._Peek(c) == "-")
            c.i += 1
        while (c.i <= c.n) {
            ch := SubStr(c.s, c.i, 1)
            if (InStr("0123456789+-.eE", ch) && ch !== "")
                c.i += 1
            else
                break
        }
        raw := SubStr(c.s, start, c.i - start)
        if (raw == "" || !IsNumber(raw))
            throw Error("JSON: invalid number at char " start)
        return Number(raw)
    }
}
