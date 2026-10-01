@echo off
rem QEL Lab 大程式（Windows）：雙擊執行。第一次會建立 Python 環境（需要 Python 3.12，python.org 下載）。
chcp 65001 >nul
cd /d "%~dp0"
set VENV=%USERPROFILE%\QELLab\envs\_launcher
if not exist "%VENV%\Scripts\pythonw.exe" (
  echo 第一次執行：建立 Python 環境…
  py -3.12 -m venv "%VENV%" 2>nul || python -m venv "%VENV%"
  "%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
)
start "" "%VENV%\Scripts\pythonw.exe" main.py
