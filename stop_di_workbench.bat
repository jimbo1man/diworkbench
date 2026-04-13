@echo off
setlocal
title Stop DI Workbench v3

taskkill /f /fi "WINDOWTITLE eq DI Workbench Backend*" >nul 2>&1
taskkill /f /fi "WINDOWTITLE eq DI Workbench Frontend*" >nul 2>&1

echo DI Workbench windows stopped.
pause