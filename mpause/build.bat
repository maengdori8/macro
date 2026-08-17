@echo off
chcp 65001 >nul
echo ===== mPause 빌드 시작 (Nuitka, 디컴파일 방어) =====

:: version.txt에서 버전 읽기
set APP_VER=0.0.0
if exist "version.txt" (
    set /p APP_VER=<version.txt
)
echo 버전: %APP_VER%

:: setup_mpause.iss의 AppVersion을 version.txt와 동기화
if exist "setup_mpause.iss" (
    python -c "import re,sys; t=open('setup_mpause.iss','r',encoding='utf-8').read(); t=re.sub(r'AppVersion=.*','AppVersion=%APP_VER%',t); open('setup_mpause.iss','w',encoding='utf-8').write(t)"
    if errorlevel 1 (
        echo [오류] setup_mpause.iss 버전 동기화 실패. 빌드를 중단합니다.
        pause
        exit /b 1
    )
    echo setup_mpause.iss 버전 동기화 완료
)

echo 패키지 설치 중...
rem pytest 는 아래 테스트 게이트가 쓴다. 빠지면 클린 PC 에서 'No module named pytest'
rem 가 exit 1 을 내고 '테스트 실패'로 오진해 빌드가 멈춘다.
rem
rem CRITICAL (mAuto 에서 그대로 가져온 함정): opencv-python(일반판)의 cv2.pyd 는
rem Media Foundation(MF*.dll)을 정적 임포트해서 Windows N/KN 에디션에서
rem 'import cv2' 가 무성으로 죽는다(= 더블클릭해도 무반응). headless 는 그 의존성이 없다.
rem 그런데 windows-capture 가 일반 opencv-python 을 의존성으로 끌어온다. 그래서 순서가 중요:
rem  (1) 일반 패키지 먼저 (windows-capture 가 opencv-python 을 끌어옴)
rem  (2) 그 뒤에 opencv-python 을 지우고 headless 를 강제 재설치 → cv2.pyd 의
rem      '마지막 기록자'가 headless 가 되게 한다.
python -m pip install --quiet --disable-pip-version-check --no-warn-script-location nuitka pyinstaller ordered-set zstandard pytest pywin32 windows-capture vgamepad numpy
python -m pip uninstall -y opencv-python opencv-python-headless opencv-contrib-python opencv-contrib-python-headless >nul 2>&1
python -m pip install --quiet --disable-pip-version-check --no-warn-script-location --force-reinstall --no-deps opencv-python-headless

echo cv2 Media Foundation 의존성 검사 중...
python check_cv2_headless.py
if %errorlevel% neq 0 (
    echo [오류] cv2.pyd가 Media Foundation을 임포트합니다. opencv-python-headless 설치 실패.
    echo        pip uninstall opencv-python opencv-python-headless 후 다시 실행하세요.
    pause
    exit /b 1
)

echo vgamepad DLL 경로 탐색 중...
rem find_spec 로 위치만 찾는다(import 하면 ViGEm 드라이버가 없을 때 그 자리에서 죽는다).
for /f "delims=" %%i in ('python -c "import importlib.util,os; s=importlib.util.find_spec('vgamepad'); print(os.path.dirname(s.origin) if s and s.origin else '')"') do set "VGAMEPAD_DIR=%%i"
if not defined VGAMEPAD_DIR (
    echo [오류] vgamepad 패키지를 찾을 수 없습니다. 'pip install vgamepad' 후 다시 실행하세요.
    pause
    exit /b 1
)

rem 라이센스 검증은 서명 공개키가 박혀 있어야만 동작한다. 비어 있으면
rem verify_signed_response가 무조건 거부하므로(fail-closed) 빌드해도 아무도 못 쓴다.
echo 라이센스 공개키 확인 중...
python -c "import re,sys; t=open('mpauseapp/license_client.py','r',encoding='utf-8').read(); m=re.search(r'LICENSE_PUBKEY_HEX\s*=\s*\"([0-9a-f]*)\"',t); sys.exit(0 if m and len(m.group(1))==64 else 1)"
if %errorlevel% neq 0 (
    echo [오류] LICENSE_PUBKEY_HEX가 비어 있거나 형식이 잘못되었습니다.
    echo        mpauseapp\license_client.py에 서버 공개키 64자리 hex를 넣고 다시 빌드하세요.
    pause
    exit /b 1
)

rem Syntax gate before the slow compile. cmd does not expand wildcards,
rem so use compileall (handles directories) instead of py_compile.
rem (NOTE: keep this comment ASCII - cmd misparses some UTF-8 Korean in :: comments)
echo 문법 검사 중...
python -m compileall -q mpauseapp mpause_main.py ed25519_tiny.py check_cv2_headless.py
if %errorlevel% neq 0 (
    echo [오류] 문법 검사 실패. 빌드를 중단합니다.
    pause
    exit /b 1
)

echo 테스트 실행 중...
python -m pytest tests -q
if %errorlevel% neq 0 (
    echo [오류] 테스트 실패. 빌드를 중단합니다.
    pause
    exit /b 1
)

rem Stale-output guard: delete old exes so a locked/failed build cannot silently
rem ship the PREVIOUS build. mpause.exe runs as admin(uac-admin); if a copy is still
rem running it LOCKS the file, del fails, Nuitka cannot overwrite, and the old exe
rem passes the "if exist" checks below -> stale build gets shipped.
rem So: try to kill it, then ABORT LOUDLY if it still cannot be removed.
taskkill /f /im mpause.exe >nul 2>&1
if exist "dist\mpause_app" rmdir /s /q "dist\mpause_app"
if exist "dist\mpause_main.dist" rmdir /s /q "dist\mpause_main.dist"
if exist "dist\mpause" rmdir /s /q "dist\mpause"
if exist "dist\mpause_setup.exe" del /f /q "dist\mpause_setup.exe"
if exist "dist\mpause_app\mpause.exe" (
    echo [오류] 이전 mPause 빌드 폴더 삭제 불가 - 실행 중이거나 잠겨 있습니다. 빌드를 중단합니다.
    echo        실행 중인 mpause.exe 를 관리자 권한으로 종료한 뒤 다시 빌드하세요.
    pause
    exit /b 1
)

