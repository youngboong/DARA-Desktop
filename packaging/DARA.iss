; DARA Desktop installer (Inno Setup 6). Built by packaging\build_dist.ps1, which passes
; /DAppVersion, /DSourceDir (dist\DARA), /DOutputDir (dist) and /DRepoRoot.
;
; Per-user install without admin rights: the BGMN programs are downloaded into the install
; folder on first start (their licence forbids bundling them), so it must stay writable.
; User data stays in %USERPROFILE%\.dara-server and is never touched by setup or uninstall.

#ifndef AppVersion
  #error AppVersion is required
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
