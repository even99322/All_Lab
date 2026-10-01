@echo off
setlocal
rem Build dist\LabControlMonitor.exe  (Windows, Python 3.9+)
rem All output is also written to deploy\monitor\build_log.txt
cd /d "%~dp0..\.."
echo Lab Control Monitor - build exe
echo Folder: %cd%
echo.

set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (
  python --version >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [ERROR] Python not found.
  echo Install Python 3.12 from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during installation, then run this file again.
  echo.
  pause
  exit /b 1
)
echo Using: %PY%
%PY% "%~dp0build_exe.py"
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo [ERROR] Build failed. Please send deploy\monitor\build_log.txt to the developer.
pause
exit /b %RC%
