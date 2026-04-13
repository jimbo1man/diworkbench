@echo off
setlocal
title DI Workbench Frontend

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

python -m streamlit run app.py --server.port 8501
pause