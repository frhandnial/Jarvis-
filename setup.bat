@echo off
cd /d "%~dp0"
echo Creating virtual environment...
python -m venv venv
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Setup done! Now open the .env file and paste your API key, then double-click run.bat
pause
