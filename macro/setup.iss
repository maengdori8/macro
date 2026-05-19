[Setup]
AppName=Macro
AppVersion=1.0.0
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

[Files]
Source: "dist\macro.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\launcher.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "targets.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "target_*.png"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Macro"; Filename: "{app}\launcher.exe"
Name: "{commondesktop}\Macro"; Filename: "{app}\launcher.exe"

[Run]
Filename: "{app}\launcher.exe"; Description: "매크로 실행"; Flags: nowait postinstall skipifsilent
