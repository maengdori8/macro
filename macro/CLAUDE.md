# CLAUDE.md — 에이전트 컨텍스트 허브

이 파일은 Claude(및 모든 AI 에이전트)가 이 레포에서 **가장 먼저 읽는 맥락 허브**입니다.
원칙: **적혀 있지 않으면 에이전트에게는 존재하지 않습니다.** 결정·규칙·맥락은 여기에 글로 남깁니다.

---

## 🌟 북극성 (이 프로젝트가 향하는 목표)

**FC ONLINE 감독모드 자동매크로(mAuto)를 "구매자는 사용만, 내부 접근·복제·디컴파일은 불가"한 상태로 안정 판매한다.**

성공 지표:
1. 클린 PC에서 설치(setup.exe) → 더블클릭 한 번으로 실행 (무반응/크래시 0)
2. 게임 창이 **비활성(백그라운드)일 때도 100%** 입력·인식 동작
3. 빌드 산출물에서 로직·이미지가 노출되지 않음(Nuitka 컴파일 + 자산 임베드)

스코프 밖(하지 않을 것):
- 탐지 회피(안티치트 우회)·대량 배포·악용 목적 기능
- 마우스 이동 **궤적 변경** (아래 불변식 참고)
- 비활성 100% 동작을 깨는 변경

---

## 📦 프로젝트 개요

- **무엇:** FC ONLINE 감독모드 화면을 인식해 자동 클릭/키 입력하고, 등수·점수·티어를 OCR해 디스코드로 보고하는 Windows 데스크톱 매크로 + 라이센스/디스코드 봇 백엔드.
- **기술 스택:**
  - 매크로 본체: **Python 3** — tkinter(GUI), OpenCV(`opencv-python-headless`), numpy, **winocr**(Windows 내장 OCR), **vgamepad**(ViGEm 가상 패드), **pywin32**, **windows-capture(WGC)**, pyautogui, pillow
  - 빌드: **Nuitka**(디컴파일 방어, `--onefile` 폴더형) + **PyInstaller** 폴백, **Inno Setup**(setup.exe)
  - 디스코드 봇: **Node.js / discord.js v14** (Railway 호스팅)
  - 라이센스 서버: **Vercel 서버리스 + Firebase Firestore**
- **레포 구조:** ⚠️ **git 루트는 상위 폴더**(`/Users/m/Documents/GitHub/macro`), 프로젝트 실체는 `macro/macro`. 추적 경로는 전부 `macro/...` 접두사 (예: `macro/macroapp/gui.py`).

```
macro/                         ← git 루트 (상위)
└─ macro/                      ← 작업 디렉터리(여기서 작업)
   ├─ macroapp/                — 매크로 Python 패키지
   │  ├─ app.py / gui.py       — 진입·tkinter GUI + 자동화 루프
   │  ├─ config.py             — 상수/타깃 로딩/자산
   │  ├─ capture.py window.py  — WGC 캡처·창 관리(클라이언트 영역)
   │  ├─ matching.py           — 2단계 템플릿 매칭(다운스케일→ROI)
   │  ├─ ocr.py                — 등수/티어/점수 OCR(winocr) + 컨센서스
   │  ├─ input_gamepad.py      — vgamepad 입력(RLock 직렬화)
   │  ├─ input_mouse.py        — 마우스 곡선(베지어) 클릭 ⚠️궤적 불변
   │  ├─ input_message.py      — PostMessage 키/마우스
   │  ├─ license_client.py     — HWID·라이센스 검증·상태 전송
   │  └─ winapi.py paths.py …  — 가드된 Windows 의존성/경로
   ├─ discord-bot/bot.js       — 디스코드 봇(discord.js v14)
   ├─ license-server/api/*.js  — Vercel 서버리스(verify/status/admin/…)
   ├─ build.bat setup.iss      — 빌드·설치 패키징
   ├─ gen_assets.py launcher.py macro_main.py
   └─ version.txt targets.json target_*.png
```

---

## ▶️ 자주 쓰는 명령어

⚠️ **빌드와 매크로 실행은 Windows 전용.** Mac에선 winocr·vgamepad·WGC·pywin32가 없어 검증만 가능(아래 의존성은 모두 가드되어 import는 됨).

