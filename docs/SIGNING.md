# Windows 代码签名

合拍使用 Authenticode 对 Windows EXE 签名；需要受信任的代码签名证书、可用的私钥以及 Windows SDK 的 `signtool.exe`。现有机器没有检测到可用证书或 SignTool，当前本地构建因此是**未签名测试版**。不应通过自签名证书声称通过了公开信任验证。

## 获取签名身份

可向受信任的证书颁发机构申请代码签名证书，或在符合地区及身份验证条件时使用 Microsoft Azure Artifact Signing。证书可能收费、需要身份审核，私钥应按提供商要求保管。Azure Artifact Signing 的账户、身份和签名配置需要单独开通；本项目目前的脚本支持 Windows 当前用户证书存储中的证书指纹，云签名须另行接入。

签名和时间戳可以帮助系统验证发布者和文件完整性，但不能保证新的下载不出现 SmartScreen 提示。微软说明见 <https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation>。

## 构建和验证

安装 Windows SDK，确保 SignTool 可用。把代码签名证书放进当前用户的个人证书库并确保私钥可用。在 PowerShell 当前会话设置：

```powershell
$env:COJAM_SIGN_CERT_THUMBPRINT = '证书指纹'
$env:COJAM_REQUIRE_SIGNATURE = '1'
./build_exe.ps1
python packaging/build_release.py
```

`sign_exe.ps1` 使用 SHA-256、RFC 3161 时间戳，随后验证签名、证书链和时间戳；可通过 `COJAM_SIGN_TIMESTAMP_URL` 选择受信任的时间戳服务。设置 `COJAM_REQUIRE_SIGNATURE=1` 后，构建和打包都会拒绝缺失或无效签名。不要把 PFX 文件、私钥、令牌或密码提交进仓库。只有签名完成且验证通过后才发布安装包。

未设置证书时，常规构建会明确标注“未签名测试版”；`./sign_exe.ps1 -RequireSignature` 会失败。PFX 密码不会通过命令行参数传递。

## 关闭行为

正常双击启动后，每个本机管理页面登记自己的身份；关闭最后一个页面约六秒后停止服务和本应用管理的本机任务。刷新页面有宽限期；仍有其他管理标签页打开时不会退出。页面异常终止导致无法通知的情况下，通过心跳超时恢复（最长约三分钟再加宽限期）。运行任务时关闭页面会有浏览器提示；手动点“退出”时会确认中断。无浏览器后台模式 `ROUNDTABLE_NO_BROWSER=1` 保持原有服务模式，可使用管理页面的“退出”按钮停止。

## 2.5.1 本地检查

165 项回归检查通过。隔离 Chromium 实测打开两个标签、关闭其中一个、刷新、关闭最后一个后服务退出；也验证了其他会话有任务时取消退出会保留服务，确认退出会停止服务。旧的 8765 本机后台已在确认无任务后停止，端口不再监听。打包版的成员接入及两个模拟 Harness 协作检查通过。正式签名检查明确拒绝了未签名 EXE；当前 `Cojam-2.5.1-win64-unsigned-test.zip` 仅用于本地测试，官网仍提供旧版。
