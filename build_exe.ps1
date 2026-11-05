# 打成 Windows 单文件 exe（需要先装 PyInstaller：pip install pyinstaller）
#   .\build_exe.ps1
# 产物在 dist\Cojam.exe。双击它和双击 start.bat 一样：开会话室、打开浏览器。
# 注意：exe 里自带 Python，但 claude / codex / gemini 这些命令还得用户自己装在 PATH 里。
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python make_icon.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m bridge.member_package
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
    Write-Host "没装 PyInstaller。先跑：pip install pyinstaller" -ForegroundColor Yellow
    exit 1
}

$args = @(
    "--noconfirm", "--clean", "--onefile", "--windowed",
    "--name", "Cojam",
    "--icon", "roundtable.ico",
    "--add-data", "index.html;.",
    "--add-data", "static;static",
    "--add-data", "bridge\presets;bridge\presets",
    "--hidden-import", "bridge.web",
    "roundtable.py"
)
Write-Host "pyinstaller $($args -join ' ')" -ForegroundColor Cyan
pyinstaller @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& (Join-Path $PSScriptRoot "sign_exe.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "好了：dist\Cojam.exe" -ForegroundColor Green
