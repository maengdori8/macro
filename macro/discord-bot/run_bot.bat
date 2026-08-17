@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 디스코드 봇을 이 PC에서 직접 실행합니다(Railway 대체, 무료).
rem 토큰은 .env 파일에서 읽습니다 - Node 20.6+ 의 --env-file 기능이라 추가 패키지가 필요 없습니다.
rem .env 는 .gitignore 에 있어 깃에 올라가지 않습니다.
if not exist ".env" (
    echo [안내] .env 파일이 없습니다.
    echo        .env.example 을 .env 로 복사한 뒤 값 두 개를 채워 넣으세요.
    pause
    exit /b 1
)
if not exist "node_modules" (
    echo [1/2] 의존성 설치 중...
    call npm.cmd install
    if errorlevel 1 (
        echo [오류] npm install 실패.
        pause
        exit /b 1
    )
)
echo [2/2] 봇 시작... ^(창을 닫으면 봇이 꺼집니다^)
:run
node --env-file=.env bot.js
echo.
echo [경고] 봇이 종료됐습니다. 5초 후 자동 재시작합니다. 완전히 끄려면 이 창을 닫으세요.
ping -n 6 127.0.0.1 >nul 2>&1
goto :run
