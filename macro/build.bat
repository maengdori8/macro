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

if exist "dist\macro.exe" (
    echo.
    echo ===== 빌드 완료 =====
    echo 파일: dist\macro.exe
    echo.
    for %%A in ("dist\macro.exe") do echo 크기: %%~zA bytes
) else (
    echo 빌드 실패!
)
pause
