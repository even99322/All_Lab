@echo off
cd /d "%~dp0"
py -3 -m venv .venv
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
call Launch.bat
exit /b 0
:fail
echo Installation failed. Check the error message above.
pause
exit /b 1
