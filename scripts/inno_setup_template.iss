[Setup]
AppName={APP_NAME}
AppVersion=1.1.1
AppPublisher=SanYing
AppPublisherURL=https://perf.sanying.site
DefaultDirName={autopf}\{APP_NAME}
DefaultGroupName={APP_NAME}
OutputDir=dist
OutputBaseFilename={APP_NAME}_Setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"
Name: "quicklaunchicon"; Description: "创建快速启动栏快捷方式"; GroupDescription: "附加图标:"; Flags: unchecked

[Files]
Source: "dist\{EXE_NAME}.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\{EXE_NAME}\{EXE_NAME}.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\{EXE_NAME}\scripts\*"; DestDir: "{app}\scripts"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\scripts\*"; DestDir: "{app}\scripts"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{APP_NAME}"; Filename: "{app}\{EXE_NAME}.exe"
Name: "{group}\卸载 {APP_NAME}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{APP_NAME}"; Filename: "{app}\{EXE_NAME}.exe"; Tasks: desktopicon
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{APP_NAME}"; Filename: "{app}\{EXE_NAME}.exe"; Tasks: quicklaunchicon

[Run]
Filename: "{app}\{EXE_NAME}.exe"; Description: "启动 {APP_NAME}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{EXE_NAME}.exe"; Parameters: "--install-background-service"; Description: "安装后台通知服务"; Flags: runhidden waituntilterminated

