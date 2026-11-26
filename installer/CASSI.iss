#ifndef CassiDistDir
  #error Supply /DCassiDistDir=<absolute path to the current PyInstaller distribution>
#endif
#ifndef CassiInitialDataDir
  #error Supply /DCassiInitialDataDir=<absolute path to the validated initial-data payload>
#endif
#ifndef CassiTesseractRoot
  #error Supply /DCassiTesseractRoot=<audited Tesseract bundle containing license notices>
#endif
#if !DirExists(CassiDistDir)
  #error CassiDistDir does not exist; refusing to compile against a stale distribution
#endif
#if !FileExists(CassiDistDir + "\CASSI.exe")
  #error CassiDistDir does not contain CASSI.exe
#endif
#if !FileExists(CassiInitialDataDir + "\cassi.db")
  #error CassiInitialDataDir does not contain cassi.db
#endif
#if !FileExists(CassiInitialDataDir + "\certificate_analysis.db")
  #error CassiInitialDataDir does not contain certificate_analysis.db
#endif
#if !FileExists(CassiInitialDataDir + "\initial-data-manifest.json")
  #error CassiInitialDataDir does not contain initial-data-manifest.json
#endif
#if !DirExists(CassiInitialDataDir + "\Acervos")
  #error CassiInitialDataDir does not contain the Acervos directory
#endif
#if !FileExists(CassiTesseractRoot + "\TESSDATA-LICENSE.txt")
  #error CassiTesseractRoot does not contain TESSDATA-LICENSE.txt
#endif
#if !FileExists(CassiTesseractRoot + "\tesseract-copyright.txt")
  #error CassiTesseractRoot does not contain tesseract-copyright.txt
#endif
#if !FileExists(CassiTesseractRoot + "\MICROSOFT-VC-RUNTIME-NOTICE.txt")
  #error CassiTesseractRoot does not contain MICROSOFT-VC-RUNTIME-NOTICE.txt
#endif

