# 2026-11-07 桌面端打开即"未响应"排查与修复

## 现象

已安装的桌面版(`D:\Cojam\Cojam\Cojam.exe`,2.6.3 之前构建)双击后窗口短暂出现即"未响应"(白影 Ghost 窗口),后台 `CojamCore.exe` 正常运行、端口 8765 正常监听。初步怀疑 pywebview。

## 根因(已用 py-spy 实锤)

桌面壳旧版 `DesktopApi` 把 pywebview 模块和窗口对象存成**公开属性**:

```python
self.webview = webview   # 公开属性
self.window = None       # 之后赋值为 Window 实例,仍是公开属性
```

pywebview 6.x 在页面加载时会递归枚举 js_api 对象的每个公开属性来生成 JS 桥接(`webview/util.py` 的 `inject_pywebview.get_functions`,对类和带 `__module__` 的非可调用对象无限下钻)。`api.window`(Window 实例 → events 容器 → .NET WinForms BrowserForm 对象图)导致枚举跑不完;期间持有 `window._expose_lock`,GUI STA 线程等锁 → 窗口立即"未响应"。

py-spy 抓到的卡死栈(安装版旧 exe):

```
Thread "Thread-1 (generate_js_object)":
    get_functions (util.py:196)   ← getattr
    get_functions (util.py:206)   ← 递归
    generate_js_object (util.py:231)  ← 持有 _expose_lock
```

不是 pywebview 的 bug,是我们把大对象暴露为 js_api 公开属性触发了它的递归机制。

## 修复

`desktop_shell/main.py`:`DesktopApi` 只保留公开方法 `choose_workdir`,实现对象全部私有化(`_webview` / `_window`)。回归测试 `tests/test_desktop_shell.py::test_only_folder_picker_is_public_to_js_bridge` 断言 `dir(api)` 的公开名单只有 `choose_workdir`,防止复发。

## 验证记录(2026-11-07)

| 验证项 | 旧安装版 | 修复后 |
|---|---|---|
| 窗口出现 | 出现即挂死 | 11-12 秒出现 |
| 响应监测 | `Responding: False` + Ghost 幽灵窗 | 40 秒 20/20 次 responsive |
| py-spy | `get_functions` 递归不退出 | `generate_js_object` 线程正常结束 |

本机已重新构建并静默重装(`Cojam-Setup-2.6.3.exe` → `D:\Cojam\Cojam`),安装文件哈希与 `dist/CojamDesktop.exe`、`dist/Cojam.exe` 一致,实测响应正常。

## 遗留事项

- **`dist/release-2.6.3/artifact.zip`(今天 10:36 的 CI 产物)是修复前的构建,不能用**;2.6.3 发布流程需在修复提交进 `codex/release-2-6-3` 分支后重跑,否则官网发布的安装包仍会挂。
- 修复代码与回归测试目前在工作区未提交(遵循"不动 git"的指示),发布前需随 2.6.3 改动一起提交。
- 排查用的 Inno Setup 6.7.4 已装到当前用户目录(`%LOCALAPPDATA%\Programs\Inno`),ISCC 可直接使用;该安装包源文件 `.test-tmp/innosetup-6.7.4.exe` 未签名,来源需自行核对。
- 排查副产物:`pip install py-spy` 已装入全局环境。

## 排查方法备忘

- PyInstaller onefile 有父子两进程,`Get-Process` 的 `MainWindowTitle`/`Responding` 只看得到 bootloader 的(恒为空),必须定位子进程或按窗口标题枚举。
- `ctypes` `SendMessageTimeoutW(hwnd, WM_NULL, ..., SMTO_ABORTIFHUNG)` 是客观判断窗口挂起的手段;出现 class 为 `Ghost` 的同名窗口即 DWM 判定挂起。
- py-spy 可直接 dump 冻结 exe 的 Python 栈,是这类"窗口未响应"问题的首选工具。
