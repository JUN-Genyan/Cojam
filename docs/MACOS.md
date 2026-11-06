# macOS 桌面版

需要 macOS 和 Python 3.11 或更新版本。源码运行：

```sh
python3 -m pip install 'pywebview>=5.3'
python3 desktop_launcher.py
```

制作独立应用包须在目标架构的 Mac 上运行：

```sh
python3 -m pip install 'pywebview>=5.3' pyinstaller
bash packaging/build_macos.sh
```

产物位于 `dist/Cojam-<版本>-macOS-<架构>.zip`。分别在 Apple Silicon 和 Intel Mac 上构建对应版本。解压后将 Cojam.app 移至应用程序文件夹。构建脚本只进行临时签名，没有开发者签名或 Apple 公证，系统可能阻止打开；正式分发需要开发者签名和公证。

桌面版的数据和日志默认保存在 `~/Library/Application Support/Cojam/`；可通过 `ROUNDTABLE_LOG_DIR` 覆盖。本机 AI 命令仍需单独安装并登录。Finder 启动时会补充 `~/.local/bin`、`/opt/homebrew/bin` 和 `/usr/local/bin` 到 PATH。

macOS 包的构建和实际运行需要在 Mac 上验证。本仓库的 Windows 更新安装器不适用于 macOS，请手动替换应用包，保留上述数据目录。
