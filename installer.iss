; ============================================================================
; FTP Sync Server — Inno Setup Installer
; ============================================================================
; Требует Inno Setup 6.x: https://jrsoftware.org/isdl.php
;
; Установщик:
;   1. Устанавливает ftp_sync_server.exe и ftp_sync_watchdog.exe
;   2. Показывает страницы для ввода настроек FTP
;   3. Генерирует config.json из введённых данных
;   4. Добавляет watchdog в автозагрузку (реестр)
;   5. Запускает сервер после установки
; ============================================================================

[Setup]
AppId={{BGD-FTP-SYNC-2024}}
AppName=FTP Sync Server
AppVersion=1.0.0
AppPublisher=BGD
DefaultDirName={commonpf}\BGD_FTP_SYNC
DefaultGroupName=FTP Sync Server
OutputDir=installer_output
OutputBaseFilename=FTP_Sync_Setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
SetupIconFile=
UninstallDisplayName=FTP Sync Server
WizardStyle=modern
DisableProgramGroupPage=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Files]
Source: "dist\ftp_sync_server.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\ftp_sync_watchdog.exe"; DestDir: "{app}"; Flags: ignoreversion
; config.json создаётся из [Code], не копируется

[Icons]
Name: "{group}\FTP Sync Server"; Filename: "{app}\ftp_sync_server.exe"
Name: "{group}\Удалить FTP Sync Server"; Filename: "{uninstallexe}"

[Registry]
; Watchdog в автозагрузку
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "FTP Sync Watchdog"; \
    ValueData: """{app}\ftp_sync_watchdog.exe"""; \
    Flags: uninsdeletevalue

[Run]
; Запуск после установки
Filename: "{app}\ftp_sync_watchdog.exe"; \
    Description: "Запустить FTP Sync Server"; \
    Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "taskkill"; Parameters: "/F /IM ftp_sync_server.exe"; \
    Flags: runhidden; RunOnceId: "KillServer"
Filename: "taskkill"; Parameters: "/F /IM ftp_sync_watchdog.exe"; \
    Flags: runhidden; RunOnceId: "KillWatchdog"

; ============================================================================
; Кастомные страницы конфигурации (Pascal Script)
; ============================================================================

[Code]

var
  // Страница 1: Удалённый FTP
  PageRemote: TWizardPage;
  EditRemoteHost: TNewEdit;
  EditRemotePort: TNewEdit;
  EditRemoteUser: TNewEdit;
  EditRemotePass: TNewEdit;
  EditRemoteRoot: TNewEdit;
  CheckRemoteTLS: TNewCheckBox;

  // Страница 2: Локальный FTP
  PageLocal: TWizardPage;
  EditLocalPort: TNewEdit;
  EditLocalUser: TNewEdit;
  EditLocalPass: TNewEdit;
  EditLocalRoot: TNewEdit;

  // Страница 3: Синхронизация
  PageSync: TWizardPage;
  EditSyncInterval: TNewEdit;
  EditMirrorDays: TNewEdit;
  CheckSyncOnUpload: TNewCheckBox;
  CheckDeleteOrphans: TNewCheckBox;


// ─── Хелперы для создания контролов ─────────────────────────────────────────

function CreateLabel(Page: TWizardPage; Top: Integer; Caption: String): TNewStaticText;
begin
  Result := TNewStaticText.Create(Page);
  Result.Parent := Page.Surface;
  Result.Top := Top;
  Result.Caption := Caption;
  Result.Font.Style := [fsBold];
end;

function CreateEdit(Page: TWizardPage; Top: Integer; DefaultText: String; IsPassword: Boolean): TNewEdit;
begin
  Result := TNewEdit.Create(Page);
  Result.Parent := Page.Surface;
  Result.Top := Top;
  Result.Width := Page.SurfaceWidth;
  Result.Text := DefaultText;
  if IsPassword then
    Result.PasswordChar := '*';
end;

