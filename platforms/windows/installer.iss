; Inno Setup script: wraps the PyInstaller onedir bundle (dist\VoiceTranscriber)
; into ONE downloadable Setup.exe, so a Windows user gets a single .exe instead
; of a ~373 MiB zip they have to extract by hand.
;
; Why not `PyInstaller --onefile`: that repacks the whole ~1.1 GB payload into a
; fresh %TEMP%\_MEIxxxxxx on EVERY launch and nothing is cached between runs, so
; every push-to-talk start would pay a multi-hundred-MB unpack. A onedir bundle
; inside an installer unpacks exactly once, then starts fast forever after.
;
; Build, on Windows (ISCC.exe needs Inno Setup 6.3+; 6.7.1 is preinstalled on
; GitHub's windows-2022 and windows-2025 runners):
;   python platforms\windows\build_offline.py --no-model   ; -> dist\VoiceTranscriber\
;   ISCC.exe /DAppVersion=1.4.1 platforms\windows\installer.iss
; Output: dist\installer\VoiceTranscriber-windows-x86_64-setup.exe
;
; The installer does NOT redistribute model weights or llama.cpp: the CI build
; is model-free (--no-model) and the pinned llama-server is fetched at runtime,
; so there is no third-party binary in here to attach licence notices to.

#ifndef AppVersion
  #error Pass the app version: ISCC.exe /DAppVersion=1.4.1 installer.iss
#endif

#define AppName "Voice Transcriber"
#define AppExeName "VoiceTranscriber.exe"
#define DistDir "..\..\dist\VoiceTranscriber"
#define IconFile "..\..\packaging\icon.ico"

[Setup]
; Stable AppId. This is how Windows and the uninstaller recognise an upgrade as
; *this* app rather than a second copy in a second directory. Never change it.
AppId={{C262EEB6-8954-46B4-A915-D891EE7FAC87}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=James Martin
AppPublisherURL=https://github.com/jjamesmartiin/voice-transcriber
AppSupportURL=https://github.com/jjamesmartiin/voice-transcriber/issues
DefaultDirName={localappdata}\VoiceTranscriber
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
; Per-user install: no UAC prompt, no admin rights, and the app never writes into
; its own read-only directory anyway (settings/logs/weights go to %APPDATA%\vt).
PrivilegesRequired=lowest
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExeName}
WizardStyle=modern
; lzma2/normal keeps CI time sane; /max buys a few percent of size for minutes
; of extra compression on ~1 GB of payload.
Compression=lzma2/normal
SolidCompression=yes
OutputDir=..\..\dist\installer
OutputBaseFilename=VoiceTranscriber-windows-x86_64-setup

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "autostart"; Description: "Start Voice Transcriber automatically on Windows startup"; GroupDescription: "Startup:"; Flags: unchecked

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\{#AppExeName}"""; Flags: uninsdeletevalue; Tasks: autostart

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

; Deliberately no [UninstallDelete]. The uninstaller removes only what it
; installed, so user data survives an uninstall: weights under
; %APPDATA%\vt\models, config/logs under %APPDATA%\vt and %LOCALAPPDATA%\vt, and
; any model files a user dropped into <install dir>\models\ by hand (a documented
; option). Deleting ~4 GB of weights that the next install would just re-download,
; or a hand-placed model, is not worth the tidiness.

[Code]
// Pre-check the autostart task on upgrades if the user had autostart enabled,
// so an upgrade does not silently disable their startup preference.
procedure InitializeWizard;
var
  ExistingRunCmd: String;
begin
  if RegQueryStringValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', '{#AppName}', ExistingRunCmd) then
  begin
    WizardSelectTasks('autostart');
  end;
end;
