; 버전 업데이트 시 아래 AppVersion도 version.txt와 동일하게 수정하세요
; (build.bat이 자동으로 동기화합니다)
[Setup]
; ⚠️ AppId는 mAuto(Macro) 설치본과 반드시 달라야 합니다.
; mAuto의 setup.iss는 AppId를 생략해 기본값(AppName "Macro")을 쓰므로,
; 여기서 고유 GUID를 명시하지 않으면 서로의 설치를 덮어쓰거나
; 프로그램 추가/제거 항목이 충돌합니다.
AppId={{BE220F01-AB87-4726-B14F-2019D987D1B9}
AppName=mPause
AppVersion=1.0.0
AppPublisher=maengdori
DefaultDirName={autopf}\mPause
DefaultGroupName=mPause
OutputDir=dist
OutputBaseFilename=mpause_setup
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\mpause.exe
PrivilegesRequired=admin
DisableProgramGroupPage=yes
DisableDirPage=yes
CloseApplications=force
RestartApplications=no
; 64비트 전용 의존성을 쓰므로 32비트 Windows에서는 설치를 차단합니다.
ArchitecturesAllowed=x64compatible
; 이 줄이 없으면 64비트 앱인데도 {autopf}가 Program Files (x86)을 가리킵니다.
; (mAuto는 기존 설치 경로 호환 때문에 일부러 생략했지만, 신규 앱은 그럴 이유가 없습니다.)
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
; 폴더형(standalone) 빌드 — mpause.exe + 부품 DLL 전체를 설치폴더에 그대로 풉니다.
; (onefile의 실시간 임시추출이 일부 PC에서 'STATUS_IN_PAGE_ERROR / 파일 액세스 불가'를
;  일으킨 전례가 있어 mAuto와 같은 폴더형을 씁니다.)
; 패드 드라이버 설치본(*.msi)은 설치 폴더에 남기지 않습니다. 런타임은 client\*.dll만
; 쓰므로 제외해도 동작에 영향이 없고, 설치 폴더에 드라이버 이름이 박힌 파일이 남지 않습니다.
Source: "dist\mpause_app\*"; DestDir: "{app}"; Excludes: "license.key,startup_error.log,vgamepad\win\vigem\install\*"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "version.txt"; DestDir: "{app}"; Flags: ignoreversion
; 설치 중에만 임시 폴더에 풀고 끝나면 지웁니다(중립적인 이름으로 배치).
Source: "dist\mpause_app\vgamepad\win\vigem\install\x64\ViGEmBusSetup_x64.msi"; DestDir: "{tmp}"; DestName: "runtime_x64.msi"; Flags: deleteafterinstall

; 재설치 시 라이센스 재인증을 피하기 위해 license.key는 남겨둡니다.
[UninstallDelete]
Type: files; Name: "{app}\startup_error.log"

[Icons]
Name: "{group}\mPause"; Filename: "{app}\mpause.exe"
Name: "{commondesktop}\mPause"; Filename: "{app}\mpause.exe"

[Run]
; 필수 런타임을 완전 무인(/qn)으로 **먼저** 설치합니다. 이게 없으면 두 번째 입력
; 방법이 통째로 죽는데, 실패가 전부 조용해서(가드에 삼켜짐) 원인을 찾을 단서도
; 남지 않습니다. 이미 설치돼 있으면 건너뜁니다.
; StatusMsg 는 구성 요소 이름을 노출하지 않는 일반 문구를 씁니다.
Filename: "msiexec.exe"; Parameters: "/i ""{tmp}\runtime_x64.msi"" /qn /norestart"; StatusMsg: "필수 구성 요소를 설치하는 중..."; Flags: waituntilterminated; Check: NeedsRuntime
Filename: "{app}\mpause.exe"; Description: "mPause 실행"; Flags: nowait postinstall skipifsilent

[Code]
function NeedsRuntime(): Boolean;
begin
  Result := not RegKeyExists(HKEY_LOCAL_MACHINE,
                             'SYSTEM\CurrentControlSet\Services\ViGEmBus');
end;
