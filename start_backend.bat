@echo off
setlocal
title DI Workbench Backend

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

python -m uvicorn server:app --reload --host 127.0.0.1 --port 8000
pause