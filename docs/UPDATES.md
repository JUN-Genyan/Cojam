# 应用内更新

侧栏「更新」会检查官网 `update.json` 的稳定版本；发现新版本时可以在应用中下载 Windows 桌面安装程序。下载到运行合拍的电脑的 Downloads 文件夹，验证文件大小与 SHA-256 后显示路径；程序不会自动运行或安装它。远程设备不能触发主机下载。更新检查失败只显示错误，不影响本地会话。

首次从旧解压版升级时，先关闭合拍，再手动运行安装程序，并在安装向导中选择旧程序文件夹，才能继续使用该文件夹下的 data。安装器卸载时保留 data。安装到其他文件夹不会自动迁移原会话数据。

当前安装程序未签名，哈希检查用于检测下载文件与版本清单不一致，不能代替受信任的代码签名。Windows 或浏览器仍可能提示扫描。

## 发布新版本

版本号必须同时更新 `bridge/version.py`、`pyproject.toml`、`static/connections.js`、`static/sw.js` 和 `packaging/cojam-desktop.iss`。用 `build_exe.ps1`、`build_desktop.ps1` 构建核心和桌面入口，再编译安装器；验证安装、卸载和数据保留后，运行 `python packaging/make_update_manifest.py --notes "更新内容"`，将安装包及生成的 `website/update.json` 发布到官网，并核对公开下载文件哈希。只有安装包公开且清单匹配后，用户才会收到可靠的更新提示。
