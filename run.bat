@echo off
rem Opens the JARVIS window (no console). Use run-text-mode.bat to type in a console instead.
cd /d "%~dp0"
start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0jarvis.py" %*
