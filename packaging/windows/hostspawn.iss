; HostSpawn for Windows: the HostSpawn app, which connects to HostSpawn running on your Linux server.
; Built by .github/workflows/windows.yml:  iscc /DVersion=0.9.0 /DBundle=<flutter windows release folder> hostspawn.iss
; Installs for the current user only (no admin password), adds Start menu and desktop shortcuts, and uninstalls cleanly
; from Settings > Apps. Nothing is sent anywhere by the installer.
#ifndef Version
  #define Version "0.0.0-dev"
#endif
#ifndef Bundle
  #define Bundle "..\..\frontend\app\build\windows\x64\runner\Release"
#endif

[Setup]
AppId={{6E2A9C41-5B7D-4E0F-9C3A-1F2B8D4E7A60}
AppName=HostSpawn
AppVersion={#Version}
AppVerName=HostSpawn {#Version} by TrixemTV
AppPublisher=TrixemTV
AppSupportURL=https://discord.gg/AaBwz8szM
DefaultDirName={localappdata}\Programs\HostSpawn
DefaultGroupName=HostSpawn
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
LicenseFile=terms.txt
InfoBeforeFile=before.txt
OutputBaseFilename=HostSpawn-Setup-{#Version}-windows-x64
OutputDir=..\..\dist
SetupIconFile=..\..\frontend\app\windows\runner\resources\app_icon.ico
UninstallDisplayIcon={app}\hostspawn.exe
WizardStyle=modern
Compression=lzma2
SolidCompression=yes

[Tasks]
Name: "desktopicon"; Description: "Add a HostSpawn shortcut to the desktop"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#Bundle}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\HostSpawn"; Filename: "{app}\hostspawn.exe"
Name: "{group}\Uninstall HostSpawn"; Filename: "{uninstallexe}"
Name: "{autodesktop}\HostSpawn"; Filename: "{app}\hostspawn.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\hostspawn.exe"; Description: "Open HostSpawn"; Flags: nowait postinstall skipifsilent
