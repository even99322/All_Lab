@echo off
chcp 65001 >nul
rem QEL Lab 監控程式（Windows，原始碼版）：雙擊執行。建議改用網站「下載」頁的 .exe，不需要安裝 Python。
cd /d "%~dp0"
set "VENV=%USERPROFILE%\QELLab\envs\_monitor"
if exist "%VENV%\Scripts\pythonw.exe" goto run
echo 第一次執行：建立 Python 環境…
set "PY="
py -3.12 -c "import sys" >nul 2>&1 && set "PY=py -3.12"
if not defined PY py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>&1 && set "PY=python"
if not defined PY (
  echo.
  echo 找不到 Python 3.9 以上。請到網站「下載」頁下載 .exe 版（不需要 Python），
  echo 或到 https://www.python.org 安裝 Python 3.12（安裝時勾選 Add python.exe to PATH）。
  pause
  exit /b 1
)
%PY% -m venv "%VENV%" || goto fail
"%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt || goto fail
:run
start "" "%VENV%\Scripts\pythonw.exe" main.py
exit /b 0
:fail
echo.
echo 建立環境失敗，請把上面的訊息拍照給站長。
rmdir /s /q "%VENV%" >nul 2>&1
pause
exit /b 1
