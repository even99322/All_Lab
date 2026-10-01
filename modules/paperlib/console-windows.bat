@echo off
chcp 65001 >nul
setlocal
rem LAB-QEL paperlib console launcher (Windows). First run creates a Python environment and installs paramiko.
set "HERE=%~dp0"
set "ENV=%APPDATA%\paperlib-console\venv"
if exist "%ENV%\Scripts\pythonw.exe" goto check
echo First run: preparing the console, please wait about 1 minute...
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto nopy
%PY% -m venv "%ENV%"
if not exist "%ENV%\Scripts\pythonw.exe" goto nopy
:check
"%ENV%\Scripts\python.exe" -c "import paramiko" >nul 2>nul
if errorlevel 1 (
  echo Installing paramiko for NAS connection...
  "%ENV%\Scripts\python.exe" -m pip install --disable-pip-version-check -q paramiko
  if errorlevel 1 echo [!] paramiko install failed. Monitoring still works; NAS functions are disabled.
)
start "" "%ENV%\Scripts\pythonw.exe" "%HERE%tools\console\paperlib_console.py"
exit /b 0
:nopy
echo.
echo Python 3.10 or newer was not found.
echo Please install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH", then run this again.
echo.
pause
exit /b 1
