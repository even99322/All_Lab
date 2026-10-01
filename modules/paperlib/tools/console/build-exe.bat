@echo off
rem Build PaperlibConsole.exe (needs Python 3.10+). Output: dist\PaperlibConsole.exe
cd /d "%~dp0"
py -3 -m pip install --disable-pip-version-check -q pyinstaller paramiko || python -m pip install -q pyinstaller paramiko
py -3 -m PyInstaller --noconfirm --onefile --windowed --name PaperlibConsole --icon console.ico --add-data "console.ico;." --hidden-import paramiko paperlib_console.py
echo.
echo Done: %~dp0dist\PaperlibConsole.exe
pause
