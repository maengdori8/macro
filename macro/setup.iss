; 버전 업데이트 시 아래 AppVersion도 version.txt와 동일하게 수정하세요
[Setup]
AppName=Macro
AppVersion=1.0.38
AppPublisher=maengdori
DefaultDirName={autopf}\Macro
DefaultGroupName=Macro
OutputDir=dist
OutputBaseFilename=macro_setup
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\launcher.exe
PrivilegesRequired=admin
DisableProgramGroupPage=yes
DisableDirPage=yes
CloseApplications=force
RestartApplications=no
; 32비트 Windows에서는 설치를 차단합니다(앱이 64비트 전용 의존성 사용).
; 설치 위치/식별자(AppId)는 기존 1.0.8과 동일하게 둡니다:
;  - 설치 모드를 x64로 바꾸지 않으므로 {autopf}=Program Files (x86)\Macro 유지
;  - AppId를 명시하지 않아 기본값(AppName "Macro") 유지
; 둘 다 기존 설치와 일치해야 1.0.8이 '제자리 업그레이드'되어 망가진 런처가 교체됩니다.
ArchitecturesAllowed=x64compatible

; 보안: targets.json / target_*.png 는 배포하지 않습니다.
; 이 자산은 gen_assets.py가 exe 안에 컴파일해 넣으므로 설치 폴더엔 노출되지 않습니다.
[Files]
; macro는 폴더형(standalone) 빌드 — macro.exe + 부품 DLL 전체를 설치폴더에 그대로 푼다.
; (onefile의 실시간 임시추출이 일부 PC에서 'STATUS_IN_PAGE_ERROR / 파일 액세스 불가'를
;  일으켜, 폴더형으로 전환함. dist\macro_app\ 안의 모든 파일을 재귀로 담는다.)
; 가상 패드 드라이버 설치본(*.msi)은 설치 폴더에 남기지 않습니다. 런타임은 client\*.dll만
; 쓰므로(패키지 안의 어떤 코드도 msi를 참조하지 않음) 제외해도 동작에 영향이 없고,
; 설치 폴더에 드라이버 이름이 박힌 파일이 남지 않습니다.
Source: "dist\macro_app\*"; DestDir: "{app}"; Excludes: "license.key,logs\*,_update_tmp\*,_update.zip,startup_error.log,vgamepad\win\vigem\install\*"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\launcher.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "version.txt"; DestDir: "{app}"; Flags: ignoreversion
; 설치 중에만 임시 폴더에 풀고 끝나면 지웁니다(중립적인 이름으로 배치).
Source: "dist\macro_app\vgamepad\win\vigem\install\x64\ViGEmBusSetup_x64.msi"; DestDir: "{tmp}"; DestName: "runtime_x64.msi"; Flags: deleteafterinstall

; 감독모드 전용 설치본: 예전 통합 설치본이 남긴 갱신/테스트 실행 파일과 바로가기도 제거합니다.
[InstallDelete]
Type: files; Name: "{app}\renewal_macro.exe"
Type: files; Name: "{app}\gamepad_test.exe"
Type: files; Name: "{group}\Macro 갱신.lnk"
Type: files; Name: "{commondesktop}\Macro 갱신.lnk"

; 런타임 생성물 정리: 캡처 템플릿이 재설치 후에도 몰래 살아남지 않게 합니다.
; license.key는 재설치 시 재인증을 피하기 위해 남겨둡니다.
[UninstallDelete]
Type: filesandordirs; Name: "{app}\custom_targets"
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\_update_tmp"
Type: files; Name: "{app}\_update.zip"

[Icons]
Name: "{group}\Macro"; Filename: "{app}\launcher.exe"
Name: "{commondesktop}\Macro"; Filename: "{app}\launcher.exe"

[Run]
; 가상 패드 드라이버를 완전 무인(/qn)으로 먼저 설치합니다. 이게 없으면 vgamepad가
; import 단계에서 실패해 비활성 입력 경로가 통째로 죽습니다(설치 안내를 사용자에게
; 떠넘기지 않으려고 설치본에 포함). 이미 설치돼 있으면 건너뜁니다.
; StatusMsg는 드라이버 이름을 노출하지 않는 일반 문구를 씁니다.
Filename: "msiexec.exe"; Parameters: "/i ""{tmp}\runtime_x64.msi"" /qn /norestart"; StatusMsg: "필수 구성 요소를 설치하는 중..."; Flags: waituntilterminated; Check: NeedsPadRuntime

; postinstall 체크박스 대신 무조건 실행: 런처가 silent 업데이트(/VERYSILENT)로 자신을
; 재설치한 뒤에도 새 런처가 자동으로 다시 떠서 macro를 실행합니다.
; /postupdate = 이번 실행은 업데이트 확인을 건너뜀(설치 직후 재확인으로 인한 루프 방지).
Filename: "{app}\launcher.exe"; Parameters: "/postupdate"; Flags: nowait; Check: ShouldRunPostInstallLauncher

[Code]
{ 가상 패드 드라이버가 이미 있으면 다시 설치하지 않습니다. 서비스 레지스트리 키는 }
{ 드라이버가 실제로 등록됐을 때만 존재해서, MSI 제품코드보다 확실한 판정 기준입니다. }
function NeedsPadRuntime(): Boolean;
begin
  Result := not RegKeyExists(HKEY_LOCAL_MACHINE,
                             'SYSTEM\CurrentControlSet\Services\ViGEmBus');
end;

function ShouldRunPostInstallLauncher(): Boolean;
var
  I: Integer;
begin
  { Developer-only local experiment installs set this inherited variable so }
  { the installer cannot map a normal GUI before the no-activate launch. }
  Result := (GetEnv('MAUTO_SKIP_POSTINSTALL_RUN') <> '1') and
            (ExpandConstant('{param:NOAUTOSTART|0}') <> '1');
  if not Result then
    Exit;

  { UAC can start Setup with a cached environment.  The explicit local-only }
  { switch survives elevation and leaves normal installs entirely unchanged. }
  for I := 1 to ParamCount do
  begin
    if CompareText(ParamStr(I), '/MAUTOLOCALNOPOSTRUN') = 0 then
    begin
      Result := False;
      Exit;
    end;
  end;
end;
