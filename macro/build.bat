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
rem CRITICAL: opencv-python(일반)은 cv2.pyd가 Media Foundation(MF.dll/MFPlat.dll/
rem MFReadWrite.dll)을 정적 임포트한다. Windows N/KN 에디션 등 미디어 기능이 없는 PC에선
rem 'import cv2'가 DLL 로드 단계에서 실패하고, --windows-console-mode=disable 때문에
rem 아무 메시지 없이 종료된다(= 다른 PC에서 '더블클릭해도 무반응'의 원인).
rem opencv-python-headless는 videoio/highgui를 빼서 MF 의존성이 없다. 이 앱은 cv2를
rem 계산용(matchTemplate/imdecode 등)으로만 쓰므로 headless로 충분하다.
rem
rem 함정: windows-capture가 'opencv-python'(일반판)을 의존성으로 끌어온다. 그래서 headless를
rem 먼저 깔아도 windows-capture 설치 단계에서 일반 opencv-python이 다시 들어와 cv2.pyd를
rem MF 버전으로 덮어쓴다. 따라서 순서가 중요하다:
rem  (1) 일반 패키지 먼저 설치 (windows-capture가 opencv-python을 끌어옴)
rem  (2) 그 뒤에 opencv-python을 제거하고 headless를 강제 재설치 → cv2.pyd의 '마지막 기록자'가
rem      headless가 되게 한다. headless도 동일한 cv2 모듈을 제공하므로 windows-capture는 정상 동작.
python -m pip install --quiet --disable-pip-version-check --no-warn-script-location nuitka pyinstaller pywin32 windows-capture vgamepad numpy pyautogui pillow ordered-set zstandard winocr
python -m pip uninstall -y opencv-python opencv-python-headless opencv-contrib-python opencv-contrib-python-headless >nul 2>&1
python -m pip install --quiet --disable-pip-version-check --no-warn-script-location --force-reinstall --no-deps opencv-python-headless

rem 검증 게이트: 설치된 cv2.pyd가 Media Foundation을 임포트하면 빌드를 중단한다.
rem (과거 pip 상태 꼬임으로 MF 버전이 조용히 번들돼 N에디션에서 무반응이 재발한 적 있음)
echo cv2 Media Foundation 의존성 검사 중...
python check_cv2_headless.py
if %errorlevel% neq 0 (
    echo [오류] cv2.pyd가 Media Foundation을 임포트합니다. opencv-python-headless 설치 실패.
    echo        pip uninstall opencv-python opencv-python-headless 후 다시 실행하세요.
    pause
    exit /b 1
)

echo vgamepad DLL 경로 탐색 중...
:: find_spec로 위치만 찾습니다(import 시 ViGEm 드라이버가 없으면 죽으므로 실행하지 않음).
for /f "delims=" %%i in ('python -c "import importlib.util,os; s=importlib.util.find_spec('vgamepad'); print(os.path.dirname(s.origin) if s and s.origin else '')"') do set "VGAMEPAD_DIR=%%i"
if not defined VGAMEPAD_DIR (
    echo [오류] vgamepad 패키지를 찾을 수 없습니다. 'pip install vgamepad' 후 다시 실행하세요.
    pause
    exit /b 1
)

:: 자산 임베드 (판매본 보호): targets.json/target_*.png를 exe 안에 컴파일해 넣습니다.
echo 자산 임베드 중...
python gen_assets.py
if %errorlevel% neq 0 (
    echo [오류] 자산 임베드 실패. 빌드를 중단합니다.
    pause
    exit /b 1
)
if not exist "macroapp\_assets.py" (
    echo [오류] macroapp\_assets.py가 생성되지 않았습니다. 빌드를 중단합니다.
    pause
    exit /b 1
)

rem Syntax gate before the slow compile. cmd does not expand wildcards,
rem so use compileall (handles directories) instead of py_compile.
rem (NOTE: keep this comment ASCII - cmd misparses some UTF-8 Korean in :: comments)
echo 문법 검사 중...
python -m compileall -q macroapp macro_main.py macro_pro_main.py launcher.py gamepad_test.py ed25519_tiny.py sign_release.py check_cv2_headless.py
if %errorlevel% neq 0 (
    echo [오류] 문법 검사 실패. 빌드를 중단합니다.
    pause
    exit /b 1
)

