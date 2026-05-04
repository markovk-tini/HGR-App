; Touchless Windows installer
; Place this file at installers/windows/hgr_app.iss

#define MyAppName "Touchless"
#define MyAppVersion "1.1.0b2"
#define MyAppPublisher "Konstantin Markov"
#define MyAppExeName "Touchless.exe"
#define DistDir "..\..\dist\Touchless"
#define IconFile "..\..\assets\icons\touchless_icon.ico"

[Setup]
AppId={{2C4EE680-53F5-4D83-92A8-ADF4D2D8794E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; Per-user install under %LOCALAPPDATA%\Programs\Touchless. Avoids
; UAC entirely — the app folder is user-writable, so subsequent
; auto-updates can replace files without prompting the user for
; admin approval. Same approach Discord/Slack/VS Code (User Installer)
; use. Trade-off: each Windows user installs separately, which is
; fine for the friends-and-family scale we ship at.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={localappdata}\Programs\{#MyAppName}
; FSL-1.1-Apache-2.0 license shown on the License Agreement page so
; users see the terms before installing. Path is relative to the
; .iss file location (installers/windows/).
LicenseFile=..\..\LICENSE
DefaultGroupName={#MyAppName}
OutputDir=..\..\release
OutputBaseFilename=Touchless_Installer
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#MyAppExeName}
ChangesAssociations=no
DisableProgramGroupPage=yes
; Auto-update support: when the running app's updater launches us
; with /CLOSEAPPLICATIONS, Inno Setup gracefully closes Touchless.exe
; before replacing files (otherwise the in-use .exe blocks the
; upgrade). /RESTARTAPPLICATIONS in the updater command line plus
; the [Run] entry below put Touchless back up automatically.
CloseApplications=yes
RestartApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon; IconFilename: "{app}\{#MyAppExeName}"

[Run]
; Touchless.exe has requireAdministrator in its manifest (uac_admin=True
; in hgr_app.spec). Inno's default launcher uses CreateProcess which
; cannot elevate, so the post-install "Launch Touchless" checkbox would
; fail with error 740 "the requested operation requires elevation". The
; shellexec flag routes through ShellExecuteEx which honors the manifest
; and triggers a normal UAC prompt — same as double-clicking the desktop
; shortcut. runasoriginaluser ensures we elevate from the actual user
; account that ran the installer (relevant only if the installer ever
; gets escalated; harmless otherwise).
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent shellexec runasoriginaluser
