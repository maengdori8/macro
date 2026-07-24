; 버전 업데이트 시 아래 AppVersion도 version.txt와 동일하게 수정하세요
#ifndef RenewalSource
#define RenewalSource "dist\renewal_macro.exe"
#endif

[Setup]
AppName=Macro
AppVersion=1.0.32
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
Source: "dist\macro_app\*"; DestDir: "{app}"; Excludes: "license.key,logs\*,_update_tmp\*,_update.zip,startup_error.log"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\launcher.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#RenewalSource}"; DestDir: "{app}"; DestName: "renewal_macro.exe"; Flags: ignoreversion
Source: "dist\gamepad_test.exe"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "version.txt"; DestDir: "{app}"; Flags: ignoreversion

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
Name: "{group}\Macro 갱신"; Filename: "{app}\renewal_macro.exe"
Name: "{commondesktop}\Macro 갱신"; Filename: "{app}\renewal_macro.exe"

[Run]
; postinstall 체크박스 대신 무조건 실행: 런처가 silent 업데이트(/VERYSILENT)로 자신을
; 재설치한 뒤에도 새 런처가 자동으로 다시 떠서 macro를 실행합니다.
; /postupdate = 이번 실행은 업데이트 확인을 건너뜀(설치 직후 재확인으로 인한 루프 방지).
Filename: "{app}\launcher.exe"; Parameters: "/postupdate"; Flags: nowait