rem Stale-output guard: delete old exes so a locked/failed build cannot silently
rem ship the PREVIOUS build. macro.exe runs as admin(uac-admin); if a copy is still
rem running it LOCKS the file, del fails, Nuitka cannot overwrite, and the old exe
rem passes the "if exist" checks below -> stale (e.g. pre-headless cv2) gets shipped.
rem So: try to kill it, then ABORT LOUDLY if it still cannot be removed.
taskkill /f /im macro.exe >nul 2>&1
taskkill /f /im macro_pro.exe >nul 2>&1
taskkill /f /im launcher_pro.exe >nul 2>&1
taskkill /f /im launcher.exe >nul 2>&1
taskkill /f /im gamepad_test.exe >nul 2>&1
if exist "dist\macro_app" rmdir /s /q "dist\macro_app"
if exist "dist\macro_pro_app" rmdir /s /q "dist\macro_pro_app"
if exist "dist\macro_pro_main.dist" rmdir /s /q "dist\macro_pro_main.dist"
if exist "dist\macro_pro_setup.exe" del /f /q "dist\macro_pro_setup.exe"
if exist "dist\launcher_pro.exe" del /f /q "dist\launcher_pro.exe"
if exist "dist\macro_main.dist" rmdir /s /q "dist\macro_main.dist"
if exist "dist\macro" rmdir /s /q "dist\macro"
if exist "dist\macro.exe" del /f /q "dist\macro.exe"
if exist "dist\macro_app\macro.exe" (
    echo [오류] 이전 macro 빌드 폴더 삭제 불가 - 실행 중이거나 잠겨 있습니다. 빌드를 중단합니다.
    echo        실행 중인 macro.exe 를 관리자 권한으로 종료한 뒤 다시 빌드하세요.
    pause
    exit /b 1
)
if exist "dist\launcher.exe" del /f /q "dist\launcher.exe"
if exist "dist\macro_setup.exe" del /f /q "dist\macro_setup.exe"

echo.
echo [1/3] macro 빌드 중 (폴더형/standalone)... (첫 빌드는 5~10분 걸릴 수 있습니다)
rem --standalone(폴더형): onefile과 달리 실행 시 임시폴더에 압축 해제하지 않는다. 큰
rem 압축 덩어리를 통째로 페이징하지도 않으므로, 일부 PC에서 발생하던
rem "파일에 액세스할 수 없습니다 / STATUS_IN_PAGE_ERROR로 macro.exe 종료" 와
rem 백신의 임시추출 차단 문제를 근본적으로 없앤다. macro.exe + 부품 DLL이 설치폴더에
rem 그대로 놓인다.
rem --lto=no is REQUIRED here: without MSVC, Nuitka on Python 3.13 uses the
rem downloaded zig 0.16 compiler, and zig + LTO fails at link time
rem (undefined symbols frexpf/wmemchr/isnan in zigc.lib). Do not re-enable
rem LTO unless MSVC Build Tools are installed.
python -m nuitka --standalone --assume-yes-for-downloads ^
  --lto=no --jobs=%NUMBER_OF_PROCESSORS% --remove-output --python-flag=-OO ^
  --output-dir=dist --output-filename=macro.exe ^
  --windows-console-mode=disable --windows-uac-admin ^
  --enable-plugin=tk-inter ^
  --include-package=macroapp ^
  --include-module=winocr ^
  --include-package=winrt ^
  --include-data-dir="%VGAMEPAD_DIR%\win=vgamepad\win" ^
  --include-data-files="%VGAMEPAD_DIR%\win\vigem\client\x64\ViGEmClient.dll=vgamepad\win\vigem\client\x64\ViGEmClient.dll" ^
  --include-data-files="%VGAMEPAD_DIR%\win\vigem\client\x86\ViGEmClient.dll=vgamepad\win\vigem\client\x86\ViGEmClient.dll" ^
  macro_main.py