echo.
echo [1/2] mpause 빌드 중 (폴더형/standalone)... (첫 빌드는 5~10분 걸릴 수 있습니다)
rem --standalone(폴더형): onefile과 달리 실행 시 임시폴더에 압축 해제하지 않는다.
rem 일부 PC에서 발생하던 "STATUS_IN_PAGE_ERROR" 와 백신의 임시추출 차단 문제를 없앤다.
rem --lto=no is REQUIRED here: without MSVC, Nuitka on Python 3.13 uses the
rem downloaded zig compiler, and zig + LTO fails at link time. Do not re-enable
rem LTO unless MSVC Build Tools are installed.
rem --windows-uac-admin: 다른 사용자/권한 상승된 프로세스를 일시정지하려면 필요하다.
rem --include-module=ed25519_tiny: 없으면 license_client가 조용히 fail-closed로 떨어져
rem 정품 키까지 전부 거부된다(패키지 밖 루트 모듈이라 Nuitka가 놓칠 수 있음).
python -m nuitka --standalone --assume-yes-for-downloads ^
  --lto=no --jobs=%NUMBER_OF_PROCESSORS% --remove-output --python-flag=-OO ^
  --output-dir=dist --output-filename=mpause.exe ^
  --windows-console-mode=disable --windows-uac-admin ^
  --enable-plugin=tk-inter ^
  --include-package=mpauseapp ^
  --include-module=ed25519_tiny ^
  --include-data-dir="%VGAMEPAD_DIR%\win=vgamepad\win" ^
  --include-data-files="%VGAMEPAD_DIR%\win\vigem\client\x64\ViGEmClient.dll=vgamepad\win\vigem\client\x64\ViGEmClient.dll" ^
  --include-data-files="%VGAMEPAD_DIR%\win\vigem\client\x86\ViGEmClient.dll=vgamepad\win\vigem\client\x86\ViGEmClient.dll" ^
  mpause_main.py
rem NOTE: 위 ViGEmClient.dll 두 줄은 필수다. --include-data-dir 은 .dll 을 조용히
rem 건너뛰므로(코드로 취급) vgamepad 가 ctypes.CDLL 로 여는 클라이언트 DLL 이 빠지고,
rem 그러면 'import vgamepad' 가 "Could not find module ViGEmClient.dll" 로 실패한다.
rem 그 실패는 가드에 걸려 조용히 넘어가므로 패드 입력만 소리 없이 사라진다.

rem Nuitka standalone 출력은 dist\mpause_main.dist\ ; 실패 시 PyInstaller --onedir 폴백.
if not exist "dist\mpause_main.dist\mpause.exe" (
    echo [경고] Nuitka 빌드 실패 - PyInstaller 폴더형으로 폴백합니다.
    python -m PyInstaller --onedir --noconsole --uac-admin --name mpause --distpath dist --hidden-import ed25519_tiny --add-data "%VGAMEPAD_DIR%\win;vgamepad\win" mpause_main.py
)
rem 어느 도구가 만들었든 출력 폴더 이름을 dist\mpause_app 으로 통일한다.
if exist "dist\mpause_main.dist\mpause.exe" ren "dist\mpause_main.dist" "mpause_app"
if not exist "dist\mpause_app\mpause.exe" if exist "dist\mpause\mpause.exe" ren "dist\mpause" "mpause_app"

:: Nuitka 빌드 부산물 정리
if exist "mpause_main.build" rmdir /s /q "mpause_main.build"
if exist "mpause_main.onefile-build" rmdir /s /q "mpause_main.onefile-build"

if not exist "dist\mpause_app\mpause.exe" (
    echo mpause 빌드 실패!
    pause
    exit /b 1
)

echo.
echo [2/2] 설치 파일 생성 중...
:: 경로에 (x86) 같은 괄호가 있으면 if-블록 안에서 cmd가 오파싱하므로
:: 괄호 블록을 쓰지 않고 한 줄 if로 처리합니다.
set "ISCC_PATH="
where iscc >nul 2>&1 && set "ISCC_PATH=iscc"
if not defined ISCC_PATH if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC_PATH=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not defined ISCC_PATH if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set "ISCC_PATH=C:\Program Files\Inno Setup 6\ISCC.exe"

if not defined ISCC_PATH (
    echo.
    echo [안내] Inno Setup이 설치되어 있지 않습니다.
    echo        https://jrsoftware.org/isdl.php 에서 설치 후 다시 실행하세요.
    echo.
    echo        EXE 빌드는 완료되었습니다: dist\mpause_app\mpause.exe
    pause
    exit /b 0
)

"%ISCC_PATH%" setup_mpause.iss

if exist "dist\mpause_setup.exe" (
    echo.
    echo ===== 빌드 완료 =====
    echo 설치 파일: dist\mpause_setup.exe
    for %%A in ("dist\mpause_setup.exe") do echo 크기: %%~zA bytes
    echo.
    echo [배포 전 확인] 라이센스 서버에 제품 바인딩^(license-v2^)이 배포돼 있어야 합니다.
    echo               서버 배포가 먼저입니다. 순서가 반대면 mPause가 전부 인증 실패합니다.
    pause
) else (
    echo 설치 파일 생성 실패!
    pause
    exit /b 1
)
