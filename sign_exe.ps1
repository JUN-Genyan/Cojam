param([switch]$RequireSignature)
$ErrorActionPreference = 'Stop'
$exePath = Join-Path $PSScriptRoot 'dist/Cojam.exe'
$thumbprint = $env:COJAM_SIGN_CERT_THUMBPRINT
if (-not $thumbprint) {
    if ($RequireSignature -or $env:COJAM_REQUIRE_SIGNATURE -eq '1') { throw '缺少代码签名证书。请设置 COJAM_SIGN_CERT_THUMBPRINT，或为本地测试去掉签名要求。' }
    Write-Warning '本次生成的是未签名测试版；公开发行前请配置受信任的代码签名证书。'
    exit 0
}
$thumbprint = ($thumbprint -replace '\s','').ToUpperInvariant()
if ($thumbprint -notmatch '^[0-9A-F]{40}$') { throw 'COJAM_SIGN_CERT_THUMBPRINT 格式不正确。' }
$cert = Get-Item -LiteralPath "Cert:/CurrentUser/My/$thumbprint" -ErrorAction Stop
if (-not $cert.HasPrivateKey -or $cert.NotAfter -le (Get-Date) -or $cert.NotBefore -gt (Get-Date)) { throw '证书缺少私钥，或尚未生效 / 已过期。' }
if (-not ($cert.EnhancedKeyUsageList.ObjectId.Value -contains '1.3.6.1.5.5.7.3.3')) { throw '该证书不具备代码签名用途。' }
$tool = Get-Command signtool.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
if (-not $tool) { $tool = Get-ChildItem "${env:ProgramFiles(x86)}/Windows Kits/10/bin" -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue | Where-Object FullName -Match '[\\/]x64[\\/]' | Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName }
if (-not $tool) { throw '找不到 Windows SDK 的 signtool.exe。' }
$timestamp = if ($env:COJAM_SIGN_TIMESTAMP_URL) { $env:COJAM_SIGN_TIMESTAMP_URL } else { 'https://timestamp.digicert.com' }
& $tool sign /sha1 $thumbprint /s My /fd SHA256 /tr $timestamp /td SHA256 /d '合拍 Cojam' $exePath
if ($LASTEXITCODE -ne 0) { throw "代码签名或时间戳失败：$LASTEXITCODE" }
& $tool verify /pa /all /tw $exePath
if ($LASTEXITCODE -ne 0) { throw "签名验证失败：$LASTEXITCODE" }
$signature=Get-AuthenticodeSignature -LiteralPath $exePath
if ($signature.Status -ne 'Valid' -or -not $signature.TimeStamperCertificate) { throw '签名未受当前 Windows 信任或缺少时间戳。' }
Write-Host ('已签名并验证：' + $signature.SignerCertificate.Subject) -ForegroundColor Green
