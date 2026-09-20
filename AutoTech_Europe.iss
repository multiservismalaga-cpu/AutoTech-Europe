[Setup]
AppName=AutoTech Europe
AppVersion=1.3.0
DefaultDirName={autopf}\AutoTech Europe
DefaultGroupName=AutoTech Europe
OutputDir=installer
OutputBaseFilename=AutoTech_Europe_Setup
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
[Files]
Source: "dist\AutoTech_Europe.exe"; DestDir: "{app}"; Flags: ignoreversion
[Icons]
Name: "{group}\AutoTech Europe"; Filename: "{app}\AutoTech_Europe.exe"
Name: "{commondesktop}\AutoTech Europe"; Filename: "{app}\AutoTech_Europe.exe"
[Run]
Filename: "{app}\AutoTech_Europe.exe"; Description: "Abrir AutoTech Europe"; Flags: nowait postinstall skipifsilent