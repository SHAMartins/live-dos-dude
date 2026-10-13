; Instalador do Live dos Dude (Inno Setup 6). Compile pelo empacotar.py, que passa a versão:
;   ISCC.exe /DVersaoApp=<versão> instalador.iss
;
; Instala só para o usuário atual (sem administrador), em %LOCALAPPDATA%\Programs\LiveDosDude.
; O desinstalador aparece em Configurações > Aplicativos.

#ifndef VersaoApp
  #define VersaoApp "0.0.0"
#endif
#define NomeApp "Live dos Dude"
#define ExeApp "LiveDosDude.exe"
#define MutexApp "LiveDosDudeCliente"

[Setup]
; Nunca mude o AppId: é ele que faz uma versão nova atualizar a instalada (em vez de instalar duas).
AppId={{70E60CAD-F2AA-4F12-BB88-7361B81DD2CC}
AppName={#NomeApp}
AppVersion={#VersaoApp}
AppVerName={#NomeApp} {#VersaoApp}
AppPublisher={#NomeApp}
VersionInfoVersion={#VersaoApp}
VersionInfoDescription=Instalador do {#NomeApp}
PrivilegesRequired=lowest
DefaultDirName={autopf}\LiveDosDude
DisableProgramGroupPage=yes
DisableDirPage=auto
OutputDir=..\dist
OutputBaseFilename=LiveDosDude-{#VersaoApp}-instalador
SetupIconFile=icone.ico
UninstallDisplayIcon={app}\{#ExeApp}
UninstallDisplayName={#NomeApp}
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
ShowLanguageDialog=no
; Sem AppMutex nem CloseApplications de propósito: o app fica na bandeja, sem janela, então a caixa padrão
; ("feche todas as instâncias") deixava a pessoa presa, até no modo silencioso. Quem fecha o app é o [Code]
; abaixo (InitializeSetup / InitializeUninstall), pelo mutex que o app cria em cliente/__main__.py.
CloseApplications=no
RestartApplications=no

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "atalhodesktop"; Description: "Criar atalho na área de trabalho"; GroupDescription: "Atalhos:"
Name: "iniciarcomwindows"; Description: "Iniciar com o Windows (abre direto na bandeja)"; GroupDescription: "Inicialização:"

[InstallDelete]
; Numa atualização, apaga as bibliotecas da versão anterior para não sobrar arquivo velho misturado.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\LiveDosDude\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#NomeApp}"; Filename: "{app}\{#ExeApp}"
Name: "{autodesktop}\{#NomeApp}"; Filename: "{app}\{#ExeApp}"; Tasks: atalhodesktop

[Registry]
; Mesmo valor que a caixa "Iniciar com o Windows" do app grava (cliente/sistema.py).
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "LiveDosDude"; ValueData: """{app}\{#ExeApp}"""; Tasks: iniciarcomwindows; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#ExeApp}"; Description: "Abrir o {#NomeApp} agora"; Flags: nowait postinstall skipifsilent

[Code]
function AppAberto: Boolean;
begin
  Result := CheckForMutexes('{#MutexApp}');
end;

{ Fecha o app (que costuma estar escondido na bandeja) e espera o mutex sumir, até ~5 s. }
procedure FecharApp;
var
  Codigo, Tentativa: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#ExeApp}', '', SW_HIDE, ewWaitUntilTerminated, Codigo);
  for Tentativa := 1 to 20 do
  begin
    if not AppAberto then
      Exit;
    Sleep(250);
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if AppAberto then
  begin
    if not WizardSilent then
      Result := MsgBox('O Live dos Dude está aberto (na bandeja do Windows).' + #13#10 + #13#10 +
                       'Para continuar ele será fechado. Se estiver transmitindo, a transmissão termina.',
                       mbConfirmation, MB_OKCANCEL) = IDOK;
    if Result then
      FecharApp;
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  if AppAberto then
  begin
    if not UninstallSilent then
      Result := MsgBox('O Live dos Dude está aberto (na bandeja do Windows).' + #13#10 + #13#10 +
                       'Para desinstalar ele será fechado. Se estiver transmitindo, a transmissão termina.',
                       mbConfirmation, MB_OKCANCEL) = IDOK;
    if Result then
      FecharApp;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Codigo: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    { Também quando foi ligado depois, pela caixa do app (e não pela opção do instalador). }
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'LiveDosDude');

    if not UninstallSilent and
       (MsgBox('Apagar também as configurações e o pareamento deste PC?' + #13#10 + #13#10 +
               'Se pretende reinstalar, escolha Não: assim não precisa usar o /parear de novo.',
               mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES) then
    begin
      { O token fica no Gerenciador de Credenciais do Windows: quem sabe apagá-lo é o próprio app. }
      Exec(ExpandConstant('{app}\{#ExeApp}'), '--limpar-dados', '', SW_HIDE, ewWaitUntilTerminated, Codigo);
      DelTree(ExpandConstant('{userappdata}\LiveDosDude'), True, True, True);
    end;
  end;
end;
