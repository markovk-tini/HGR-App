; Touchless Windows installer
; Place this file at installers/windows/hgr_app.iss
;
; -- Build modes --
;   Default  : STUB installer. Tiny (~5-15 MB) Setup.exe that
;              downloads Touchless_Payload_v<version>.zip from R2
;              at install time and unpacks it into {app}. Mirrors the
;              Adobe / Discord / Spotify pattern; lets the website
;              link feel like a few-second click.
;   /DMONOLITHIC=1 : the original behavior — Setup.exe carries the
;              entire dist/Touchless tree inside it (~2.4 GB). Kept
;              as the "offline edition" for users on planes / air-
;              gapped machines, behind the MONOLITHIC=1 env flag in
;              builder/windows/build_windows.bat.
;
; -- Defines passed in by build_windows.bat --
;   /DPAYLOAD_URL=<full https URL>     (stub mode, required)
;   /DPAYLOAD_FILE=<filename only>     (stub mode, required)
;   /DPAYLOAD_SHA256=<lowercase hex>   (stub mode, required — verifies the
;                                       downloaded zip before extraction)
;   /DMONOLITHIC=1                     (optional — switches to embedded zip)

#define MyAppName "Touchless"
#define MyAppVersion "1.1.0b7"
#define MyAppPublisher "Konstantin Markov"
#define MyAppExeName "Touchless.exe"
#define DistDir "..\..\dist\Touchless"
#define IconFile "..\..\assets\icons\touchless_icon.ico"

#ifndef MONOLITHIC
  #define STUB
#endif

#ifdef STUB
  #ifndef PAYLOAD_URL
    #error "STUB build requires /DPAYLOAD_URL=https://..."
  #endif
  #ifndef PAYLOAD_FILE
    #error "STUB build requires /DPAYLOAD_FILE=filename.zip"
  #endif
  #ifndef PAYLOAD_SHA256
    #error "STUB build requires /DPAYLOAD_SHA256=lowercase-hex"
  #endif
#endif

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
; Defender exclusion task. Default ON because the most common reason
; GPU Mode silently falls back to CPU on a fresh install is Microsoft
; Defender's ML heuristic flagging bundled DirectML.dll (Microsoft-
; signed, but inside a recently-downloaded third-party folder). The
; exclusion lets DirectML.dll load via LoadLibrary, which lets ONNX
; Runtime bring up its DirectX 12 execution provider. Reversible
; (uninstall removes it; user can also remove via Defender Settings
; -> Exclusions). Triggers one UAC prompt during install — the only
; UAC prompt in the whole flow, since the rest of the install is
; per-user under %LOCALAPPDATA%.
Name: "defender_exclusion"; Description: "Allow Touchless to use your GPU (adds the install folder to Microsoft Defender exclusions — one UAC prompt)"; GroupDescription: "GPU acceleration:"

[Files]
#ifdef MONOLITHIC
; Original embedded-payload behavior. Pulls the entire PyInstaller
; bundle into the installer at compile time. Used only when the
; build is invoked with MONOLITHIC=1 (offline-edition build).
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#endif
#ifdef STUB
; STUB mode has no embedded files — everything ships in the
; downloaded payload zip. The [Code] section handles the download
; and extraction.
#endif

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon; IconFilename: "{app}\{#MyAppExeName}"

[Run]
; Microsoft Defender exclusion for the install folder. Wrapped in a
; PowerShell try/catch that always exits 0 so a failure (Group Policy
; blocking Add-MpPreference, third-party AV taking over Defender's
; role, user denying the UAC prompt) doesn't abort the install or
; surface a confusing error dialog. Verb: runas triggers UAC for this
; single command — the rest of the install is per-user. Add-MpPreference
; is idempotent for ExclusionPath, so re-running on top of an existing
; install is a no-op.
;
; skipifsilent is critical: the auto-updater runs the installer with
; /SILENT, which would otherwise fire this entry (the task is ON by
; default) and pop a context-free UAC prompt while the user is in the
; middle of something else — a real regression. Manual installs aren't
; affected; the user is in the wizard already and expects the prompt.
; Net effect: first-time manual installers get the exclusion (the
; population that needs it most); existing users who auto-update later
; already had it added at their original install time, so nothing's
; lost on the auto-update path.
Filename: "powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -Command ""try {{ Add-MpPreference -ExclusionPath '{app}' -ErrorAction Stop }} catch {{ exit 0 }}"""; Verb: runas; Flags: shellexec waituntilterminated skipifsilent; Tasks: defender_exclusion; StatusMsg: "Adding Touchless to Microsoft Defender exclusions..."

