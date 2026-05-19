@echo off
chcp 65001 >nul
echo ===== 매크로 빌드 시작 =====

echo 패키지 설치 중...
python -m pip install pyinstaller pywin32 windows-capture vgamepad opencv-python numpy pyautogui pillow >nul 2>&1

echo vgamepad DLL 경로 탐색 중...
for /f "delims=" %%i in ('python -c "import vgamepad, os; print(os.path.dirname(vgamepad.__file__))"') do set VGAMEPAD_DIR=%%i

echo.
echo [1/3] macro.exe 빌드 중...
python -m PyInstaller --onefile --noconsole --uac-admin --name macro --add-data "%VGAMEPAD_DIR%\win;vgamepad\win" main.py

echo.
echo [2/3] launcher.exe 빌드 중...
python -m PyInstaller --onefile --noconsole --uac-admin --name launcher launcher.py

if not exist "dist\macro.exe" (
    echo macro.exe 빌드 실패!
    pause
    exit /b 1
)
if not exist "dist\launcher.exe" (
    echo launcher.exe 빌드 실패!
    pause
    exit /b 1
)

echo.
echo [3/3] 설치 파일 생성 중...
set ISCC_PATH=
where iscc >nul 2>&1
if %errorlevel% equ 0 (
    set ISCC_PATH=iscc
) else if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
    set "ISCC_PATH=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
) else if exist "C:\Program Files\Inno Setup 6\ISCC.exe" (
    set "ISCC_PATH=C:\Program Files\Inno Setup 6\ISCC.exe"
)

if "%ISCC_PATH%"=="" (
    echo.
    echo [안내] Inno Setup이 설치되어 있지 않습니다.
    echo        https://jrsoftware.org/isdl.php 에서 설치 후 다시 실행하세요.
    echo.
    echo        EXE 빌드는 완료되었습니다:
    echo        - dist\macro.exe
    echo        - dist\launcher.exe
    pause
    exit /b 0
)

"%ISCC_PATH%" setup.iss

if exist "dist\macro_setup.exe" (
    echo.
    echo ===== 빌드 완료 =====
    echo 설치 파일: dist\macro_setup.exe
    for %%A in ("dist\macro_setup.exe") do echo 크기: %%~zA bytes
) else (
    echo 설치 파일 생성 실패!
)
pause
