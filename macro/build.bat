@echo off
chcp 65001 >nul
echo ===== 매크로 빌드 시작 =====

echo 패키지 설치 중...
python -m pip install pyinstaller pywin32 windows-capture vgamepad opencv-python numpy pyautogui pillow >nul 2>&1

echo vgamepad DLL 경로 탐색 중...
for /f "delims=" %%i in ('python -c "import vgamepad, os; print(os.path.dirname(vgamepad.__file__))"') do set VGAMEPAD_DIR=%%i
echo vgamepad 경로: %VGAMEPAD_DIR%

echo 빌드 중...
python -m PyInstaller --onefile --noconsole --uac-admin --name macro --add-data "%VGAMEPAD_DIR%\win;vgamepad\win" main.py

if not exist "dist\macro.exe" (
    echo 빌드 실패!
    pause
    exit /b 1
)

echo.
echo ===== 배포 패키지 생성 =====
if exist "dist\release" rmdir /s /q "dist\release"
mkdir "dist\release"

copy "dist\macro.exe" "dist\release\" >nul
copy "targets.json" "dist\release\" >nul
for %%f in (target_*.png) do copy "%%f" "dist\release\" >nul

echo 압축 중...
if exist "dist\macro_release.zip" del "dist\macro_release.zip"
powershell -Command "Compress-Archive -Path 'dist\release\*' -DestinationPath 'dist\macro_release.zip' -Force"

echo.
echo ===== 완료 =====
echo EXE: dist\macro.exe
echo ZIP: dist\macro_release.zip
for %%A in ("dist\macro_release.zip") do echo 크기: %%~zA bytes
echo.
echo GitHub Releases에 macro_release.zip을 업로드하세요.
pause
