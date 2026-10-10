@echo off
rem Run Lab Control Hub on this Windows PC (when the NAS cannot run Docker). Needs Python 3.8+.
rem Data is stored in the "data" folder next to this file. Edit the --releases path below if needed.
cd /d "%~dp0"
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (
  python --version >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [ERROR] Python not found. Install Python 3.12 from https://www.python.org/downloads/
  pause
  exit /b 1
)
%PY% -m labhub --port 8765 --data "%~dp0data" --releases "\\192.168.50.2\ccuqel\Releases"
pause