```bash
# 빌드 (Windows에서만) — Nuitka 3바이너리 + ISCC로 setup.exe 생성
build.bat            # version.txt 읽어 setup.iss 동기화 → gen_assets → 컴파일 → 빌드

# Mac에서 가능한 검증(정적·순수 로직) — 정식 테스트 스위트는 없음
python3 -m compileall macroapp/*.py        # 문법(=build.bat의 '문법검사' 대응)
python3 -m pyflakes macroapp/*.py          # 미정의/미사용 점검
python3 -c "import macroapp.gui, macroapp.ocr, macroapp.config"   # import 스모크
# 순수 로직(파서/컨센서스/전처리/타이밍)은 winocr 없이 단위 검증 가능 — ocr.py는 일부러 분리해 둠
```

---

## ✅ 작업 완료 기준 (Definition of Done)

- [ ] `compileall` + `pyflakes` 통과 (내 변경이 만든 경고 0)
- [ ] **Windows 전용 기능은 순수 로직을 분리**해 Mac에서 단위 검증(정규식·투표·전처리·타이밍 시뮬레이션)
- [ ] Windows 의존성(winocr/vgamepad/cv2/pywin32)이 없거나 실패해도 **매크로 본체에 영향 0**(전부 가드)
- [ ] 불변식 보존: 마우스 궤적 바이트 동일, 비활성 100%, 이미지 매칭 경로
- [ ] 위험·복합 변경은 **워크플로 적대적 리뷰(Doer–Verifier)** 후 confirmed만 반영
- [ ] 커밋 메시지에 "무엇을·왜" 명시
- [ ] winocr/입력/캡처 등 Mac에서 못 돌리는 부분은 **"사용자가 Windows에서 빌드·테스트" 필요**라고 명확히 전달

---

## 🧭 코딩 규칙 / 컨벤션

- **언어:** 코드 주석·로그·커밋 메시지·UI는 **한국어**. Python 4-space 들여쓰기.
- **커밋:** Conventional Commits 접두사(한국어 본문) — `feat:`, `fix:`, `perf:`, `tune:`, `chore:`, `docs:`. 푸터에 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Windows 의존성 접근:** 직접 import 금지 → `from macroapp import winapi` 후 `winapi.win32gui`, `winapi.vg` 등 **속성으로 접근**(키스톤 가드). 없으면 `None`이라 Mac import가 안 깨짐.
- **하지 말 것:**
  - 디스코드 토큰·`BOT_API_KEY` 등 **비밀키 하드코딩/입력칸 타이핑 금지** — 사용자가 직접 입력.
  - `input_mouse.py`의 베지어 궤적·RNG 추출 순서 변경 금지.
  - 사용자 동의 없는 배포(Railway/Vercel)·버전 올림·릴리스.

---

## 🚦 자율성 & 사람 개입 (Trust Ladder)

에이전트가 알아서 해도 되는 것:
- 버그 수정, 성능/정확도 개선, 리팩터링, 주석/문서, 순수 로직 추가와 그 Mac 검증, 커밋(브랜치/요청 시 푸시)

반드시 사람이 하는 것:
- **빌드·릴리스**(Windows에서 사용자가 `build.bat`), **배포**(Railway 봇 / Vercel 서버), 버전 올림
- 의존성 추가/삭제, Firestore 스키마·라이센스 정책 변경
- 비밀키 입력, GitHub **push**(사용자는 GitHub Desktop 사용 + 가끔 커밋 사이에 `git pull --ff` 함 → 푸시 전 `git rev-list --count origin/main..HEAD` 확인)

판단이 애매하면: 추측으로 빌드 사이클 낭비시키지 말고 **질문을 모아** 올린다(특히 화면 좌표/레이아웃처럼 사용자만 아는 값).

---

## 📝 결정 로그 (왜 이렇게 했는지 — 최신순)

