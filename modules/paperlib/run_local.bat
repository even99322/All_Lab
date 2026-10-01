@echo off
chcp 65001 >nul
REM 在 Windows 電腦上執行（不用 Docker）。第一次會建立虛擬環境並安裝套件。
cd /d "%~dp0"
if "%PAPERLIB_DATA%"=="" set PAPERLIB_DATA=%cd%\data
if not exist .venv (
  py -3 -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
)
echo 資料夾：%PAPERLIB_DATA%    網址：http://本機IP:8080
.venv\Scripts\uvicorn app.main:app --host 0.0.0.0 --port 8080
