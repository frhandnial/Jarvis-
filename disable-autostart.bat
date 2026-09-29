@echo off
schtasks /change /tn "JARVIS" /disable
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\JARVIS.lnk" 2>nul
echo JARVIS will no longer start automatically.
pause
