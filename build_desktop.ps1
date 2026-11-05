# 打成可选的桌面窗口插件（需要先装 pywebview：pip install "pywebview>=5.3"）
#   .\build_desktop.ps1
# 产物在 dist\CojamDesktop.exe；和 dist\Cojam.exe 放在同一文件夹后双击。
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python -c "import webview" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "没装 pywebview。先跑：pip install `"pywebview>=5.3`"" -ForegroundColor Yellow
    exit 1
}

$args = @(
    "--noconfirm", "--clean", "--onefile", "--windowed",
    "--name", "CojamDesktop",
    "--icon", "roundtable.ico",
    "--collect-all", "webview",
    "desktop_launcher.py"
)
Write-Host "pyinstaller $($args -join ' ')" -ForegroundColor Cyan
pyinstaller @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "好了：dist\CojamDesktop.exe" -ForegroundColor Green
