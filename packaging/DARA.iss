; DARA Desktop installer (Inno Setup 6). Built by packaging\build_dist.ps1, which passes
; /DAppVersion, /DSourceDir (dist\DARA), /DOutputDir (dist), /DRepoRoot, /DMaxAppDirLength and
; the structure-library numbers /DLib* (read from src\dara\data\structure_packs.json).
;
; Per-user install without admin rights: the BGMN programs are downloaded into the install
; folder after the files are copied (their licence forbids bundling them), so it must stay
; writable. User data stays in %USERPROFILE%\.dara-server and is never touched by setup or
; uninstall. The optional structure library goes where the bundled app reads it:
; %USERPROFILE%\.dara\dara_downloaded_cifs, dara_mp_structures and dara_sticks.
;
; Command line (besides Inno's own /SILENT, /VERYSILENT, /DIR=, /LOG=...):
;   /LIBRARY      download the recommended structure library (silent installs skip it by default)
;   /NOLIBRARY    do not download it (also unticks the box in the wizard)
;   /LIBRARYELEMENTS=Li,Co,O   download only these elements instead of the recommended library
;                 (tests, small offline mirrors); a silent install still needs /LIBRARY as well
; The Materials Project key is never taken on the command line: it is asked in the wizard or
; on the first start of DARA.

#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef LibStructures
  #error The structure-library numbers (/DLib...) are required: build with packaging\build_dist.ps1
#endif
#ifndef MaxAppDirLength
  ; MAX_PATH minus the longest relative path inside dist\DARA (build_dist.ps1 measures it).
  #define MaxAppDirLength 100
#endif

[Setup]
; Fixed AppId: a newer installer upgrades the existing installation in place.
AppId={{6C0E5D7B-2F0A-4E52-9B86-3D1A7E4C8B21}
AppName=DARA
AppVersion={#AppVersion}
AppVerName=DARA {#AppVersion}
AppPublisher=DARA
AppPublisherURL=https://github.com/CederGroupHub/dara
DefaultDirName={localappdata}\Programs\DARA
DefaultGroupName=DARA
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=DARA-Setup-{#AppVersion}
SetupIconFile={#RepoRoot}\logo\dara.ico
UninstallDisplayIcon={app}\DARA.exe
UninstallDisplayName=DARA {#AppVersion}
LicenseFile={#SourceDir}\LICENSE.txt
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
WizardStyle=modern
; DARA.exe holds this mutex while it runs; setup and uninstall ask the user to close it first.
AppMutex=DARA-Desktop-SingleInstance
CloseApplications=yes
; Held while setup runs: a second setup asks to close this one, and DARA.exe does not offer to
; continue the library download that this setup is still running.
SetupMutex=DARA-Setup-Running
; The Materials Project key is a user environment variable: tell Explorer it changed.
ChangesEnvironment=yes

[Messages]
WizardSelectComponents=Structure library
SelectComponentsDesc=Download reference structures now, so DARA is ready when it first starts
SelectComponentsLabel2=The recommended library holds the COD and Materials Project structures of the elements used most in battery and semiconductor work. With it, phase search and quick ranking start at once and also work offline. It downloads after the program files are copied (about {#LibTime}); you can stop it and finish later.%n%nClear the box to skip it, for example on an offline PC. You can download it later inside DARA (Advanced > Structure library).
; The library is not counted here: it goes to the profile drive (its size is shown above this
; line and NextButtonClick checks that drive).
ComponentsDiskSpaceMBLabel=DARA needs at least [mb] MB of disk space in the installation folder.
ComponentsDiskSpaceGBLabel=DARA needs at least [gb] GB of disk space in the installation folder.
; Inno can only put KB figures in this message (4,882,816 KB reads as huge), so it has none;
; the page itself shows the needed space in MB or GB.
DiskSpaceWarning=The selected drive does not have enough free space. The space DARA needs is shown at the bottom of this page.%n%nDo you want to continue anyway?

[Types]
; One type only, so the type list is hidden and the library box is ticked by default.
Name: "custom"; Description: "Custom"; Flags: iscustom

[Components]
Name: "library"; Description: "Download the recommended structure library ({#LibStructures} structures, {#LibDownloadMB} MB)"; Types: custom

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; An upgrade replaces the bundled Python and the server/UI files completely, so files that a
; newer version no longer ships cannot linger. The downloaded BGMN folder is kept.
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\src\dara\server"
Type: filesandordirs; Name: "{app}\scripts"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\DARA"; Filename: "{app}\DARA.exe"
Name: "{autodesktop}\DARA"; Filename: "{app}\DARA.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\DARA.exe"; Description: "{cm:LaunchProgram,DARA}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Created by DARA.exe at run time inside the install folder: the downloaded BGMN programs,
; Python caches, the CA bundle and launcher logs. Then the WebView2 cache and launcher logs.
Type: filesandordirs; Name: "{app}\src"
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\scripts"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\artifacts"
Type: dirifempty; Name: "{app}"
Type: filesandordirs; Name: "{localappdata}\DARA\WebView2"
Type: filesandordirs; Name: "{localappdata}\DARA\logs"
Type: files; Name: "{localappdata}\DARA\structure-library-pending"
Type: files; Name: "{localappdata}\DARA\structure-library-failed"

[Code]
const
  { DARA.exe offers to continue the library download while this file exists. }
  PendingMarker = 'structure-library-pending';
  KeyUrl = 'https://next-gen.materialsproject.org/api';

var
  KeyPage: TInputQueryWizardPage;
  ProgressPage: TOutputProgressWizardPage;
  LaterButton: TNewButton;
  HadKey, LaterClicked: Boolean;
  CancelFile, LastPhase, LastError, CaBundle, Notes: String;

function SetEnvironmentVariable(Name, Value: String): Boolean;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

{ While ExecAndLogOutput waits, Inno disables the wizard and handles no input; the download's
  output callback runs this message loop so the Finish later button works. }
type
  TMsg = record
    hwnd: HWND;
    message: UINT;
    wParam: Longint;
    lParam: Longint;
    time: DWORD;
    pt: TPoint;
  end;

function PeekMessage(var Msg: TMsg; Wnd: HWND; FilterMin, FilterMax, RemoveMsg: UINT): BOOL;
  external 'PeekMessageW@user32.dll stdcall';
function TranslateMessage(const Msg: TMsg): BOOL;
  external 'TranslateMessage@user32.dll stdcall';
function DispatchMessage(const Msg: TMsg): Longint;
  external 'DispatchMessageW@user32.dll stdcall';
function EnableWindow(Wnd: HWND; Enable: BOOL): BOOL;
  external 'EnableWindow@user32.dll stdcall';

procedure ProcessMessages;
var
  Msg: TMsg;
begin
  EnableWindow(WizardForm.Handle, True);
  while PeekMessage(Msg, 0, 0, 0, 1 { PM_REMOVE }) do
  begin
    TranslateMessage(Msg);
    DispatchMessage(Msg);
  end;
end;

function HasSwitch(const Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Name) = 0 then
      Result := True;
end;

function DataDir: String;
begin
  Result := ExpandConstant('{localappdata}\DARA');
end;

{ Where the bundled app keeps downloaded structures (structure_db.cod_cache_dir without a
  pyproject.toml next to src). shortcut: ignores DARA_COD/MP/STICK_CACHE_DIR overrides, read them
  here too if those are ever documented for end users. }
function LibraryRoot: String;
begin
  Result := ExpandConstant('{%USERPROFILE}\.dara');
end;

function HasLibrary: Boolean;
begin
  Result := DirExists(LibraryRoot + '\dara_downloaded_cifs') or DirExists(LibraryRoot + '\dara_mp_structures') or
    DirExists(LibraryRoot + '\dara_sticks');
end;

function SavedKey: String;
begin
  if not RegQueryStringValue(HKCU, 'Environment', 'MP_API_KEY', Result) then
    Result := '';
  Result := Trim(Result);
end;

function DirTooLong: String;
var
  Dir: String;
begin
  Result := '';
  Dir := RemoveBackslash(WizardDirValue);
  if Length(Dir) > {#MaxAppDirLength} then
    Result := Format('The installation folder path is too long (%d characters):'#13#10'%s'#13#10#13#10 +
      'DARA''s bundled Python needs a folder path of at most %d characters. Choose a shorter folder, for example C:\DARA.', [
      Length(Dir), Dir, {#MaxAppDirLength}]);
end;

{ ---- Wizard pages -------------------------------------------------------------------------- }

procedure OpenKeyPage(Sender: TObject);
var
  Code: Integer;
begin
  ShellExecAsOriginalUser('open', KeyUrl, '', '', SW_SHOWNORMAL, ewNoWait, Code);
end;

procedure InitializeWizard;
var
  Elements, Link: TNewStaticText;
  Note: String;
begin
  { Silent installs download the library only with /LIBRARY; /NOLIBRARY unticks it everywhere. }
  if HasSwitch('/NOLIBRARY') or (WizardSilent and not HasSwitch('/LIBRARY')) then
    WizardSelectComponents('!library')
  else if HasSwitch('/LIBRARY') then
    WizardSelectComponents('library');

  Elements := TNewStaticText.Create(WizardForm);
  Elements.Parent := WizardForm.SelectComponentsPage;
  Elements.AutoSize := False;
  Elements.WordWrap := True;
  Elements.Left := WizardForm.ComponentsList.Left;
  Elements.Width := WizardForm.ComponentsList.Width;
  { Three lines: a long profile path wraps onto its own line. }
  Elements.Height := ScaleY(45);
  Elements.Font.Color := clGrayText;
  Elements.Caption := 'Elements: {#LibElements}. About {#LibDiskMB} MB on disk in ' + LibraryRoot + '.';
  WizardForm.ComponentsList.Height := ScaleY(26);
  Elements.Top := WizardForm.ComponentsList.Top + WizardForm.ComponentsList.Height + ScaleY(6);

  HadKey := SavedKey <> '';
  { The subcaption is set here: the page places the field below it when it is created. }
  if HadKey then
    Note := 'A key is already saved for your account. Leave the field empty to keep it, or paste a new key to replace it.'
  else
    Note := 'If you skip, DARA uses COD only, and the structure library holds only its {#LibCodCount} COD structures. ' +
      'To add a key later, run this setup again.';
  KeyPage := CreateInputQueryPage(wpSelectTasks, 'Materials Project API key',
    'Optional: lets DARA use Materials Project structures as well as COD',
    'Sign in at materialsproject.org (free) and copy the API key from your dashboard. ' +
    'Setup saves it for your Windows account, where DARA reads it, and never shows it again.' + #13#10#13#10 + Note);
  KeyPage.Add('API key (leave empty to skip):', True);
  Link := TNewStaticText.Create(KeyPage);
  Link.Parent := KeyPage.Surface;
  Link.Caption := 'Get a free key (opens materialsproject.org in your browser)';
  Link.Font.Color := clHotLight;
  Link.Font.Style := [fsUnderline];
  Link.Cursor := crHand;
  Link.OnClick := @OpenKeyPage;
  Link.Left := KeyPage.Edits[0].Left;
  Link.Top := KeyPage.Edits[0].Top + KeyPage.Edits[0].Height + ScaleY(10);
end;

procedure CurPageChanged(CurPageID: Integer);
var
  Text: String;
  Delta, P: Integer;
begin
  if (CurPageID = wpFinished) and (Notes <> '') then
  begin
    { Notes go before the closing "Click Finish to exit Setup." line. }
    Text := WizardForm.FinishedLabel.Caption;
    P := Pos('Click Finish', Text);
    if P = 0 then
      P := Length(Text) + 1;
    WizardForm.FinishedLabel.Caption := Copy(Text, 1, P - 1) + Notes + #13#10 + Copy(Text, P, Length(Text));
    Delta := WizardForm.AdjustLabelHeight(WizardForm.FinishedLabel);
    WizardForm.RunList.Top := WizardForm.RunList.Top + Delta;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Problem, Drive, Note: String;
  Free, Total: Int64;
begin
  Result := True;
  { Silent installs call this too and would repeat it forever; PrepareToInstall stops them. }
  if (CurPageID = wpSelectDir) and not WizardSilent then
  begin
    Problem := DirTooLong;
    if Problem <> '' then
    begin
      MsgBox(Problem, mbError, MB_OK);
      Result := False;
    end;
  end
  else if (CurPageID = wpSelectComponents) and WizardIsComponentSelected('library') then
  begin
    { Inno checks only the program's drive; the library goes to the profile drive.
      shortcut: on the same drive the program's own size is not added here (Inno checks it on its
      own); have build_dist.ps1 pass the app size if that ever matters. }
    Drive := ExtractFileDrive(LibraryRoot);
    if GetSpaceOnDisk64(Drive + '\', Free, Total) and (Free < {#LibDiskBytes}) then
    begin
      Note := '';
      if HasLibrary then
        Note := #13#10#13#10'Structures already in ' + LibraryRoot + ' are not downloaded again, so less space may be needed.';
      { Suppressible: /VERYSILENT /SUPPRESSMSGBOXES must not wait for an answer. }
      { No line may start with "[": ISCC reads it as a section tag, even inside [Code]. }
      Result := SuppressibleMsgBox(Format('The structure library needs about {#LibDiskMB} MB on drive %s, which has %d MB free.', [Drive,
        Free div 1048576]) + Note + #13#10#13#10'Continue anyway?', mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDYES) = IDYES;
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  { Also covers silent installs with /DIR=, before any file is copied. }
  Result := DirTooLong;
end;

{ ---- After the files are copied: key, BGMN, structure library ------------------------------- }

procedure SaveKey;
var
  Key: String;
begin
  Key := Trim(KeyPage.Values[0]);
  KeyPage.Values[0] := '';
  if Key <> '' then
  begin
    { Same place as scripts\materials_project_key.ps1; never logged, never in the uninstall data. }
    if RegWriteStringValue(HKCU, 'Environment', 'MP_API_KEY', Key) then
      DeleteFile(DataDir + '\mp-api-setup-skipped')
    else
      Notes := Notes + 'The Materials Project key could not be saved; DARA asks for it when it starts.'#13#10;
  end
  else if not WizardSilent and not HadKey then
  begin
    { Skipped on purpose: DARA.exe does not ask again (as when the key dialog is skipped). }
    ForceDirectories(DataDir);
    SaveStringToFile(DataDir + '\mp-api-setup-skipped', '', False);
  end;
  { The download below runs in child processes: give them the key without a command line. }
  if Key = '' then
    Key := SavedKey;
  if Key <> '' then
    SetEnvironmentVariable('MP_API_KEY', Key);
  Key := '';
end;

procedure OnCaLine(const S: String; const Error, FirstLine: Boolean);
begin
  if (CompareText(ExtractFileExt(Trim(S)), '.pem') = 0) and FileExists(Trim(S)) then
    CaBundle := Trim(S);
end;

procedure OnBgmnLine(const S: String; const Error, FirstLine: Boolean);
var
  P: Integer;
begin
  P := Pos('BGMN download: ', S);
  if P > 0 then
    ProgressPage.SetProgress(StrToIntDef(Copy(S, P + 15, Pos('%', S) - P - 15), 0), 100);
end;

function NextField(var Rest: String): String;
var
  P: Integer;
begin
  P := Pos(#9, Rest);
  if P = 0 then
  begin
    Result := Rest;
    Rest := '';
  end
  else
  begin
    Result := Copy(Rest, 1, P - 1);
    Rest := Copy(Rest, P + 1, Length(Rest));
  end;
end;

{ One line of scripts\library_download.py: PROGRESS<TAB>phase<TAB>done<TAB>total<TAB>eta_s<TAB>message }
procedure OnLibraryLine(const S: String; const Error, FirstLine: Boolean);
var
  Rest, Phase, Eta, Msg, Title, Detail: String;
  Done, Total, Seconds: Integer;
begin
  { Every line (TICK about every 0.3 s) lets the Finish later button react. Back, Next and
    Cancel stay disabled on their own during the installation. }
  if not WizardSilent then
    ProcessMessages;
  if Error or (Copy(S, 1, 9) <> 'PROGRESS'#9) then
    Exit;
  Rest := Copy(S, 10, Length(S));
  Phase := NextField(Rest);
  Done := StrToIntDef(NextField(Rest), 0);
  Total := StrToIntDef(NextField(Rest), 0);
  Eta := NextField(Rest);
  Msg := Rest;
  LastPhase := Phase;
  if Phase = 'error' then
    LastError := Msg;
  if Phase = 'select' then Title := 'Step 1 of 4: selecting the structures'
  else if Phase = 'cod' then Title := 'Step 2 of 4: downloading COD structures'
  else if Phase = 'mp' then Title := 'Step 3 of 4: downloading Materials Project structures'
  else if Phase = 'sticks' then Title := 'Step 4 of 4: precomputing diffraction patterns'
  else Title := 'Structure library';
  if LaterClicked then
    Title := 'Stopping the download (up to 15 seconds)...';
  Detail := Msg;
  if (Total > 1) and ((Phase = 'cod') or (Phase = 'mp') or (Phase = 'sticks')) then
  begin
    Detail := Format('%d of %d', [Done, Total]);
    Seconds := StrToIntDef(Eta, -1);
    if Seconds >= 0 then
      Detail := Detail + Format(', about %d min left in this step', [(Seconds + 59) div 60]);
  end;
  ProgressPage.SetText(Title, Detail);
  if Total > 0 then
    ProgressPage.SetProgress(Done, Total);
end;

procedure FinishLaterClick(Sender: TObject);
begin
  LaterClicked := True;
  SaveStringToFile(CancelFile, '', False);
  LaterButton.Enabled := False;
  LaterButton.Caption := 'Stopping...';
  ProgressPage.SetText('Stopping the download (up to 15 seconds)...', 'Everything downloaded so far is kept.');
end;

{ /LIBRARYELEMENTS=Li,Co,O (letters and commas only), or '' for the recommended pack. }
function LibraryElements: String;
var
  I: Integer;
begin
  Result := ExpandConstant('{param:LIBRARYELEMENTS|}');
  for I := 1 to Length(Result) do
    if Pos(Uppercase(Result[I]), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ,') = 0 then
    begin
      Result := '';
      Exit;
    end;
end;

function LibraryArguments: String;
var
  Elements: String;
begin
  Elements := LibraryElements;
  if Elements <> '' then
    Result := '--elements ' + Elements
  else
    Result := '--packs recommended';
  if GetEnv('MP_API_KEY') <> '' then
    Result := Result + ' --sources cod,mp'
  else
    Result := Result + ' --sources cod';
end;

{ Runs the bundled Python hidden in the app folder; returns its exit code, or -1 when it did not start.
  -X utf8: ExecAndLogOutput decodes the output as UTF-8, and -I ignores PYTHONIOENCODING. }
function RunPython(const Params: String; const OnLog: TOnLog): Integer;
begin
  try
    if not ExecAndLogOutput(ExpandConstant('{app}\python\python.exe'), '-X utf8 ' + Params, ExpandConstant('{app}'), SW_HIDE,
      ewWaitUntilTerminated, Result, OnLog) then
      Result := -1;
  except
    Log(GetExceptionMessage);
    Result := -1;
  end;
end;

procedure DownloadLibrary;
var
  Code: Integer;
  Marker: String;
begin
  Marker := DataDir + '\' + PendingMarker;
  CancelFile := ExpandConstant('{tmp}\library.cancel');
  ForceDirectories(DataDir);
  SaveStringToFile(Marker, LibraryElements, False);
  { DARA.exe asks to continue at most once a day after a failed resume; a new setup run resets that. }
  DeleteFile(DataDir + '\structure-library-failed');
  LastPhase := '';
  LastError := '';
  ProgressPage.SetText('Downloading the structure library...', '');
  ProgressPage.SetProgress(0, 100);
  LaterButton.Visible := True;
  Code := RunPython('-I "' + ExpandConstant('{app}\scripts\library_download.py') + '" --cancel-file "' + CancelFile +
    '" --log "' + DataDir + '\logs\library-download.log" -- ' + LibraryArguments, @OnLibraryLine);
  LaterButton.Visible := False;
  Log(Format('Structure library download: exit code %d, last step %s', [Code, LastPhase]));
  if Code = 0 then
    DeleteFile(Marker)
  else if LaterClicked then
    Notes := Notes + 'The structure library download is paused. DARA offers to continue it when it starts ' +
      '(also under Advanced > Structure library).'#13#10
  else
  begin
    if Code = -1 then
      LastError := 'the download did not start'
    else if LastError = '' then
      LastError := Format('exit code %d', [Code]);
    Notes := Notes + 'The structure library could not be downloaded completely (' + LastError + '). ' +
      'DARA offers to continue the download when it starts.'#13#10;
  end;
end;

procedure RunDownloads;
begin
  ProgressPage := CreateOutputProgressPage('Downloading',
    'BGMN and the structure library are downloaded now. Finish later keeps what is downloaded; DARA offers to continue when it starts.');
  LaterButton := TNewButton.Create(ProgressPage);
  LaterButton.Parent := ProgressPage.Surface;
  LaterButton.Caption := 'Finish later';
  LaterButton.Left := ProgressPage.ProgressBar.Left;
  LaterButton.Top := ProgressPage.ProgressBar.Top + ProgressPage.ProgressBar.Height + ScaleY(16);
  LaterButton.Width := ScaleX(110);
  LaterButton.Height := WizardForm.NextButton.Height;
  LaterButton.Visible := False;
  LaterButton.OnClick := @FinishLaterClick;
  if not WizardSilent then
    ProgressPage.Show;
  try
    ProgressPage.SetText('Checking certificates...', '');
    CaBundle := '';
    RunPython('-I "' + ExpandConstant('{app}\scripts\windows_ca_bundle.py') + '" --repo-root "' + ExpandConstant('{app}') + '"',
      @OnCaLine);
    if CaBundle <> '' then
    begin
      { Networks that re-sign HTTPS: same CA bundle DARA.exe sets for its Python processes. }
      SetEnvironmentVariable('SSL_CERT_FILE', CaBundle);
      SetEnvironmentVariable('REQUESTS_CA_BUNDLE', CaBundle);
      SetEnvironmentVariable('CURL_CA_BUNDLE', CaBundle);
    end;

    ProgressPage.SetText('Downloading the BGMN refinement programs (about 7 MB)...', 'From cedergrouphub.github.io; BGMN is not part of this installer.');
    ProgressPage.SetProgress(0, 100);
    if RunPython('-I "' + ExpandConstant('{app}\scripts\ensure_bgmn.py') + '"', @OnBgmnLine) <> 0 then
      Notes := Notes + 'BGMN could not be downloaded now; DARA downloads it when it first starts.'#13#10;

    if WizardIsComponentSelected('library') then
      DownloadLibrary
    else
      DeleteFile(DataDir + '\' + PendingMarker);
  finally
    ProgressPage.Hide;
  end;
  if Notes <> '' then
    Log('Notes: ' + Notes);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    SaveKey;
    RunDownloads;
  end;
end;

{ ---- Uninstall ------------------------------------------------------------------------------ }

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Root: String;
begin
  if (CurUninstallStep <> usUninstall) or UninstallSilent then
    Exit;
  Root := LibraryRoot;
  if not HasLibrary then
    Exit;
  if MsgBox('Also delete the downloaded structure library and caches?' + #13#10#13#10 +
    Root + '\dara_downloaded_cifs, dara_mp_structures and dara_sticks' + #13#10#13#10 +
    'Choose No to keep them if you may install DARA again: downloading them again takes hours. ' +
    'Your analyses and settings (.dara-server) are kept either way.',
    mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
  begin
    DelTree(Root + '\dara_downloaded_cifs', True, True, True);
    DelTree(Root + '\dara_mp_structures', True, True, True);
    DelTree(Root + '\dara_sticks', True, True, True);
    DeleteFile(Root + '\structure_library.log');
    RemoveDir(Root);
  end;
end;
