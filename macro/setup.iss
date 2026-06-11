; 버전 업데이트 시 아래 AppVersion도 version.txt와 동일하게 수정하세요
[Setup]
AppName=Macro
AppVersion=1.0.8
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

; 보안: targets.json / target_*.png 는 배포하지 않습니다.
; 이 자산은 gen_assets.py가 exe 안에 컴파일해 넣으므로 설치 폴더엔 노출되지 않습니다.
[Files]
Source: "dist\macro.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\launcher.exe"; DestDir: "{app}"; Flags: ignoreversion
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

[Run]
Filename: "{app}\launcher.exe"; Description: "매크로 실행"; Flags: nowait postinstall skipifsilent
