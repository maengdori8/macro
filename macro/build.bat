@echo off
chcp 65001 >nul
echo ===== 매크로 빌드 시작 =====

echo 패키지 설치 중...
python -m pip install pyinstaller pywin32 windows-capture vgamepad opencv-python numpy pyautogui pillow >nul 2>&1

echo 빌드 중...
python -m PyInstaller --onefile --noconsole --uac-admin --name macro main.py

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
