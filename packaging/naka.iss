; The Naka installer. Compiled by packaging/build.py, which passes the
; version and the folder to pack in; ISCC on its own needs both defined.
;
; It installs per user, into %LOCALAPPDATA%\Programs\Naka, so there is no
; administrator prompt and no machine-wide state to clean up. Everything it
; writes lives in that folder and in one registry value for starting with
; Windows — the same value the tray menu and the panel toggle, so the three
; cannot disagree.
;
; What a person downloads is about 50 MB. Naka's runtime, the speech models
; and a language model are fetched on first launch, by the wizard, where the
; steps can be shown and a stopped download can pick up where it left off.

#ifndef Version
  #define Version "0.0.0"
#endif
#ifndef Source
  #define Source "..\build\dist\Naka"
#endif
#ifndef AppName
  #define AppName "Naka"
#endif

[Setup]
; Never change this: it is how Windows recognises an upgrade rather than a
; second copy alongside the first.
AppId={{7F2B6A14-5C29-4E1D-9B4C-0E3A6D8F42C1}
AppName={#AppName}
AppVersion={#Version}
AppPublisher={#AppName}
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#Source}\..\..
OutputBaseFilename={#AppName}-setup-{#Version}
Compression=lzma2/max
SolidCompression=yes
; Dark, forced rather than following Windows: Naka is a dark application, and
; a white installer in front of it would be the seam. Needs Inno Setup 7 —
; 6 does not know the word and will not compile this.
WizardStyle=modern dark
WizardImageBackColor=$0b0807
; The welcome page is where the artwork lives, and where someone gets to see
; what they are installing before the first question.
DisableWelcomePage=no
WizardImageFile={#Source}\..\..\wizard\side.bmp
WizardSmallImageFile={#Source}\..\..\wizard\small.bmp
WizardImageStretch=yes
SetupIconFile={#Source}\..\..\wizard\naka.ico
UninstallDisplayIcon={app}\Naka.exe
AppMutex=Local\NakaTray
CloseApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart"; Description: "Start {#AppName} when I sign in"; GroupDescription: "After installing:"
Name: "runsetup"; Description: "Set {#AppName} up now (downloads about 15 GB)"; GroupDescription: "After installing:"

[Files]
; The whole built folder: Naka.exe, the frozen tray beside it, and the
; source, panel, config and manifests it runs from.
Source: "{#Source}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Unpacked into {tmp} only when WebView2 is missing (see CurStepChanged).
Source: "{#Source}\..\..\vendor\MicrosoftEdgeWebview2Setup.exe"; Flags: dontcopy

[Icons]
; One entry, loose in the Start menu rather than in a folder of its own:
; there is one thing to start, and the uninstaller is listed under Apps.
Name: "{userstartmenu}\{#AppName}"; Filename: "{app}\Naka.exe"

[Registry]
; The one value that decides whether Naka starts with Windows. Written only
; when the box is ticked, and removed with the app either way.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\Naka.exe"" --hidden"; \
    Flags: uninsdeletevalue; Tasks: autostart

[Run]
; WebView2 renders the panel. Windows 11 has it; Windows 10 may not, and the
; bootstrapper is a no-op when it is already there.
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; \
    StatusMsg: "Installing WebView2..."; Check: NeedsWebView2; Flags: waituntilterminated
Filename: "{app}\Naka.exe"; Parameters: "--setup"; Description: "Set {#AppName} up now"; \
    Flags: nowait postinstall skipifsilent; Tasks: runsetup

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
var
  WebView2Needed: Boolean;
  WebView2Checked: Boolean;

function WebView2Installed: Boolean;
var
  Version: String;
begin
  // The evergreen runtime records its version in one of these, per machine
  // or per user. Anything non-empty means it is there.
  Result :=
    (RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

function NeedsWebView2: Boolean;
begin
  if not WebView2Checked then
  begin
    WebView2Needed := not WebView2Installed;
    WebView2Checked := True;
  end;
  Result := WebView2Needed;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  // Unpacked only when it is actually needed, so the usual install writes
  // nothing to disk for it.
  if (CurStep = ssInstall) and NeedsWebView2 then
    ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Data: String;
begin
  if CurUninstallStep <> usUninstall then
    Exit;
  Data := ExpandConstant('{localappdata}\Naka');
  if not DirExists(Data) then
    Exit;
  // About 15 GB of downloads and everything the person told Naka. Kept
  // unless it is asked for, and the No button is the default one.
  if MsgBox('Delete everything Naka downloaded and everything it remembers?'#13#10#13#10
            + 'This removes ' + Data + ', which holds:'#13#10
            + '  - the language and speech models (about 15 GB)'#13#10
            + '  - your settings, persona and remembered facts'#13#10
            + '  - the conversation history and logs'#13#10#13#10
            + 'It cannot be undone, and the models would have to be downloaded'#13#10
            + 'again. Choose No to keep all of it for a later install.'#13#10#13#10
            + 'Notes are files in your Documents folder and are never touched.',
            mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    DelTree(Data, True, True, True);
end;