rem 등수 OCR(winocr)은 winrt.windows.* 네임스페이스 패키지(native .pyd)들을 동적 import한다.
rem Nuitka가 놓치지 않도록 winocr 모듈과 winrt 패키지를 명시 포함한다. 빠지면 빌드는 되지만
rem 'import winocr'가 frozen exe에서 실패해 등수 OCR이 조용히 비활성된다.
rem NOTE: the two ViGEmClient.dll lines above are REQUIRED and must not be removed.
rem --include-data-dir silently SKIPS .dll files (treats them as code), so the
rem vigem CLIENT dlls vgamepad loads via ctypes.CDLL at runtime get dropped while
rem the install\*.msi files come through. Without these, "import vgamepad" fails
rem with "Could not find module ViGEmClient.dll". Must be explicit --include-data-files.
rem Nuitka standalone 출력은 dist\macro_main.dist\ ; 실패 시 PyInstaller --onedir 폴백(dist\macro\).
if not exist "dist\macro_main.dist\macro.exe" (
    echo [경고] Nuitka 빌드 실패 - PyInstaller 폴더형으로 폴백합니다.
    python -m PyInstaller --onedir --noconsole --uac-admin --name macro --distpath dist --add-data "%VGAMEPAD_DIR%\win;vgamepad\win" macro_main.py
)
rem 어느 도구가 만들었든 출력 폴더 이름을 dist\macro_app 으로 통일한다.
if exist "dist\macro_main.dist\macro.exe" ren "dist\macro_main.dist" "macro_app"
if not exist "dist\macro_app\macro.exe" if exist "dist\macro\macro.exe" ren "dist\macro" "macro_app"

echo.
echo [2/5] 프로 빌드 준비 중 (런처 상수 주입)...
rem 런처는 소스가 하나다. 프로용은 제품 식별자와 실행 대상만 바꿔 생성한다.
rem 이렇게 해야 업데이트 확인이 제품별 기록을 보고, 프로 런처가 프로 exe 를 띄운다.
python -c "import pathlib; t=pathlib.Path('launcher.py').read_text(encoding='utf-8'); t=t.replace('LAUNCHER_PRODUCT = \"\"','LAUNCHER_PRODUCT = \"macro_pro\"').replace('TARGET_EXE = \"macro.exe\"','TARGET_EXE = \"macro_pro.exe\"'); pathlib.Path('launcher_pro.py').write_text(t,encoding='utf-8')"
if %errorlevel% neq 0 (
    echo [오류] 프로 런처 생성 실패. 빌드를 중단합니다.
    pause
    exit /b 1
)
python -c "import sys,pathlib; t=pathlib.Path('launcher_pro.py').read_text(encoding='utf-8'); sys.exit(0 if 'LAUNCHER_PRODUCT = \"macro_pro\"' in t and 'TARGET_EXE = \"macro_pro.exe\"' in t else 1)"
if %errorlevel% neq 0 (
    echo [오류] 프로 런처에 상수가 주입되지 않았습니다. 빌드를 중단합니다.
    pause
    exit /b 1
)

echo.
echo [3/5] macro_pro 빌드 중 (프로 전용 기능 포함)...
python -m nuitka --standalone --assume-yes-for-downloads ^
  --lto=no --jobs=%NUMBER_OF_PROCESSORS% --remove-output --python-flag=-OO ^
  --output-dir=dist --output-filename=macro_pro.exe ^
  --windows-console-mode=disable --windows-uac-admin ^
  --enable-plugin=tk-inter ^
  --include-package=macroapp ^
  --include-module=winocr ^
  --include-package=winrt ^
  --include-data-dir="%VGAMEPAD_DIR%\win=vgamepad\win" ^
  --include-data-files="%VGAMEPAD_DIR%\win\vigem\client\x64\ViGEmClient.dll=vgamepad\win\vigem\client\x64\ViGEmClient.dll" ^
  --include-data-files="%VGAMEPAD_DIR%\win\vigem\client\x86\ViGEmClient.dll=vgamepad\win\vigem\client\x86\ViGEmClient.dll" ^
  macro_pro_main.py