- `2026-07-21` **비활성 SKIP 경로 복구 + 활성 메시지 스푸핑 강화(Windows 실측 대기).** 사용자 실측 로그에서 `(A) SKIP`을 감지하면서도 `gui.py`의 A형 분기가 `press_start=True`로 바뀌어 START 0.05초 탭만 반복했고, 기존 `spoof_window_active()+A홀드` 경로는 아예 실행되지 않은 사실을 확인했다. A형은 다시 `press_a=True`로 복구했다. 비활성 유지 계약을 위해 실제 포커스 API(`SetForegroundWindow`/`SetActiveWindow`/`SetFocus`)는 호출하지 않고, `WM_ACTIVATEAPP`/`WM_NCACTIVATE`/`WM_ACTIVATE`/`WM_SETFOCUS` 알림만 올바른 상대 HWND·스레드 ID와 `SendMessageTimeoutW`로 동기 전달한다. 매 메시지 전후 실제 전면 HWND가 그대로일 때만 ViGEm A를 1.2초 홀드하며, 실패하면 A를 보내지 않는다. 홀드 뒤에는 KILLFOCUS/INACTIVE 메시지를 짝으로 보내고 예외 시에도 가상 A를 반드시 해제한다. `compileall`/`pyflakes`/예외 중 버튼 해제 단위 검증 통과. **현재 dist 바이너리에는 미반영이며 사용자 Windows 재빌드 + 실전 SKIP 검증 필요.** MOGA가 XInput slot 0을 점유할 수 있으므로 mAuto(ViGEm 타깃)를 FC보다 먼저 실행하는 양성 대조도 필요하다.

- `2026-07-12` **(A) SKIP 스윕에 'hold-to-skip' 축 추가 + 잘못된 '다른 동작' 후보 폐기.** 배경 컷신 스킵을 웹 리서치+적대적 검증(워크플로 5각도, 14후보 전멸)한 결과 재확인: 전면화0+주입0 유저모드로 스킵 입력을 게임플레이 계층에 넣는 건 원리적 불가(게임이 매 프레임 GetForegroundWindow 재검증, 전역값이라 외부 스푸핑 불가; 주입만 가능하나 안티치트·스코프 밖). **처음엔 '개발자가 안 막은 다른 동작'(start/back로 pause 메뉴, esc/enter/space로 CEF)을 사냥하려 했으나 → 사용자 정정: 컷신 스킵을 넘기는 입력은 오직 '키보드 s' 또는 '게임패드 A' 뿐(다른 버튼·키·pause 무효).** 그래서 그 후보들 전부 폐기. **살아남은 진짜 인사이트 = hold-to-skip**: 사용자 실측 "전면에서 'A 홀드'로 넘어감" → 원래 스윕이 0.15초 '탭'만 해서 홀드 축이 빠져 있었음(탭은 원리상 못 넘김). 스윕을 [스킵 입력=s/A] × [전달 경로] × [탭/홀드]로 재구성: `char_s`/`attach_state_s`/`attach_post_s`/`pm_s`(s 딥 메시지 — 게임이 CEF 메시지 계층으로 s를 읽으면 배경 통과 가능)·`focus_child_s`(자식 SetFocus)·`a`(vgamepad A=게임 A 확정), 각각 `*_hold`(1초, `SKIP_A_SWEEP_HOLD_SECONDS`) 변형 포함 = 12후보. si_s/focus_s는 유출/깜빡임이라 스윕 제외. 물리 키보드는 배경에 더 나쁨(키보드=포그라운드 전용 라우팅, XInput=세션 전역). `_press_skip_candidate`는 `<방법>_<키>` 파싱으로 일반화(홀드/딜리버리 분기 정리용, 현재 key='s'만). 변경: `config.py`(스윕/`SKIP_A_SWEEP_HOLD_SECONDS`), `gui.py`(`_hold`+키보드 일반화). compileall/pyflakes 통과(신규 경고 0), 라우팅 시뮬 검증. **Windows 실측 필요** — 통하는 게 있으면 배경 스킵 확정, 없으면 게임플레이 계층이 순수 RawInput 전면 전용(배경 불가 확정).

