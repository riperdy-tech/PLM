' PLM Studio launcher: one click, no console window.
'
' Runs scripts\plm_studio_launch.ps1, which starts whatever is not running (Docker
' Desktop, the PLM services, the dashboard), frees Windows-reserved ports with one
' administrator prompt if needed, then opens the Studio. It shows a small status window
' and explains any failure; its log is data\logs\launcher.log.
Option Explicit

Dim fso, shell, here, script
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
script = here & "\scripts\plm_studio_launch.ps1"

If Not fso.FileExists(script) Then
  MsgBox "Launcher script not found: " & script, 16, "PLM Studio"
  WScript.Quit 1
End If

shell.CurrentDirectory = here
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & script & """", 0, False
