@echo off
chcp 65001 >nul
title NQ - local server
cd /d "%~dp0"
where py >nul 2>nul && goto haspy
where python >nul 2>nul && goto haspython
echo.
echo   Python is not installed.
echo   Download it, and CHECK "Add python.exe to PATH" during setup, then run this file again.
start https://www.python.org/downloads/
pause
exit /b
:haspy
py -3 dev\run.py %*
goto end
:haspython
python dev\run.py %*
:end
echo.
echo   Server stopped. Close this window or press any key.
pause >nul
