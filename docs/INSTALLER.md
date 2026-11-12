# 合拍 Windows 安装程序

官网的 Windows 下载入口使用 `Cojam-Setup-2.7.2.exe`。安装程序基于 Inno Setup 6.7.4 构建，当前使用的编译器显示“Non-commercial use only”；如果项目将商业分发，需要先核对并满足 Inno Setup 的商业使用许可。

安装程序默认安装到当前用户的 `%LOCALAPPDATA%\Programs\Cojam`，无需管理员权限；创建开始菜单快捷方式，并默认提供桌面快捷方式。运行中的应用需要先退出。应用首次启动时在安装目录旁创建 `data` 文件夹。

卸载器删除安装时登记的程序和快捷方式，不删除运行时生成的 `data`。因此卸载后安装目录可能保留会话数据。旧的便携 ZIP 或源码版若在其他目录保存数据，安装程序不会自动搬迁：需要用户确认原数据目录后再另行迁移，不应覆盖新安装中已有的数据。

当前的安装程序和内部 EXE 均未签名；下载或运行时仍可能出现安全提示。正式签名流程见 `SIGNING.md`。用户明确要求先将未签名安装程序放到官网，页面已说明这一状态。

## 构建与验证

先构建 `dist/Cojam.exe`，然后使用经过签名验证的官方 Inno Setup 编译 `packaging/cojam-desktop.iss`。默认输出 `dist/Cojam-Setup-2.7.2.exe`。编译时定义 `TEST_BUILD` 会生成独立 AppId 的测试安装程序，避免覆盖正常安装登记。

最终安装程序已在项目临时目录中实际完成静默安装、EXE 哈希校验、卸载和数据保留检查，并验证测试用卸载登记已清理。官网发布后下载公开安装程序，其 SHA-256 与本地验证过的文件一致。官网由 `.github/workflows/pages.yml` 从 `website/` 构建并部署到 GitHub Pages；安装包不放进仓库，构建时从同名 Release（如 `v2.7.2`）下载到站点的 `downloads/` 目录。