function CreateCheck(Page: TWizardPage; Top: Integer; Caption: String; Checked: Boolean): TNewCheckBox;
begin
  Result := TNewCheckBox.Create(Page);
  Result.Parent := Page.Surface;
  Result.Top := Top;
  Result.Width := Page.SurfaceWidth;
  Result.Caption := Caption;
  Result.Checked := Checked;
end;


// ─── Инициализация страниц мастера ──────────────────────────────────────────

procedure InitializeWizard;
begin
  // ═══ Страница 1: Удалённый FTP-сервер (сервер выгрузки) ═══
  PageRemote := CreateCustomPage(
    wpSelectDir,
    'Удалённый FTP-сервер',
    'Укажите параметры FTP-сервера, на который будут выгружаться файлы.'
  );

  CreateLabel(PageRemote, 0, 'Хост (адрес сервера):');
  EditRemoteHost := CreateEdit(PageRemote, 18, 'ftp.example.com', False);

  CreateLabel(PageRemote, 48, 'Порт:');
  EditRemotePort := CreateEdit(PageRemote, 66, '21', False);

  CreateLabel(PageRemote, 96, 'Имя пользователя:');
  EditRemoteUser := CreateEdit(PageRemote, 114, '', False);

  CreateLabel(PageRemote, 144, 'Пароль:');
  EditRemotePass := CreateEdit(PageRemote, 162, '', True);

  CreateLabel(PageRemote, 192, 'Корневая папка на сервере:');
  EditRemoteRoot := CreateEdit(PageRemote, 210, '/', False);

  CheckRemoteTLS := CreateCheck(PageRemote, 244, 'Использовать TLS (FTPS)', False);


  // ═══ Страница 2: Локальный FTP-сервер ═══
  PageLocal := CreateCustomPage(
    PageRemote.ID,
    'Локальный FTP-сервер',
    'Настройки локального FTP-сервера для приёма файлов.'
  );

  CreateLabel(PageLocal, 0, 'Порт локального FTP:');
  EditLocalPort := CreateEdit(PageLocal, 18, '2121', False);

  CreateLabel(PageLocal, 48, 'Имя пользователя:');
  EditLocalUser := CreateEdit(PageLocal, 66, 'localuser', False);

  CreateLabel(PageLocal, 96, 'Пароль:');
  EditLocalPass := CreateEdit(PageLocal, 114, 'localpass', True);

  CreateLabel(PageLocal, 148, 'Папка для файлов (FTP root):');
  EditLocalRoot := CreateEdit(PageLocal, 166, 'C:\BGD_FTP_DATA', False);


  // ═══ Страница 3: Параметры синхронизации ═══
  PageSync := CreateCustomPage(
    PageLocal.ID,
    'Параметры синхронизации',
    'Интервалы и режимы синхронизации файлов.'
  );

  CreateLabel(PageSync, 0, 'Интервал периодической синхронизации (секунды):');
  EditSyncInterval := CreateEdit(PageSync, 18, '30', False);

  CreateLabel(PageSync, 52, 'Интервал mirror-синхронизации (дни):');
  EditMirrorDays := CreateEdit(PageSync, 70, '3', False);

  CheckSyncOnUpload := CreateCheck(PageSync, 110, 'Мгновенная синхронизация при загрузке файла', True);
  CheckDeleteOrphans := CreateCheck(PageSync, 138, 'Удалять с сервера файлы, которых нет локально (mirror)', True);
end;


