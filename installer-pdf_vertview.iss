; -------------------------------------------------------------
; Inno Setup script to package PyOxidizer release output.
; Build with: iscc installer-pdf_vertview.iss
; -------------------------------------------------------------

#define AppVer "1.0.7"

[Setup]
AppName=PDF VertView
AppVersion={#AppVer}
AppPublisher=Chris
AppId={{F8A2E30D-BA0F-4C75-A484-72A40C0C9D3E}}
DefaultDirName={autopf}\PDF VertView
DefaultGroupName=PDF VertView
DisableProgramGroupPage=yes
OutputDir={#SourcePath}\build\installer
OutputBaseFilename=pdf_vertview_setup_{#AppVer}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayIcon={app}\icon.ico
LicenseFile=build\x86_64-pc-windows-msvc\release\install\COPYING.txt
SetupIconFile=icon.ico

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Files]
Source: "build\x86_64-pc-windows-msvc\release\install\pdf_vertview.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "build\x86_64-pc-windows-msvc\release\install\python3.dll"; DestDir: "{app}"; Flags: ignoreversion
Source: "build\x86_64-pc-windows-msvc\release\install\python310.dll"; DestDir: "{app}"; Flags: ignoreversion
Source: "build\x86_64-pc-windows-msvc\release\install\vcruntime140.dll"; DestDir: "{app}"; Flags: ignoreversion
Source: "build\x86_64-pc-windows-msvc\release\install\vcruntime140_1.dll"; DestDir: "{app}"; Flags: ignoreversion
Source: "build\x86_64-pc-windows-msvc\release\install\vcruntime140_threads.dll"; DestDir: "{app}"; Flags: ignoreversion
Source: "build\x86_64-pc-windows-msvc\release\install\COPYING.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "icon.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "build\x86_64-pc-windows-msvc\release\install\prefix\*"; DestDir: "{app}\prefix"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Icons]
Name: "{autoprograms}\PDF Vert View"; Filename: "{app}\pdf_vertview.exe"; IconFilename: "{app}\icon.ico"
Name: "{autodesktop}\PDF Vert View"; Filename: "{app}\pdf_vertview.exe"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Classes\.pdf"; ValueType: string; ValueName: ""; ValueData: "PDFVertView"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.pdf"; ValueType: string; ValueName: "Content Type"; ValueData: "application/pdf"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\PDFVertView"; ValueType: string; ValueName: ""; ValueData: "PDF VertView Document"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\PDFVertView\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\icon.ico"
Root: HKCU; Subkey: "Software\Classes\PDFVertView\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\pdf_vertview.exe"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\Applications\pdf_vertview.exe"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "PDF VertView"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\pdf_vertview.exe\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\icon.ico"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\pdf_vertview.exe\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\pdf_vertview.exe"" ""%1"""; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\pdf_vertview.exe\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "PDF VertView"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\pdf_vertview.exe\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "Vertical-tab PDF viewer"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\pdf_vertview.exe\Capabilities"; ValueType: string; ValueName: "ApplicationIcon"; ValueData: "{app}\icon.ico"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\pdf_vertview.exe\Capabilities\FileAssociations"; ValueType: string; ValueName: ".pdf"; ValueData: "PDFVertView"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "PDF VertView"; ValueData: "Software\\Classes\\Applications\\pdf_vertview.exe\\Capabilities"; Flags: uninsdeletevalue

[Run]
Filename: "{sys}\ie4uinit.exe"; Parameters: "-ClearIconCache"; Flags: runhidden
