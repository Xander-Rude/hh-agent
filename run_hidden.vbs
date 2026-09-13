Option Explicit

Dim shell, scriptPath, command, rc

If WScript.Arguments.Count = 0 Then
    WScript.Quit 87
End If

scriptPath = WScript.Arguments(0)

Set shell = CreateObject("WScript.Shell")

command = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ & scriptPath & """"

rc = shell.Run(command, 0, True)

WScript.Quit rc
