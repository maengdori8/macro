@echo off
chcp 65001 >nul
echo ===== 매크로 빌드 시작 (Nuitka, 디컴파일 방어) =====

:: version.txt에서 버전 읽기
set APP_VER=0.0.0
if exist "version.txt" (
    set /p APP_VER=<version.txt
)
echo 버전: %APP_VER%

:: setup.iss의 AppVersion을 version.txt와 동기화
if exist "setup.iss" (
    python -c "import re,sys; t=open('setup.iss','r',encoding='utf-8').read(); t=re.sub(r'AppVersion=.*','AppVersion=%APP_VER%',t); open('setup.iss','w',encoding='utf-8').write(t)"
    if errorlevel 1 (
        echo [오류] setup.iss 버전 동기화 실패. 빌드를 중단합니다.
        pause
        exit /b 1
    )
    echo setup.iss 버전 동기화 완료
)

echo 패키지 설치 중...
python -m pip install --quiet --disable-pip-version-check --no-warn-script-location nuitka pyinstaller pywin32 windows-capture vgamepad opencv-python numpy pyautogui pillow ordered-set zstandard

echo vgamepad DLL 경로 탐색 중...
for /f "delims=" %%i in ('python -c "import vgamepad, os; print(os.path.dirname(vgamepad.__file__))"') do set VGAMEPAD_DIR=%%i

:: 문법 게이트 (느린 컴파일 전에 1초 안에 오류 잡기)
echo 문법 검사 중...
python -m py_compile macroapp\*.py macro_main.py launcher.py gamepad_test.py
if %errorlevel% neq 0 (
    echo [오류] 문법 검사 실패. 빌드를 중단합니다.
    pause
    exit /b 1
)

echo.
echo [1/3] macro.exe 빌드 중... (첫 빌드는 5~10분 걸릴 수 있습니다)
python -m nuitka --onefile --assume-yes-for-downloads ^
  --lto=yes --jobs=%NUMBER_OF_PROCESSORS% --remove-output ^
  --output-dir=dist --output-filename=macro.exe ^
  --windows-console-mode=disable --windows-uac-admin ^
  --enable-plugin=tk-inter ^
  --include-package=macroapp ^
  --include-data-dir="%VGAMEPAD_DIR%\win=vgamepad\win" ^
  macro_main.py
if not exist "dist\macro.exe" (
    echo [경고] Nuitka 빌드 실패 - PyInstaller로 폴백합니다.
    python -m PyInstaller --onefile --noconsole --uac-admin --name macro --add-data "%VGAMEPAD_DIR%\win;vgamepad\win" macro_main.py
)

echo.
echo [2/3] launcher.exe 빌드 중...
python -m nuitka --onefile --assume-yes-for-downloads ^
  --lto=yes --jobs=%NUMBER_OF_PROCESSORS% --remove-output ^
  --output-dir=dist --output-filename=launcher.exe ^
  --windows-console-mode=disable --windows-uac-admin ^
  launcher.py
if not exist "dist\launcher.exe" (
    echo [경고] Nuitka 빌드 실패 - PyInstaller로 폴백합니다.
    python -m PyInstaller --onefile --noconsole --uac-admin --name launcher launcher.py
)

echo.
echo [추가] gamepad_test.exe 빌드 중 (버튼 테스트용, 콘솔 표시)...
python -m nuitka --onefile --assume-yes-for-downloads ^
  --lto=yes --jobs=%NUMBER_OF_PROCESSORS% --remove-output ^
  --output-dir=dist --output-filename=gamepad_test.exe ^
  --windows-uac-admin ^
  --include-data-dir="%VGAMEPAD_DIR%\win=vgamepad\win" ^
  gamepad_test.py
if not exist "dist\gamepad_test.exe" (
    python -m PyInstaller --onefile --uac-admin --name gamepad_test --add-data "%VGAMEPAD_DIR%\win;vgamepad\win" gamepad_test.py
)
if not exist "dist\gamepad_test.exe" echo [경고] gamepad_test.exe 빌드 실패 (버튼 테스트 도구 — 설치엔 선택사항).

:: Nuitka 빌드 부산물 정리
if exist "macro_main.build" rmdir /s /q "macro_main.build"
if exist "macro_main.onefile-build" rmdir /s /q "macro_main.onefile-build"
if exist "launcher.build" rmdir /s /q "launcher.build"
if exist "launcher.onefile-build" rmdir /s /q "launcher.onefile-build"
if exist "gamepad_test.build" rmdir /s /q "gamepad_test.build"
if exist "gamepad_test.onefile-build" rmdir /s /q "gamepad_test.onefile-build"

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
