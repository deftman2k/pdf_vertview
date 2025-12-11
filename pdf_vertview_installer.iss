; -------------------------------------------------------------
; Inno Setup script to package PyInstaller release output.
; -------------------------------------------------------------

#define MyAppName "PDF Vertical Tabs Viewer"
#define MyAppVersion "1.0.8"
#define MyAppPublisher "Chris"
#define MyAppExeName "pdf_vertview.exe"
#define MyAppSourceDir "dist\pdf_vertview"

[Setup]
AppId={{4275B9E8-3F59-4A09-A2E8-9516D6A4DAA5}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=dist\installer
OutputBaseFilename=pdf_vertview_setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=icon.ico

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#MyAppSourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Classes\.pdf"; ValueType: string; ValueName: ""; ValueData: "PDFVertView"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.pdf"; ValueType: string; ValueName: "Content Type"; ValueData: "application/pdf"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\PDFVertView"; ValueType: string; ValueName: ""; ValueData: "PDF VertView Document"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\PDFVertView\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\icon.ico"
Root: HKCU; Subkey: "Software\Classes\PDFVertView\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\Applications\{#MyAppExeName}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\{#MyAppExeName}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\icon.ico"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\{#MyAppExeName}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\{#MyAppExeName}\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "{#MyAppName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\{#MyAppExeName}\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "Vertical-tab PDF viewer"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\{#MyAppExeName}\Capabilities"; ValueType: string; ValueName: "ApplicationIcon"; ValueData: "{app}\icon.ico"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\{#MyAppExeName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".pdf"; ValueData: "PDFVertView"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: "Software\\Classes\\Applications\\{#MyAppExeName}\\Capabilities"; Flags: uninsdeletevalue

[Run]
Filename: "{sys}\ie4uinit.exe"; Parameters: "-ClearIconCache"; Flags: runhidden
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