[Setup]
AppId={{D8E0D660-A77A-4A56-A19A-B319CA550001}
AppName=CASSI - Acervo Inteligente
AppVersion=1.0.0
AppPublisher=CASSI
DefaultDirName={autopf}\CASSI
DefaultGroupName=CASSI - Acervo Inteligente
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=CASSI-Setup-1.0.0
Uninstallable=yes
DisableProgramGroupPage=yes
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
RestartApplications=no

[Dirs]
Name: "{commonappdata}\CASSI"; Permissions: users-modify; Flags: uninsneveruninstall; Check: ShouldCreatePersistentDirs
Name: "{commonappdata}\CASSI\Acervos"; Permissions: users-modify; Flags: uninsneveruninstall; Check: ShouldCreatePersistentDirs
Name: "{commonappdata}\CASSI\Backups"; Permissions: users-modify; Flags: uninsneveruninstall; Check: ShouldCreatePersistentDirs
Name: "{commonappdata}\CASSI\logs"; Permissions: users-modify; Flags: uninsneveruninstall; Check: ShouldCreatePersistentDirs
Name: "{code:GetInitialStageDir}"; Permissions: admins-full system-full users-readexec; Flags: uninsneveruninstall; Check: ShouldInstallInitialData
Name: "{code:GetInitialStageDir}\Acervos"; Permissions: admins-full system-full users-readexec; Flags: uninsneveruninstall; Check: ShouldInstallInitialData
Name: "{code:GetInitialStageDir}\Backups"; Permissions: admins-full system-full users-readexec; Flags: uninsneveruninstall; Check: ShouldInstallInitialData
Name: "{code:GetInitialStageDir}\logs"; Permissions: admins-full system-full users-readexec; Flags: uninsneveruninstall; Check: ShouldInstallInitialData

[Files]
Source: "{#CassiDistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#CassiTesseractRoot}\*.txt"; DestDir: "{app}\tesseract"; Flags: ignoreversion
Source: "{#CassiInitialDataDir}\cassi.db"; DestDir: "{code:GetInitialStageDir}"; Flags: ignoreversion uninsneveruninstall; Check: ShouldInstallInitialData
Source: "{#CassiInitialDataDir}\certificate_analysis.db"; DestDir: "{code:GetInitialStageDir}"; Flags: ignoreversion uninsneveruninstall; Check: ShouldInstallInitialData
Source: "{#CassiInitialDataDir}\Acervos\*.pdf"; DestDir: "{code:GetInitialStageDir}\Acervos"; Flags: ignoreversion recursesubdirs createallsubdirs uninsneveruninstall; Check: ShouldInstallInitialData
Source: "{#CassiInitialDataDir}\initial-data-manifest.json"; DestDir: "{code:GetInitialStageDir}"; Flags: ignoreversion uninsneveruninstall; Check: ShouldInstallInitialData; AfterInstall: CommitInitialData

[Icons]
Name: "{autodesktop}\CASSI - Acervo Inteligente"; Filename: "{app}\CASSI.exe"; WorkingDir: "{app}"
Name: "{group}\CASSI - Acervo Inteligente"; Filename: "{app}\CASSI.exe"; WorkingDir: "{app}"
Name: "{group}\Desinstalar CASSI"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\CASSI.exe"; Description: "Iniciar CASSI - Acervo Inteligente"; Flags: postinstall nowait skipifsilent

; ProgramData is deliberately not removed by the uninstaller. Database, archive settings,
; logs and backups remain available after upgrades and uninstall.

[Code]
var
  InstallInitialData: Boolean;
  RecoveryRequired: Boolean;
  RecoveryFailed: Boolean;
  RecoveryFailureMessage: String;

function ShouldInstallInitialData: Boolean;
begin
  Result := InstallInitialData;
end;

function ShouldCreatePersistentDirs: Boolean;
begin
  Result := not InstallInitialData;
end;

function GetInitialStageDir(Param: String): String;
begin
  Result := ExpandConstant('{commonappdata}') + '\CASSI.install-staging';
end;

function DirectoryHasEntries(const Directory: String): Boolean;
var
  FindRec: TFindRec;
begin
  Result := False;
  if not DirExists(Directory) then
    Exit;
  if FindFirst(AddBackslash(Directory) + '*', FindRec) then
  try
    repeat
      if (FindRec.Name <> '.') and (FindRec.Name <> '..') then
      begin
        Result := True;
        Exit;
      end;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
end;

function PrepareInitialStage(const StageDir: String; var ErrorMessage: String): Boolean;
var
  IcaclsPath: String;
  Parameters: String;
  ResultCode: Integer;
begin
  Result := False;
  ErrorMessage := '';
  if not ForceDirectories(StageDir) then
  begin
    ErrorMessage := 'Nao foi possivel criar a pasta temporaria de preparacao dos dados iniciais.';
    Exit;
  end;

  IcaclsPath := ExpandConstant('{sys}\icacls.exe');
  if not FileExists(IcaclsPath) then
  begin
    ErrorMessage := 'A ferramenta do Windows para proteger os dados temporarios nao foi encontrada.';
    Exit;
  end;
  Parameters := '"' + StageDir + '" /inheritance:r /grant:r ' +
    '*S-1-5-32-544:(OI)(CI)F *S-1-5-18:(OI)(CI)F *S-1-5-32-545:(OI)(CI)RX /T /C';
  if not Exec(IcaclsPath, Parameters, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    ErrorMessage := 'Nao foi possivel iniciar a protecao de permissoes da pasta temporaria.';
    Exit;
  end;
  if ResultCode <> 0 then
  begin
    ErrorMessage := 'A protecao de permissoes da pasta temporaria falhou (codigo ' +
      IntToStr(ResultCode) + ').';
    Exit;
  end;
  Result := True;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  DataDir: String;
  DatabasePath: String;
  CertificateDatabasePath: String;
  ArchivePath: String;
  SettingsPath: String;
  BackupsPath: String;
  LogsPath: String;
  ManifestPath: String;
  PendingPath: String;
  PendingTempPath: String;
  PendingContents: AnsiString;
  ExistingContents: AnsiString;
  StageError: String;
begin
  Result := '';
  RecoveryRequired := False;
  RecoveryFailed := False;
  RecoveryFailureMessage := '';
  DataDir := ExpandConstant('{commonappdata}') + '\CASSI';
  DatabasePath := DataDir + '\cassi.db';
  CertificateDatabasePath := DataDir + '\certificate_analysis.db';
  ArchivePath := DataDir + '\Acervos';
  SettingsPath := DataDir + '\settings.json';
  BackupsPath := DataDir + '\Backups';
  LogsPath := DataDir + '\logs';
  ManifestPath := DataDir + '\initial-data-manifest.json';
  PendingPath := ExpandConstant('{commonappdata}') + '\CASSI.install-pending';
  PendingTempPath := PendingPath + '.tmp';
  PendingContents := 'CASSI_INITIAL_INSTALL_PENDING_V1' + #13#10;
  InstallInitialData := False;

  if FileExists(PendingPath) then
  begin
    if not LoadStringFromFile(PendingPath, ExistingContents) then
    begin
      ExistingContents := '';
    end;
  if ExistingContents <> PendingContents then
    begin
      Result := 'Foi encontrado um marcador de instalação pendente inválido. Os dados foram preservados; ' +
        'a instalação foi bloqueada para recuperação controlada.';
      Exit;
    end;
    if DirExists(DataDir) then
    begin
      RecoveryRequired := True;
      if FileExists(DatabasePath) and FileExists(ManifestPath) and DirExists(ArchivePath) then
      begin
        { A publicação por rename já ocorreu; o launcher fará a validação final e removerá o marcador. }
        InstallInitialData := False;
        Exit;
      end;
      Result := 'A instalação inicial foi interrompida durante a publicação e deixou dados incompletos. ' +
        'Nenhum arquivo foi substituído. Preserve a pasta CASSI e solicite recuperação controlada.';
      Exit;
    end;
    { Staging incompleto pode ser refeito pelo mesmo instalador; ele contém somente payload temporário. }
    InstallInitialData := True;
    if not PrepareInitialStage(GetInitialStageDir(''), StageError) then
    begin
      InstallInitialData := False;
      Result := StageError;
      Exit;
    end;
    Exit;
  end;

  if DirExists(GetInitialStageDir('')) then
  begin
    Result := 'Foi encontrado staging sem marcador de instalação. Nada foi sobrescrito; ' +
      'a instalação foi bloqueada para inspeção controlada.';
    Exit;
  end;

  InstallInitialData := not FileExists(DatabasePath);
  if InstallInitialData and
     (DirExists(DataDir) or FileExists(CertificateDatabasePath) or FileExists(SettingsPath) or
      FileExists(ManifestPath) or DirectoryHasEntries(ArchivePath) or
      DirectoryHasEntries(BackupsPath) or DirectoryHasEntries(LogsPath)) then
  begin
    InstallInitialData := False;
    Result := 'Foram encontrados dados existentes do CASSI sem o banco principal. ' +
      'A instalação inicial foi bloqueada para evitar mistura ou sobrescrita de dados.';
    Exit;
  end;
  if InstallInitialData then
  begin
    if not SaveStringToFile(PendingTempPath, PendingContents, False) then
    begin
      InstallInitialData := False;
      Result := 'Não foi possível preparar o marcador temporário de instalação em ProgramData. ' +
        'A instalação foi interrompida antes da cópia dos dados.';
      Exit;
    end;
    if not RenameFile(PendingTempPath, PendingPath) then
    begin
      InstallInitialData := False;
      Result := 'Não foi possível publicar atomicamente o marcador de instalação pendente. ' +
        'A instalação foi interrompida antes da cópia dos dados.';
    end;
  end;
  if InstallInitialData and not PrepareInitialStage(GetInitialStageDir(''), StageError) then
  begin
    InstallInitialData := False;
    Result := StageError;
  end;
end;

procedure CommitInitialData;
var
  ResultCode: Integer;
  Parameters: String;
begin
  if not InstallInitialData then
    Exit;
  Parameters := '--_cassi_commit_initial_data "' + GetInitialStageDir('') + '" "' +
    ExpandConstant('{commonappdata}') + '\CASSI"';
  if not Exec(ExpandConstant('{app}\CASSI.exe'), Parameters, ExpandConstant('{app}'),
       SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    RaiseException('Não foi possível validar e publicar o staging inicial. O marcador e os dados temporários foram preservados.');
  if ResultCode <> 0 then
    RaiseException('O banco ou o acervo inicial está incompleto/incompatível. O CASSI não foi publicado; ' +
      'o staging foi preservado para retomada segura.');
end;

procedure RecoverPendingInitialData;
var
  ResultCode: Integer;
  Parameters: String;
begin
  if not RecoveryRequired then
    Exit;
  Parameters := '--_cassi_recover_pending_initial_install';
  if not Exec(ExpandConstant('{app}\CASSI.exe'), Parameters, ExpandConstant('{app}'),
       SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    RecoveryFailed := True;
    RecoveryFailureMessage := 'Nao foi possivel iniciar a validacao dos dados publicados. ' +
      'O marcador pendente foi preservado.';
    RaiseException(RecoveryFailureMessage);
  end;
  if ResultCode <> 0 then
  begin
    RecoveryFailed := True;
    RecoveryFailureMessage := 'A recuperacao dos dados iniciais falhou (codigo ' +
      IntToStr(ResultCode) + '). O marcador pendente foi preservado e a inicializacao segue bloqueada.';
    RaiseException(RecoveryFailureMessage);
  end;
  RecoveryRequired := False;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and RecoveryRequired then
    RecoverPendingInitialData;
end;

function GetCustomSetupExitCode: Integer;
begin
  Result := 0;
  if RecoveryFailed then
    Result := 1;
end;
