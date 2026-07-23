@echo off
setlocal
cd /d "%~dp0"

echo [1/3] Checking renewal source...
python -m compileall -q macroapp renewal_macro.py
if errorlevel 1 goto :failed

echo [2/3] Building renewal_macro.exe...
taskkill /f /im renewal_macro.exe >nul 2>&1
if exist "dist\renewal_macro.exe" del /f /q "dist\renewal_macro.exe"

python -m nuitka --onefile --assume-yes-for-downloads ^
  --lto=no --jobs=%NUMBER_OF_PROCESSORS% --remove-output --python-flag=-OO ^
  --output-dir=dist --output-filename=renewal_macro.exe ^
  --windows-console-mode=disable --windows-uac-admin ^
  --enable-plugin=tk-inter ^
  renewal_macro.py

if exist "dist\renewal_macro.exe" goto :success

echo Nuitka failed. Falling back to PyInstaller...
python -m PyInstaller --onefile --noconsole --uac-admin ^
  --name renewal_macro --distpath dist renewal_macro.py
if not exist "dist\renewal_macro.exe" goto :failed

:success
echo [3/3] Build complete: dist\renewal_macro.exe
exit /b 0

:failed
echo Renewal macro build failed.
exit /b 1