; Touchless.exe has requireAdministrator in its manifest (uac_admin=True
; in hgr_app.spec). Inno's default [Run] launcher uses CreateProcess
; which can't elevate, producing error 740 "requires elevation" on the
; post-install Launch checkbox. The 1.1.0b2 attempt at fixing this used
; "shellexec runasoriginaluser" hoping ShellExecuteEx would auto-elevate
; via the manifest — but in practice Inno still went through CreateProcess
; for this particular entry (a real install repro from a beta tester
; reproduced the same error 740 with that flag set).
;
; Forcing it: Verb: runas explicitly asks the shell for the "Run as
; administrator" verb, which always triggers UAC regardless of how the
; manifest is interpreted. Same pattern we use on the Defender exclusion
; PowerShell call above. shellexec stays so ShellExecuteEx is the API
; (Verb is ignored under CreateProcess). runasoriginaluser dropped — it
; only matters when the installer itself is elevated, which ours never is
; (PrivilegesRequired=lowest), so it was a no-op decoration.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Verb: runas; Flags: nowait postinstall skipifsilent shellexec

[UninstallRun]
; Mirror of the install-time Defender exclusion: remove the entry
; on uninstall so we don't leave a stale exclusion pointing at a
; folder that no longer exists. Same try/catch pattern; if it
; fails (UAC denied, GP-managed Defender, etc.) the uninstall
; still completes cleanly. Remove-MpPreference is a no-op when
; the exclusion isn't set, so older installs that pre-date this
; change uninstall harmlessly.
Filename: "powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -Command ""try {{ Remove-MpPreference -ExclusionPath '{app}' -ErrorAction Stop }} catch {{ exit 0 }}"""; Verb: runas; Flags: shellexec waituntilterminated; RunOnceId: "RemoveDefenderExclusion"

#ifdef STUB
[Code]
// Stub installer download + extract logic. Uses Inno Setup 6.1+'s
// built-in CreateDownloadPage + DownloadTemporaryFile (no third-
// party plugins). Flow:
//   1. NextButtonClick(wpReady) shows the download page with a
//      progress bar; user can't proceed until the download lands
//      (or they cancel).
//   2. CurStepChanged(ssInstall) unpacks the zip into the install
//      directory via PowerShell's Expand-Archive (built into
//      Windows 10+).
//   3. The post-Install [Run] entries (Defender exclusion + Launch
//      checkbox) and [Icons] / [UninstallRun] all reference the
//      install directory, which is populated by step 2 — no
//      other plumbing changes.
//
// SHA256 verification is built into DownloadPage.Add — if the
// downloaded zip doesn't match PAYLOAD_SHA256 (baked in at
// compile time by build_windows.bat), Inno raises an exception
// before extraction runs.

var
  DownloadPage: TDownloadWizardPage;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage(
    'Downloading Touchless',
    'Please wait while Setup downloads the Touchless payload from the Touchless website.',
    nil);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  if CurPageID = wpReady then begin
    DownloadPage.Clear;
    DownloadPage.Add('{#PAYLOAD_URL}', '{#PAYLOAD_FILE}', '{#PAYLOAD_SHA256}');
    DownloadPage.Show;
    try
      try
        DownloadPage.Download;
        Result := True;
      except
        if DownloadPage.AbortedByUser then begin
          Log('Aborted by user.');
          Result := False;
        end else begin
          SuppressibleMsgBox(
            'Download failed: ' + GetExceptionMessage,
            mbCriticalError,
            MB_OK,
            IDOK);
          Result := False;
        end;
      end;
    finally
      DownloadPage.Hide;
    end;
  end else
    Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  ZipPath: String;
  ExtractDir: String;
  PsCmd: String;
begin
  if CurStep = ssInstall then begin
    ZipPath := ExpandConstant('{tmp}\{#PAYLOAD_FILE}');
    ExtractDir := ExpandConstant('{app}');
    if not ForceDirectories(ExtractDir) then
      RaiseException('Could not create install directory: ' + ExtractDir);
    // PowerShell's Expand-Archive -- bundled with Windows 10+,
    // handles arbitrary nested zip layouts, no extra binary to
    // ship. -Force overwrites any partial extraction left by a
    // previous failed attempt. Single-quoted PowerShell strings
    // avoid having to backslash-escape the Windows paths.
    PsCmd :=
      '-NoProfile -NonInteractive -ExecutionPolicy Bypass ' +
      '-Command "Expand-Archive -LiteralPath ''' + ZipPath + ''' ' +
      '-DestinationPath ''' + ExtractDir + ''' -Force"';
    if not Exec('powershell.exe', PsCmd, '', SW_HIDE,
                ewWaitUntilTerminated, ResultCode) then
      RaiseException('Could not launch PowerShell to extract payload.');
    if ResultCode <> 0 then
      RaiseException('Payload extraction failed (PowerShell exit code ' +
                     IntToStr(ResultCode) + '). Try running the installer ' +
                     'again, or use the offline edition from the Touchless ' +
                     'website if the issue persists.');
  end;
end;
#endif
