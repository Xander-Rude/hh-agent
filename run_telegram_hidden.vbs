Set Shell = CreateObject("WScript.Shell")
Shell.Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""C:\hh-agent\run_telegram.ps1""", 0, True
