' PLM Studio launcher: one click, no console window.
'
' Starts the live dashboard (plm dashboard serve on 127.0.0.1:8000) from this folder's
' virtualenv if it is not already answering, then opens the Studio in the default
' browser. The dashboard's output goes to data\logs\dashboard.log.
'
' The PLM services themselves (Docker: postgres, minio, temporal, the phase APIs, the
' pipeline worker) must already be running; this launcher never starts or restarts them.
Option Explicit

Const URL = "http://127.0.0.1:8000/"
Const HEALTH = "http://127.0.0.1:8000/api/studio"

Dim fso, shell, here, py, logs, i
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = here

Function Answers(u)
  Dim http
  Answers = False
  On Error Resume Next
  Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0")
  http.setTimeouts 2000, 2000, 5000, 5000
  http.Open "GET", u, False
  http.Send
  If Err.Number = 0 Then Answers = (http.Status = 200)
  On Error GoTo 0
End Function

If Not Answers(HEALTH) Then
  py = here & "\.venv\Scripts\python.exe"
  If Not fso.FileExists(py) Then
    MsgBox "Virtualenv not found: " & py, 16, "PLM Studio"
    WScript.Quit 1
  End If
  logs = here & "\data\logs"
  If Not fso.FolderExists(here & "\data") Then fso.CreateFolder here & "\data"
  If Not fso.FolderExists(logs) Then fso.CreateFolder logs
  shell.Run "cmd /c """"" & py & """ -m plm_platform.cli dashboard serve --host 127.0.0.1 --port 8000 > """ & logs & "\dashboard.log"" 2>&1""", 0, False
  For i = 1 To 60
    WScript.Sleep 1000
    If Answers(HEALTH) Then Exit For
  Next
  If Not Answers(HEALTH) Then
    MsgBox "The dashboard did not start within a minute. See data\logs\dashboard.log." _
      & vbCrLf & "Is Docker running with the PLM services up?", 48, "PLM Studio"
    WScript.Quit 1
  End If
End If

shell.Run URL
