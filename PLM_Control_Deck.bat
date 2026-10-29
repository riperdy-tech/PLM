@echo off
title PLM Control Deck Launcher
cd /d "%~dp0"

echo Starting PLM Autonomous Publishing Control Deck...
set PLM_SERVICE_TOKEN=change-me-dev-service-token
set PLM_DASHBOARD_REVIEWERS_FILE=config\dev\reviewers.json

if exist ".venv\Scripts\python.exe" (
    start "" ".venv\Scripts\python.exe" scripts\plm_desktop_control.py
) else (
    echo [ERROR] Virtualenv not found at .venv\Scripts\python.exe
    pause
)

