@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto fail
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --onefile --name "Labber Converter" --icon assets\icon.ico --add-data "assets\icon.png;assets" --exclude-module scipy --exclude-module matplotlib --exclude-module pandas --exclude-module IPython app.py
if errorlevel 1 goto fail
echo Ready: dist\Labber Converter.exe
echo Share only this EXE. Recipients do not need Python or extra program folders.
pause
exit /b 0
:fail
echo Build failed. Check the error message above.
pause
exit /b 1
