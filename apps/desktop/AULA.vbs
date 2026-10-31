' Starts the AULA F75 Max app with no console window.
' Pass "live" (the desktop shortcut does) to let Send really write to the keyboard.
Set sh = CreateObject("WScript.Shell")
dir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
If WScript.Arguments.Count > 0 Then
  If WScript.Arguments(0) = "live" Then sh.Environment("Process")("AULA_LIVE_WRITES") = "1"
End If
sh.CurrentDirectory = dir
sh.Run """" & dir & "\node_modules\electron\dist\electron.exe"" """ & dir & """", 1, False
