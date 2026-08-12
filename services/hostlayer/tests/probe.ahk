#Requires AutoHotkey v2.0
#SingleInstance Force

if (A_Args.Length < 2) {
    FileAppend("usage: probe.ahk <output> <key> [timeout-ms]`n", "*")
    ExitApp(2)
}

output := A_Args[1]
key := A_Args[2]
timeoutMs := A_Args.Length >= 3 ? Integer(A_Args[3]) : 8000

try FileDelete(output)
Hotkey("~*" key, Record.Bind(key), "On")
SetTimer(Timeout, -timeoutMs)

Record(name, *) {
    global output
    FileAppend(name "`n", output, "UTF-8")
    ExitApp(0)
}

Timeout() {
    ExitApp(1)
}
