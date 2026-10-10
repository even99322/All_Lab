@echo off
rem Lab Control: build a single Windows .exe (double-click this file).
rem Needs Python 3.12 (https://www.python.org/downloads/ , tick "Add python.exe to PATH").
rem Do NOT click inside this window while it runs (Windows pauses it); if you did, press Esc.
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0\.."
set PY=py -3.12
%PY% -c "import sys" 2>nul || set PY=py -3
%PY% -c "import sys" 2>nul || set PY=python
echo Python:
%PY% -c "import sys;print(sys.version)" || goto nopython

echo.
echo [1/3] %time%  Creating build environment (.build-venv) ...
if exist .build-venv\Scripts\python.exe goto venvok
%PY% -m venv .build-venv || goto fail
:venvok
set VPY=.build-venv\Scripts\python.exe

echo.
echo [2/3] %time%  Installing packages (numpy, PyQt6, xarray, PyInstaller ... about 400 MB, first time takes several minutes) ...
%VPY% -m pip install --disable-pip-version-check --upgrade pip || goto fail
%VPY% -m pip install --disable-pip-version-check --progress-bar on -r requirements.txt pyinstaller pillow pyvisa-py || goto fail

echo.
echo [3/3] %time%  Building and self-testing (3-8 minutes, lots of INFO lines) ...
%VPY% packaging\build_app.py || goto fail
echo.
echo %time%  DONE. Drop dist\LabControl_v*_windows.exe into the web console (portal - console - publish all).
explorer dist
pause
exit /b 0

:nopython
echo Python not found. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH").
:fail
echo.
echo BUILD FAILED - please send a screenshot of this window.
pause
exit /b 1
