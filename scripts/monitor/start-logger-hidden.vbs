' Starts ping-logger.ps1 with no console window. Used by the Startup-folder shortcut
' created by install-autostart.ps1. ping-logger.ps1 itself refuses to start twice.
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & dir & "\ping-logger.ps1"""
CreateObject("WScript.Shell").Run cmd, 0, False