// ─── Валидация ──────────────────────────────────────────────────────────────

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;

  if CurPageID = PageRemote.ID then
  begin
    if Trim(EditRemoteHost.Text) = '' then
    begin
      MsgBox('Укажите адрес удалённого FTP-сервера.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if Trim(EditRemoteUser.Text) = '' then
    begin
      MsgBox('Укажите имя пользователя удалённого FTP.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;

  if CurPageID = PageLocal.ID then
  begin
    if Trim(EditLocalPort.Text) = '' then
    begin
      MsgBox('Укажите порт локального FTP-сервера.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
end;


// ─── Замена бэкслешей на прямые слеши для JSON ─────────────────────────────

function EscapePath(S: String): String;
begin
  StringChangeEx(S, '\', '/', True);
  Result := S;
end;

// ─── Экранирование строки для JSON ─────────────────────────────────────────

function JsonEscape(S: String): String;
begin
  StringChangeEx(S, '\', '\\', True);
  StringChangeEx(S, '"', '\"', True);
  Result := S;
end;

// ─── Запись config.json после установки ─────────────────────────────────────

procedure CurStepChanged(CurStep: TSetupStep);
var
  ConfigFile: String;
  S: String;
  TLSVal, SyncOnUploadVal, DeleteOrphansVal: String;
  FTPRootPath: String;
begin
  if CurStep = ssPostInstall then
  begin
    ConfigFile := ExpandConstant('{app}\config.json');

    // Булевые значения
    if CheckRemoteTLS.Checked then TLSVal := 'true' else TLSVal := 'false';
    if CheckSyncOnUpload.Checked then SyncOnUploadVal := 'true' else SyncOnUploadVal := 'false';
    if CheckDeleteOrphans.Checked then DeleteOrphansVal := 'true' else DeleteOrphansVal := 'false';

    // Путь к FTP root — прямые слеши
    FTPRootPath := EscapePath(Trim(EditLocalRoot.Text));

    // Формируем JSON
    S := '{' + #13#10;
    S := S + '    "local_ftp": {' + #13#10;
    S := S + '        "host": "0.0.0.0",' + #13#10;
    S := S + '        "port": ' + Trim(EditLocalPort.Text) + ',' + #13#10;
    S := S + '        "user": "' + JsonEscape(Trim(EditLocalUser.Text)) + '",' + #13#10;
    S := S + '        "password": "' + JsonEscape(Trim(EditLocalPass.Text)) + '",' + #13#10;
    S := S + '        "root": "' + FTPRootPath + '",' + #13#10;
    S := S + '        "permissions": "elradfmw"' + #13#10;
    S := S + '    },' + #13#10;
    S := S + '    "remote_ftp": {' + #13#10;
    S := S + '        "host": "' + JsonEscape(Trim(EditRemoteHost.Text)) + '",' + #13#10;
    S := S + '        "port": ' + Trim(EditRemotePort.Text) + ',' + #13#10;
    S := S + '        "user": "' + JsonEscape(Trim(EditRemoteUser.Text)) + '",' + #13#10;
    S := S + '        "password": "' + JsonEscape(Trim(EditRemotePass.Text)) + '",' + #13#10;
    S := S + '        "root": "' + JsonEscape(Trim(EditRemoteRoot.Text)) + '",' + #13#10;
    S := S + '        "tls": ' + TLSVal + #13#10;
    S := S + '    },' + #13#10;
    S := S + '    "sync": {' + #13#10;
    S := S + '        "interval_seconds": ' + Trim(EditSyncInterval.Text) + ',' + #13#10;
    S := S + '        "on_upload": ' + SyncOnUploadVal + #13#10;
    S := S + '    },' + #13#10;
    S := S + '    "mirror": {' + #13#10;
    S := S + '        "interval_days": ' + Trim(EditMirrorDays.Text) + ',' + #13#10;
    S := S + '        "delete_orphans": ' + DeleteOrphansVal + #13#10;
    S := S + '    }' + #13#10;
    S := S + '}' + #13#10;

    // Записываем файл
    SaveStringToFile(ConfigFile, S, False);

    // Создаём папку для FTP-данных
    ForceDirectories(Trim(EditLocalRoot.Text));
  end;
end;


// ─── Завершение процессов перед установкой (для обновлений) ──────────────────

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Exec('taskkill', '/F /IM ftp_sync_server.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill', '/F /IM ftp_sync_watchdog.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  // Даём время процессам завершиться
  Sleep(1000);
  Result := '';
end;


// ─── Завершение процессов при деинсталляции ─────────────────────────────────

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    Exec('taskkill', '/F /IM ftp_sync_server.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exec('taskkill', '/F /IM ftp_sync_watchdog.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(1000);
  end;
end;
