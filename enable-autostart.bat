@echo off
schtasks /change /tn "JARVIS" /enable >nul 2>&1 || schtasks /create /tn "JARVIS" /xml "%~dp0jarvis-task.xml" /f
echo JARVIS will start at login, on unlock, and when the laptop wakes from sleep.
pause
