; ملف التثبيت للويندوز (Inno Setup) — بيتبني تلقائي من GitHub Actions
#define AppVer GetEnv("NQ_VER")
#if AppVer == ""
  #define AppVer "1.0.0"
#endif

[Setup]
AppId={{6B0E6E52-7C0A-4E7E-9D7B-3F1B2A9C4E11}
AppName=NQ Call
AppVersion={#AppVer}
AppPublisher=NQ
DefaultDirName={localappdata}\NQ Call
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=NQ-Call-Setup
SetupIconFile=windows\runner\resources\app_icon.ico
UninstallDisplayIcon={app}\nq_call.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "أيقونة على سطح المكتب"; Flags: checkedonce
Name: "startup"; Description: "يشتغل لوحده مع الويندوز (عشان المكالمات توصلك)"; Flags: checkedonce

[Files]
Source: "build\windows\x64\runner\Release\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\NQ Call"; Filename: "{app}\nq_call.exe"
Name: "{autodesktop}\NQ Call"; Filename: "{app}\nq_call.exe"; Tasks: desktopicon
Name: "{userstartup}\NQ Call"; Filename: "{app}\nq_call.exe"; Tasks: startup

[Run]
Filename: "{app}\nq_call.exe"; Description: "شغّل NQ Call"; Flags: nowait postinstall skipifsilent
