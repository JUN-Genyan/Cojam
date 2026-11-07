# Tauri 桌面窗口实验

这是源码开发实验，不是可分发的安装程序。浏览器版和现有桌面版不受影响。

在 Windows 上需要 Python 3.11+、Rust MSVC 工具链、Microsoft C++ Build Tools 和 WebView2。安装 Rust Analyzer 扩展本身不会安装编译器。Tauri 的平台要求见 [官方说明](https://v2.tauri.app/start/prerequisites/)。

在仓库根目录运行：

```powershell
cd experiments/tauri/src-tauri
cargo build --release
cd ../../..
python experiments/tauri/run_experiment.py --shell tauri
```

对照现有桌面窗口（需要安装 `pywebview`）：

```powershell
python experiments/tauri/run_experiment.py --shell pywebview
```

两种模式都使用 `127.0.0.1:18765` 和 `.test-tmp/tauri-experiment-data`，不读取现有会话，也不开放局域网入口。先关闭上一种模式，等待实验后台退出后再启动另一种。已有进程占用实验端口时启动器拒绝运行。

控制台只计量隔离后台就绪时间；这不是窗口可交互时间或内存基准。窗口性能、关闭时的提示、文件选择、外部链接及 macOS 打包仍需实际验证。实验窗口目前没有原生文件夹选择桥接或发布安装器，不应替代正式版。