if exist "dist\macro_pro_main.dist\macro_pro.exe" ren "dist\macro_pro_main.dist" "macro_pro_app"
if not exist "dist\macro_pro_app\macro_pro.exe" (
    echo macro_pro 빌드 실패!
    pause
    exit /b 1
)

echo.
echo [4/5] launcher.exe 빌드 중...
python -m nuitka --onefile --assume-yes-for-downloads ^
  --lto=no --jobs=%NUMBER_OF_PROCESSORS% --remove-output --python-flag=-OO ^
  --output-dir=dist --output-filename=launcher.exe ^
  --windows-console-mode=disable --windows-uac-admin ^
  --include-module=ed25519_tiny ^
  launcher.py
if not exist "dist\launcher.exe" (
    echo [경고] Nuitka 빌드 실패 - PyInstaller로 폴백합니다.
    python -m PyInstaller --onefile --noconsole --uac-admin --name launcher --hidden-import ed25519_tiny launcher.py
)

python -m nuitka --onefile --assume-yes-for-downloads ^
  --lto=no --jobs=%NUMBER_OF_PROCESSORS% --remove-output --python-flag=-OO ^
  --output-dir=dist --output-filename=launcher_pro.exe ^
  --windows-console-mode=disable --windows-uac-admin ^
  --include-module=ed25519_tiny ^
  launcher_pro.py
if not exist "dist\launcher_pro.exe" (
    echo [경고] Nuitka 빌드 실패 - PyInstaller로 폴백합니다.
    python -m PyInstaller --onefile --noconsole --uac-admin --name launcher_pro --hidden-import ed25519_tiny launcher_pro.py
)

echo.
echo [추가] gamepad_test.exe 빌드 중 (버튼 테스트용, 콘솔 표시)...
python -m nuitka --onefile --assume-yes-for-downloads ^
  --lto=no --jobs=%NUMBER_OF_PROCESSORS% --remove-output --python-flag=-OO ^
  --output-dir=dist --output-filename=gamepad_test.exe ^
  --windows-uac-admin ^
  --include-data-dir="%VGAMEPAD_DIR%\win=vgamepad\win" ^
  --include-data-files="%VGAMEPAD_DIR%\win\vigem\client\x64\ViGEmClient.dll=vgamepad\win\vigem\client\x64\ViGEmClient.dll" ^
  --include-data-files="%VGAMEPAD_DIR%\win\vigem\client\x86\ViGEmClient.dll=vgamepad\win\vigem\client\x86\ViGEmClient.dll" ^
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

if not exist "dist\macro_app\macro.exe" (
    echo macro 빌드 실패!
    pause
    exit /b 1
)
if not exist "dist\launcher.exe" (
    echo launcher.exe 빌드 실패!
    pause
    exit /b 1
)

echo.
echo [5/5] 설치 파일 생성 중...
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
    echo        EXE 빌드는 완료되었습니다:
    echo        - dist\macro.exe
    echo        - dist\launcher.exe
    pause
    exit /b 0
)

"%ISCC_PATH%" setup.iss
"%ISCC_PATH%" setup_pro.iss

if exist "dist\macro_setup.exe" (
    echo.
    echo ===== 빌드 완료 =====
    echo 설치 파일: dist\macro_setup.exe ^(일반^)
    if exist "dist\macro_pro_setup.exe" echo             dist\macro_pro_setup.exe ^(프로^)
    for %%A in ("dist\macro_setup.exe") do echo 크기: %%~zA bytes
    echo.
    echo [배포 절차] GitHub Release에 dist\macro_setup.exe 업로드 후:
    echo    python sign_release.py "<일반 다운로드 URL>"
    echo    python sign_release.py --product macro_pro "<프로 다운로드 URL>"
    echo 출력된 version/url/sha256/sig 네 값을 admin 패널에 등록하세요.
    echo ^(ADMIN_KEY 환경변수 설정 시 --post 옵션으로 자동 등록 가능^)
) else (
    echo 설치 파일 생성 실패!
)
pause
