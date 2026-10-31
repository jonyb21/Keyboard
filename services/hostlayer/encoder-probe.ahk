#Requires AutoHotkey v2.0
; ---------------------------------------------------------------------------
; encoder-probe.ahk - READ-ONLY check of what the AULA knob actually sends.
;
; Registers raw input listeners only: no hotkeys, no keyboard hook, nothing is
; blocked or sent, so it is safe beside the CIDOO runtime. Turn the knob left,
; right and press it, then read encoder-probe.log next to this script. Each
; line names the device and the key (or HID bytes) it reported. Exit from the
; tray icon. Don't type while it runs. It listens on the keyboard and consumer
; collections only: a knob that reports as a mouse wheel or vendor page will not
; show up. Run it from the desktop session, not over SSH. If the knob's keys are not Volume_Down / Volume_Up / Volume_Mute,
; change HL_EncoderSources() in core.ahk to match.
; ---------------------------------------------------------------------------

#SingleInstance Force
Persistent(true)

global gLog := A_ScriptDir "\encoder-probe.log"
global gNames := Map()

sz := 8 + A_PtrSize
buf := Buffer(sz * 2, 0)
for i, pair in [[0x01, 0x06], [0x0C, 0x01]] {
    o := (i - 1) * sz
    NumPut("UShort", pair[1], "UShort", pair[2], "UInt", 0x100, buf, o)   ; RIDEV_INPUTSINK
    NumPut("Ptr", A_ScriptHwnd, buf, o + 8)
}
if (!DllCall("RegisterRawInputDevices", "Ptr", buf, "UInt", 2, "UInt", sz)) {
    MsgBox("RegisterRawInputDevices failed, error " A_LastError, "encoder probe", 0x10)
    ExitApp(1)
}
OnMessage(0x00FF, OnRawInput)
FileAppend("--- probe started " A_Now " ---`n", gLog, "UTF-8")
TrayTip("Turn and press the AULA knob (don't type), then read encoder-probe.log.", "Encoder probe", 0x1)

OnRawInput(wParam, lParam, *) {
    hdr := 8 + 2 * A_PtrSize
    size := 0
    DllCall("GetRawInputData", "Ptr", lParam, "UInt", 0x10000003, "Ptr", 0, "UInt*", &size, "UInt", hdr, "Int")
    if (size < hdr)
        return
    b := Buffer(size)
    if (DllCall("GetRawInputData", "Ptr", lParam, "UInt", 0x10000003, "Ptr", b, "UInt*", &size, "UInt", hdr, "Int") < hdr)
        return
    name := DeviceName(NumGet(b, 8, "Ptr"))
    if (!InStr(name, "VID_0C45&PID_800A") && !InStr(name, "VID_05AC&PID_024F"))
        return                                  ; ordinary typing on other boards is not logged
    typ := NumGet(b, 0, "UInt")
    if (typ == 1) {
        vk := NumGet(b, hdr + 6, "UShort"), up := NumGet(b, hdr + 2, "UShort") & 1
        if (vk == 0x20 || (vk >= 0x30 && vk <= 0x5A))
            return                              ; letters, digits and Space are never logged
        line := Format("key vk=0x{:02X} {} ({})", vk, GetKeyName(Format("vk{:02X}", vk)), up ? "up" : "down")
    } else if (typ == 2) {
        n := NumGet(b, hdr, "UInt"), cnt := NumGet(b, hdr + 4, "UInt"), hex := ""
        loop n * cnt
            hex .= Format("{:02X} ", NumGet(b, hdr + 8 + A_Index - 1, "UChar"))
        line := "hid " Trim(hex)
    } else {
        return
    }
    FileAppend(A_Hour ":" A_Min ":" A_Sec "." A_MSec "  " line "  " name "`n", gLog, "UTF-8")
    ToolTip(line)
    SetTimer(() => ToolTip(), -1500)
}

DeviceName(h) {
    global gNames
    if (gNames.Has(h))
        return gNames[h]
    n := 0, name := ""
    DllCall("GetRawInputDeviceInfoW", "Ptr", h, "UInt", 0x20000007, "Ptr", 0, "UInt*", &n, "Int")
    if (n > 0) {
        nb := Buffer(n * 2, 0)
        if (DllCall("GetRawInputDeviceInfoW", "Ptr", h, "UInt", 0x20000007, "Ptr", nb, "UInt*", &n, "Int") > 0)
            name := StrGet(nb, "UTF-16")
    }
    gNames[h] := name
    return name
}
