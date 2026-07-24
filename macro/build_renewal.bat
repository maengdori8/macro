@echo off
setlocal
cd /d "%~dp0"

echo [1/3] Checking renewal source...
python -m compileall -q macroapp renewal_macro.py
if errorlevel 1 goto :failed

echo [2/3] Building local PyInstaller renewal_macro.exe...
if not exist "dist_local_renewal" mkdir "dist_local_renewal"
if exist "dist_local_renewal\renewal_macro.exe" del /f /q "dist_local_renewal\renewal_macro.exe"
if exist "dist_local_renewal\renewal_macro.exe" (
  echo Existing local renewal_macro.exe is locked; close it and retry.
  goto :failed
)

python -m PyInstaller --noconfirm --clean ^
  --distpath "dist_local_renewal" ^
  --workpath "build_local_renewal" ^
  "renewal_macro.spec"
if errorlevel 1 goto :failed
if not exist "dist_local_renewal\renewal_macro.exe" goto :failed

:success
copy /y "version.txt" "dist_local_renewal\version.txt" >nul
if not exist "dist_local_renewal\version.txt" goto :failed
echo [3/3] Local build complete: dist_local_renewal\renewal_macro.exe
exit /b 0

:failed
echo Renewal macro build failed.
exit /b 1
