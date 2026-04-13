@echo off
setlocal
title Start DI Workbench v3

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

start "DI Workbench Backend" cmd /k "cd /d ""%SCRIPT_DIR%"" && python -m uvicorn server:app --reload --host 127.0.0.1 --port 8000"
timeout /t 2 /nobreak >nul
start "DI Workbench Frontend" cmd /k "cd /d ""%SCRIPT_DIR%"" && python -m streamlit run app.py --server.port 8501 --server.headless true"
timeout /t 3 /nobreak >nul
start "" http://localhost:8501