@echo off
setlocal
if exist "%~dp0.venv\Scripts\python.exe" (
    echo ==========================================================
    echo   PLM Autonomous Music Engine - Visual Control Deck
    echo   URL: http://127.0.0.1:8000
    echo ==========================================================
    "%~dp0.venv\Scripts\python.exe" -m plm_platform.cli dashboard %*
) else (
    echo [ERROR] Virtual environment not found at .venv.
    pause
)
