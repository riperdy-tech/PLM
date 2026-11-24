' Daily channel report, run by the Windows task "PLM Channel Daily Report" with no
' console window. Runs scripts\channel_daily_report.py from the repository root; the
' report goes to data\analytics-reports\<date>.md, the output to
' data\logs\channel_report.log.
Option Explicit

Dim fso, shell, root, python, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
python = root & "\.venv\Scripts\python.exe"
shell.CurrentDirectory = root
cmd = "cmd.exe /c """"" & python & """ -X utf8 scripts\channel_daily_report.py" _
    & " --channel-id UC3Ps0F0z8Mw9ftakIcZ1cWA" _
    & " --reach-job-id dc7dc5a3-5cfa-43ef-b86f-41f69a1c90d6 --reach-ctr-unit ratio" _
    & " >> data\logs\channel_report.log 2>&1"""
shell.Run cmd, 0, True
