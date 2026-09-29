@echo off
rem Launched by Windows at login. Waits for the internet to come up, then starts JARVIS.
cd /d "%~dp0"
title JARVIS
echo Waiting for internet connection...
:waitnet
ping -n 1 8.8.8.8 >nul 2>&1
if errorlevel 1 (
    timeout /t 5 /nobreak >nul
    goto waitnet
)
call venv\Scripts\activate.bat
python jarvis.py
