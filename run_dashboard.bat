@echo off
setlocal
if exist "%~dp0.venv\Scripts\python.exe" (
    echo ==========================================================
    echo   PLM Control Deck - DEMO DATA ^(not connected to the pipeline^)
    echo   URL: printed by the server below ^(default http://127.0.0.1:8000^)
    echo ==========================================================
    "%~dp0.venv\Scripts\python.exe" -m plm_platform.cli dashboard %*
) else (
    echo [ERROR] Virtual environment not found at .venv.
    pause
)
