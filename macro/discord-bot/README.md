# 매크로 디스코드 봇 — Railway 배포 가이드

24시간 켜져 있어야 `!인증`·`!등수`가 작동합니다. Railway 무료 크레딧으로 충분합니다.

## 1. 사전 준비 (Discord 개발자 포털)
https://discord.com/developers/applications → 봇 선택 → **Bot** 탭
- ✅ **SERVER MEMBERS INTENT** 켜기
- ✅ **MESSAGE CONTENT INTENT** 켜기
- **Reset Token** 으로 토큰 복사 (DISCORD_TOKEN)

봇 초대: **OAuth2 → URL Generator**
- Scopes: `bot`
- Permissions: `Manage Roles`, `Manage Channels`, `Manage Messages`, `Send Messages`, `Read Message History`
- 생성된 URL로 서버 초대 → 서버설정에서 **봇 역할을 `구매자`보다 위로** 이동

## 2. Railway 배포
1. https://railway.app → GitHub로 로그인
2. **New Project → Deploy from GitHub repo** → 이 저장소 선택
3. 서비스 **Settings → Root Directory** 를 `macro/discord-bot` 로 설정
   (저장소 구조상 봇이 하위 폴더에 있음)
4. **Variables** 탭에서 환경변수 추가:
   - `DISCORD_TOKEN` = 봇 토큰
   - `BOT_API_KEY` = 라이센스 서버와 동일한 봇 키
   - (선택) `API_BASE` = `https://license-server-flame-eta.vercel.app/api`
5. 자동 빌드/실행됨. **Deploy Logs** 에 `✅ 봇 로그인: ...` 가 뜨면 성공.

start 명령은 `railway.json` 에 정의돼 있어 별도 설정 불필요합니다.

## 3. 서버 세팅 (봇이 온라인 된 후)
- 관리자가 `!서버구축` → 채널·역할 자동 생성
- 구매자가 `#인증` 에서 `!인증 <키>` → `구매자` 역할 자동 부여

## 명령어
- `!인증 <키>` — 라이센스 인증 후 구매자 역할
- `!등수` — 현재 매크로 등수
- `!도움` — 명령어 목록
- `!서버구축` — (관리자) 채널/역할 자동 생성

## 문제 해결
- 봇은 온라인인데 응답 없음 → 인텐트 2개 켰는지 확인
- `!인증` 시 "역할 부여 실패" → 봇 역할이 `구매자`보다 위인지 확인
- `!인증` 시 "메시지 관리 권한 없음" → 초대 시 Manage Messages 권한 포함했는지 확인
