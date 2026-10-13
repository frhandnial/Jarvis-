@echo off
rem Launched by Windows at login, unlock and wake. Waits for the internet, then opens the JARVIS window.
cd /d "%~dp0"
title JARVIS
echo Waiting for internet connection...
:waitnet
ping -n 1 8.8.8.8 >nul 2>&1
if errorlevel 1 (
    timeout /t 5 /nobreak >nul
    goto waitnet
)
start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0jarvis.py"