- `2026-07-07` **(A) SKIP GitHub 리서치 결론.** 권위 출처(MS DirectXTK Wiki, WGI 문서, reWASD·GlosSI)로 확정: **XInput 값은 배경에도 들어감**(메뉴 A가 배경에서 되는 이유), 게이팅은 **게임/런타임이 포커스로 스스로** 함(DirectXTK: "applications should ignore input when in background"). 외부 유저모드 도구(vgamepad/GlosSI/XOutput/reWASD)는 값만 넣지 게이팅을 못 지움 → **진짜 배경 우회 불가**(게임 exe 패치/DLL 후킹만 되나 안티치트·스코프 밖, 절대 금지). **단 유일한 미검증 배경 후보 = `WM_ACTIVATEAPP`+`WM_ACTIVATE` 활성 플래그 스푸핑**(SDL #4450: 앱이 메시지만으로 '포커스 있다' 오인). 엔진이 스레드-로컬 플래그로 게이팅하면 배경 스킵 가능, GetForegroundWindow 직접 확인이면 불가 — 실측만이 판별. → `SKIP_A_ACTIVATE_SPOOF`(기본 켬, 깜빡임0) 먼저, 안 되면 전면화(ALT트릭+SetForegroundWindow, 리서치가 보증한 유일 유저모드 방법) 폴백. RDP 별도세션은 안티치트 차단 우려로 제외.
- `2026-07-07` **(A) SKIP 최종 결론.** 실측으로 두 입력 계층 확정: 메뉴(패스/확인=VB_AI_A)는 **게임패드로 배경에서 됨**(DirectInput/buttonData), 그러나 **컷신 스킵은 매치 엔진이 '전면(포커스)'일 때만** 읽음. 사용자 직접 확인: "A 홀드 + 알트탭으로 FIFA 전면화해야 스킵됨". 결론 = **컷신 스킵은 배경으로 원리적 불가**(키보드·게임패드·커널·하드웨어 전부, OS가 게임플레이 입력을 전면 창으로만 라우팅). 유일 동작 = **잠깐 전면화+A 홀드+원래 창 복원**(사용자 알트탭 자동화, `SKIP_A_FOREGROUND`, 기본 꺼짐 — 화면 변화 0 유지, 켜면 컷신마다 ~1초 FIFA 번쩍). buttonData: ViGEm 패드는 Controller_011(XUSB) 매핑, A=Button01→VB_AI_A. XUSB↔DInput 번호 어긋남(Button08→RT, Button10→START)이라 vgamepad START는 게임에서 RT로 받힘. 배경 키보드/스윕 코드는 불가 증명돼 (A)브랜치에서 제거.

- `2026-07-03` **깜빡임 0 요구** 반영(자동화라 화면 변화 절대 불가). 적대적 리뷰로 확인: `focus_s`(SetFocus)는 백그라운드 top-level을 activate시켜 **반드시 깜빡임** → 기본 스윕에서 제외(수동 opt-in만). `si_s`(SendInput 전역)는 깜빡임 0이나 **활성 앱에 s 유출** → 제외. 기본 스윕 = 깜빡임0·유출0 4종: `attach_state_s`(AttachThreadInput+SetKeyboardState, GetKeyState 폴링 속이기), `char_s`(WM_KEYDOWN+WM_CHAR+WM_KEYUP 딥, CEF용), `attach_post_s`(큐 공유 후 SetFocus 없이 포커스창에 Post), `pm_s`(WM_KEYDOWN 딥). **경계:** 게임이 RawInput/GetAsyncKeyState 포그라운드 전용이면 유저모드론 원리적 불가(커널 드라이버만) → 첫 (A) SKIP에 창 클래스 덤프 로그(`[SKIP 진단]`)로 CEF(Chrome_RenderWidgetHostHWND) 여부 판별. ⚠️ `send_key_focus_attach` docstring이 '화면 변화 0'이라 거짓 표기했던 것 정정.
- `2026-07-02` (A) SKIP = **자동 버튼 탐색(스윕)+학습**으로 전환, 전면전환 최후수단은 **기본 꺼짐(=0)**. **이유:** 실측 — 가상 A는 탭·1s 홀드 모두 무시, 클릭 무효, 전면전환+키보드 s '탭'은 통함 → 프롬프트는 탭 기반이고 게임의 'A 동작'이 XInput A와 어긋난 것(자체 매핑/DirectInput 인덱스 추정). 후보(a,b,x,y,rb,lb,rt,lt,down,pm_s)를 사이클당 1개 탭 → 스킵이 사라지면 그 입력을 학습(로그 "[SKIP] 학습 완료"). 사용자가 비활성 100%를 요구 → sendinput은 옵트인(`SKIP_A_SENDINPUT_AFTER_SECONDS>0`)일 때만, 스윕 소진+시간 경과 후. 답을 알면 `SKIP_A_BUTTON="b"`처럼 고정. 스킵 실패해도 컷신은 자연 종료라 안전.
- `2026-06-29` 등수/티어/점수를 **각각 독립 "마지막 값 유지"**. **이유:** 로비(등수 없음) 읽기가 매칭 때 잡은 등수를 덮어쓰던 버그. 챔/슈챔이면 등수가 무조건 있음.
- `2026-06-29` 목표 도달 자동 정지(등수 이내 / 점수 이상 / 안 함). 컨센서스로 확정된 값에서만 판정.
- `2026-06-29` 등수 OCR을 **프레임 신선도 게이팅 + 컨센서스 투표 + 전처리(업스케일·대비, 이진화 금지)**로 개선. **이유:** 패널이 ~0.7초만 떠서 시간 간격 폴링은 통째로 놓침. 확정은 벽시계로 폴링(WGC 정적화면 프레임 동결 대비).
- `2026-06-29` vgamepad 입력 시퀀스를 **RLock로 직렬화**. **이유:** 매칭 루프와 SKIP 워커가 공유 가상패드 리포트를 동시에 만져 입력 병합/누락(경합 ~9%).
- `2026-06` OCR을 **별도 스레드**로 분리. **이유:** OCR이 이미지 매칭 루프를 막아 인식 속도 저하.
- (초기) **Nuitka** 채택(+PyInstaller 폴백). **이유:** 디컴파일 방어(판매 보호). `sys.frozen` 대신 `__compiled__` 설정됨.
- (초기) 타깃 이미지·`targets.json`을 **바이너리에 임베드**(`gen_assets.py`→`_assets.py`). **이유:** 설치 폴더에 자산 노출 안 함.
- (초기) HWID = **SMBIOS UUID → MachineGuid(레지스트리) → MAC** 순 폴백, sha256[:32]. 라이센스 = Vercel + Firestore, `BOT_API_KEY` 인증.
- (초기) 배경 입력 = **WGC 클라이언트 영역 캡처 + WM_ACTIVATE 가짜 포커스 + vgamepad(XInput 전역)**.
- (초기) 매칭 = **2단계(다운스케일 사전필터 → ROI 정밀)**, 화면·템플릿 **둘 다 INTER_AREA**로 축소.

---

## 🧠 교훈 & 실수 노트 (에이전트가 갱신)

- **git 루트는 상위 폴더다.** `git show HEAD:macroapp/x.py`는 빈 결과(에러) → `macro/macroapp/x.py`로 써야 함. `git diff --stat`으로 접두사 확인. 한때 "커밋 누락?"으로 오인했음.
- **winocr/vgamepad/WGC는 Mac에서 못 돌린다.** → ocr.py처럼 **순수 로직(정규식·투표·전처리)을 winocr 호출과 분리**해 Mac에서 단위 검증. 인식 정확도/타이밍 실측은 항상 사용자가 Windows에서.
- **다운스케일은 화면·템플릿 둘 다 같은 보간(INTER_AREA)**이어야 함. 한쪽만 스트라이드 슬라이싱하면 얇은 템플릿이 1차에서 누락(8개 중 6개 실패) 버그.
- **cmd는 글롭을 안 펼침** → `py_compile macroapp\*.py` 실패("문법검사 실패"). `compileall`(디렉터리 처리) 사용.
- **vgamepad는 `import`만 해도 ViGEm 없으면 크래시** → 빌드 점검은 `import` 말고 `importlib.util.find_spec`.
- **GIL이 보호하는 것에 헛수정 금지:** 단일 참조/튜플 대입은 원자적. 동시성 리뷰의 오탐 다수가 여기. 진짜 경합은 "공유 가변 객체를 두 스레드가 동시 수정"하는 경우(예: 가상패드 리포트, 컨센서스 객체 공유).
- **'비활성 키보드 입력'은 원리적으로 불가.** RawInput 게임은 포커스 창에만 키가 가서 PostMessage 키(WM_KEYDOWN)를 무시한다 — 그래서 배경 입력의 근거가 전역 XInput(vgamepad)이다. "가상 버튼이 안 먹는다"는 증상은 (1) hold-to-skip인데 탭만 함(롱홀드부터 의심) (2) 게임이 그 버튼만 무시 순으로 가르고, 최후수단은 전면 순간전환+SendInput 스캔코드(sizeof(INPUT)=40, x64).
- 좌표·화면 레이아웃은 **사용자 스크린샷으로 확정**한 뒤 값을 박는다(추측 빌드 금지).
