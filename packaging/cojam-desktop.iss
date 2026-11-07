#define MyAppName "合拍 Cojam"
#define MyAppVersion "2.6.6"
#ifdef TEST_BUILD
#define MyAppId "{{E49FBE28-CC82-4E08-B5CD-E48A2C894472}"
#define OutputName "Cojam-Setup-2.6.6-test"
#else
#define MyAppId "{{856A26B5-6DDE-40EC-A6B0-2CDE2C62A58E}"
#define OutputName "Cojam-Setup-2.6.6"
#endif
[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Cojam
DefaultDirName={localappdata}\Programs\Cojam
DefaultGroupName=合拍 Cojam
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename={#OutputName}
SetupIconFile=..\roundtable.ico
UninstallDisplayIcon={app}\Cojam.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=yes
RestartApplications=no
[Languages]
Name: "chinese"; MessagesFile: "compiler:Default.isl"
[Messages]
SetupAppTitle=安装
SetupWindowTitle=安装 - %1
UninstallAppFullTitle=卸载 %1
ButtonBack=< 上一步(&B)
ButtonNext=下一步(&N) >
ButtonInstall=安装(&I)
ButtonOK=确定
ButtonBrowse=浏览(&B)…
ButtonWizardBrowse=浏览(&R)…
ButtonCancel=取消
ButtonYes=是(&Y)
ButtonNo=否(&N)
ButtonFinish=完成(&F)
ClickNext=点击“下一步”继续，或点击“取消”退出安装。
WelcomeLabel1=欢迎使用 [name] 安装向导
WelcomeLabel2=此程序将在电脑上安装 [name/ver]。%n%n继续前，请先关闭正在运行的合拍。
WizardSelectDir=选择安装位置
SelectDirDesc=要将 [name] 安装在哪里？
SelectDirLabel3=合拍将安装到以下文件夹。
SelectDirBrowseLabel=点击“下一步”继续，或点击“浏览”选择其他位置。
DiskSpaceGBLabel=至少需要 [gb] GB 可用空间。
DiskSpaceMBLabel=至少需要 [mb] MB 可用空间。
WizardSelectTasks=选择附加任务
SelectTasksDesc=需要执行哪些附加任务？
SelectTasksLabel2=选择安装时需要执行的附加任务，然后点击“下一步”。
WizardReady=准备安装
ReadyLabel1=安装程序准备将 [name] 安装到电脑上。
ReadyLabel2a=点击“安装”继续，或点击“上一步”修改设置。
ReadyLabel2b=点击“安装”继续。
ReadyMemoDir=安装位置：
ReadyMemoGroup=开始菜单文件夹：
ReadyMemoTasks=附加任务：
WizardPreparing=准备安装
WizardInstalling=正在安装
InstallingLabel=正在安装 [name]，请稍候。
FinishedHeadingLabel=已完成 [name] 安装
ClickFinish=点击“完成”退出安装向导。
FinishedLabelNoIcons=已在电脑上安装 [name]。
FinishedLabel=已在电脑上安装 [name]。可以通过快捷方式打开应用。
StatusExtractFiles=正在解压文件……
StatusCreateIcons=正在创建快捷方式……
StatusSavingUninstall=正在保存卸载信息……
StatusRunProgram=正在完成安装……
ConfirmUninstall=确定卸载 %1 吗？会话数据将保留。
UninstallStatusLabel=正在卸载 %1，请稍候。
WizardUninstalling=正在卸载
[CustomMessages]
AdditionalIcons=附加快捷方式：
CreateDesktopIcon=创建桌面快捷方式(&D)
[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce
[Files]
Source: "..\dist\CojamDesktop.exe"; DestDir: "{app}"; DestName: "Cojam.exe"; Flags: ignoreversion
Source: "..\dist\Cojam.exe"; DestDir: "{app}"; DestName: "CojamCore.exe"; Flags: ignoreversion
Source: "connect-member.bat"; DestDir: "{app}"; DestName: "连接其他会议室.bat"; Flags: ignoreversion
Source: "desktop-readme.txt"; DestDir: "{app}"; DestName: "README.txt"; Flags: ignoreversion
[Icons]
Name: "{autoprograms}\合拍 Cojam"; Filename: "{app}\Cojam.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\合拍 Cojam"; Filename: "{app}\Cojam.exe"; WorkingDir: "{app}"; Tasks: desktopicon
[Run]
Filename: "{app}\Cojam.exe"; Description: "打开合拍 Cojam"; Flags: nowait postinstall skipifsilent
