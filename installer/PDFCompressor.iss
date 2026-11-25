; ---------------------------------------------------------------------------
;  PDF 压缩器 安装包脚本（Inno Setup 6.5+ / 7.x）
;
;  编译：  powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1
;  手动：  ISCC.exe /DAppVersion=1.0.0 installer\PDFCompressor.iss
;
;  前置条件：先跑 build.ps1 生成本脚本要打包的 dist\PDFCompressor
; ---------------------------------------------------------------------------

#define AppName        "PDFCompressor"
#ifndef AppVersion
  #define AppVersion   "1.0.0"
#endif
#define AppVerName     AppName + " " + AppVersion
#define AppPublisher   "PDFCompressor"
#define AppExeName     "PDFCompressor.exe"
; 升级用的固定标识，发布后不要再改
#define AppId          "{{7C4F2B61-9D3A-4A57-9E8B-2B6F0C1D4E72}"
#define PayloadDir     "..\dist\PDFCompressor"
#define IconFile       "..\assets\icon.ico"
#define InstallerName  AppName + "-" + AppVersion + "-Setup"
#define ReadmeName     "使用说明.md"
#define DisclaimerName "免责声明.md"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppVerName}
AppPublisher={#AppPublisher}
AppComments=PDF 批量压缩：图像降采样、字体子集化、对象流清理，离线运行
AppCopyright=© 2026 PDFCompressor contributors
; 默认装到当前用户目录（不需要管理员、不弹 UAC）；安装向导里可切换到「所有用户」
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=output
OutputBaseFilename={#InstallerName}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppVerName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; 自动按系统语言选择界面语言，不弹语言选择框
ShowLanguageDialog=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#AppVersion}.0
VersionInfoCompany={#AppPublisher}
VersionInfoCopyright=© 2026 PDFCompressor contributors
VersionInfoDescription={#AppVerName} 安装程序
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}

[Languages]
; 语言文件随仓库自带：Inno Setup 新版本的安装包不再附带简体中文
Name: "chinesesimplified"; MessagesFile: "languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; 整个 PyInstaller 产物（PDFCompressor.exe + _internal + 使用说明.md + 免责声明.md）原样安装
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Registry]
; 让「Win+R → PDFCompressor」和命令行直接可用（普通安装写 HKCU，所有用户安装写 HKLM）
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#AppExeName}"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExeName}"; Flags: uninsdeletekey

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\使用说明"; Filename: "{app}\{#ReadmeName}"
Name: "{group}\免责声明"; Filename: "{app}\{#DisclaimerName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[CustomMessages]
chinesesimplified.RemoveSettingsPrompt=是否同时删除 PDF 压缩器的设置（自定义参数、上次选择的目录）？%n%n%1
english.RemoveSettingsPrompt=Also delete the PDFCompressor settings (custom parameters, last folder)?%n%n%1

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  SettingsDir: String;
begin
  // 提示是否一并删除 %APPDATA%\PDFCompressor（静默卸载时保留，避免脚本误删用户数据）
  if CurUninstallStep = usPostUninstall then
  begin
    SettingsDir := ExpandConstant('{userappdata}\PDFCompressor');
    if DirExists(SettingsDir) and not UninstallSilent then
    begin
      // 默认按钮是「否」，回车不会误删
      if MsgBox(FmtMessage(CustomMessage('RemoveSettingsPrompt'), [SettingsDir]),
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(SettingsDir, True, True, True);
    end;
  end;
end;
