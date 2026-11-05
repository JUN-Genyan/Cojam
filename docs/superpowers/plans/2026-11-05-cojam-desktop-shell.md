# 合拍 Cojam 桌面窗口插件 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付可选的 `CojamDesktop.exe`：双击后在原生窗口里打开合拍，关闭窗口后后台按现有 owner 页签规则退出；没有插件的用户继续用浏览器版。

**Architecture:** 新增独立的 `desktop_shell` 包，只依赖标准库；真正开窗口时才导入 pywebview。核心 `bridge/web.py` 只增加一个环境开关，让无浏览器模式也能创建 `OwnerLifecycle`。页面接受壳生成的 `owner_tab` id，壳与页面共用同一个页签登记。

**Tech Stack:** Python 3.11+、标准库 `urllib`/`subprocess`/`ctypes`、pywebview 6.x（系统 WebView2）、PyInstaller 6.x、Node（前端测试）。

**Spec:** `docs/superpowers/specs/2026-11-05-cojam-desktop-shell-design.md`

## Global Constraints

- 核心 `pyproject.toml` 的 `dependencies` 保持为空；桌面依赖只放 `[project.optional-dependencies] desktop = ["pywebview>=5.3"]`。
- 桌面壳只和 `127.0.0.1` 通信，不新增监听端口。
- owner token 只存在内存，不写日志、不写磁盘。
- 壳启动核心时环境变量固定加 `ROUNDTABLE_NO_BROWSER=1` 和 `ROUNDTABLE_OWNER_LIFECYCLE=1`；不经过 shell 拼接命令。
- 关闭窗口时先查 `/api/rooms`，`running` 非空或 `queued` 大于 0 才弹确认。
- 原生提示用 `ctypes.windll.user32.MessageBoxW`；单元测试注入假实现，不真的弹窗。
- 不改 `API_REVISION`，不新增或修改 HTTP API。
- 所有测试通过 `python tests/run_all.py`；每个测试文件可单独运行。

---

### Task 1: 核心生命周期开关

**Files:**
- Modify: `bridge/web.py:647-678`
- Test: `tests/test_owner_lifecycle.py`

**Interfaces:**
- Produces: `bridge.web.owner_lifecycle_enabled(env=None) -> bool`
  - 未设置 `ROUNDTABLE_NO_BROWSER=1` 返回 True；
  - 设置了 `ROUNDTABLE_NO_BROWSER=1` 且 `ROUNDTABLE_OWNER_LIFECYCLE=1` 返回 True；
  - 其余 False。
- 后续任务依赖：桌面壳设置两个环境变量后，后台会创建 `OwnerLifecycle` 并在最后一个页签关闭约 6 秒后退出。

- [ ] **Step 1: 写失败测试**

在 `tests/test_owner_lifecycle.py` 增加：

```python
class LifecycleSwitch(unittest.TestCase):
    def test_matrix(self):
        from bridge.web import owner_lifecycle_enabled
        self.assertTrue(owner_lifecycle_enabled({}))
        self.assertTrue(owner_lifecycle_enabled({'ROUNDTABLE_OWNER_LIFECYCLE':'1'}))
        self.assertFalse(owner_lifecycle_enabled({'ROUNDTABLE_NO_BROWSER':'1'}))
        self.assertTrue(owner_lifecycle_enabled({'ROUNDTABLE_NO_BROWSER':'1','ROUNDTABLE_OWNER_LIFECYCLE':'1'}))
```

- [ ] **Step 2: 运行确认失败**

Run: `python tests/test_owner_lifecycle.py -v`
Expected: FAIL / ImportError，`owner_lifecycle_enabled` 不存在。

- [ ] **Step 3: 实现开关并接入 `main()`**

在 `bridge/web.py` 模块级加入：

```python
def owner_lifecycle_enabled(env=None):
    env = os.environ if env is None else env
    return env.get("ROUNDTABLE_NO_BROWSER") != "1" or env.get("ROUNDTABLE_OWNER_LIFECYCLE") == "1"
```

把 `main()` 中的两个条件改为：

```python
if owner_lifecycle_enabled():
    from .owner_lifecycle import OwnerLifecycle
    srv.owner_lifecycle = OwnerLifecycle(lambda: stop_application(srv))
    if lan_srv:
        lan_srv.owner_lifecycle = srv.owner_lifecycle
...
if os.environ.get("ROUNDTABLE_NO_BROWSER") != "1":
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
```

浏览器是否打开只看 `ROUNDTABLE_NO_BROWSER`，不要改。

- [ ] **Step 4: 补一条真实子进程测试**

在 `tests/test_owner_lifecycle.py` 增加集成测试：随机端口启动 `roundtable.py`，环境为
`ROUNDTABLE_NO_BROWSER=1`、`ROUNDTABLE_OWNER_LIFECYCLE=1`、临时 `ROUNDTABLE_LOG_DIR`；
从 `/` 取 `rt-token`，POST `open` 再 POST `close`，断言进程在 15 秒内退出。
用 `subprocess.Popen`，测试结束确保 `terminate()`。

- [ ] **Step 5: 运行测试**

Run: `python tests/test_owner_lifecycle.py -v`
Expected: 全部 PASS。

- [ ] **Step 6: 提交**

```bash
git add bridge/web.py tests/test_owner_lifecycle.py
git commit -m "桌面壳：无浏览器时可用页签生命周期"
```

---

### Task 2: 页面接受桌面页签 id

**Files:**
- Modify: `index.html:536-560`, `index.html:1340-1348`
- Test: `tests/test_frontend_runtime.py`

**Interfaces:**
- Produces: 页面内 `ownerTabId(search) -> string`，合法值匹配 `^[A-Za-z0-9_-]{16,80}$`，否则空串。
- 有合法 `owner_tab` 时：`tab` 和 `instance` 都用它；不读写 `sessionStorage`；不注册 `beforeunload`。
- `switchRoom()` 改写 URL 时保留 `owner_tab`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_frontend_runtime.py` 增加 Node VM 测试（`skipUnless(shutil.which('node'))`）：

1. 从 `index.html` 提取 `function ownerTabId(search) { ... }`，断言：
   - 合法 32 位 id 原样返回；
   - 15 位、81 位、带 `/` 或空格的值返回空串；
   - `''` 返回空串。
2. 执行 owner 页签 IIFE（从 `(function(){\n  const instance=` 到对应 `})();`），传桩：
   `crypto.randomUUID`、`performance.getEntriesByType`、`sessionStorage`、`fetch`、`setInterval`、`addEventListener`、`document.getElementById`。
   地址带合法 `owner_tab` 时断言：
   - 第一次 `fetch` body 的 `tab` 与 `instance` 都等于该 id；
   - `sessionStorage.setItem` 未被调用；
   - 没有注册 `beforeunload` 监听。
3. 无参数时断言仍写 `sessionStorage` 并注册 `beforeunload`。
4. 提取 `function switchRoom(id,replace){...}`，桩掉 `RID`、`resetRoomView`、`post`、`api`、`drawRooms`、`poll`、`feed`、`queueJump`、`location.search='?owner_tab=<id>'`，断言 `history.replaceState` 的 URL 同时含 `r=` 和 `owner_tab=`。

- [ ] **Step 2: 运行确认失败**

Run: `python tests/test_frontend_runtime.py -v`
Expected: FAIL，`ownerTabId` 不存在。

- [ ] **Step 3: 实现页面逻辑**

在 owner 页签 IIFE 前加入：

```js
function ownerTabId(search){
  try{
    const id=new URLSearchParams(search).get('owner_tab')||'';
    return /^[A-Za-z0-9_-]{16,80}$/.test(id)?id:'';
  }catch(e){return ''}
}
```

IIFE 改为：

```js
const instance=crypto.randomUUID().replace(/-/g,''), endpoint='/api/ui/session';
const desktop=ownerTabId(location.search);
let tab=desktop||instance;
try{
  if(!desktop){
    const navigation=performance.getEntriesByType('navigation')[0];
    if(navigation&&navigation.type==='reload')tab=sessionStorage.getItem('cojam_owner_tab')||instance;
    sessionStorage.setItem('cojam_owner_tab',tab);
  }
}catch(e){}
```

`beforeunload` 改为：

```js
if(!desktop)addEventListener('beforeunload',event=>{if(!exiting&&hasActiveJobs()){event.preventDefault();event.returnValue=''}});
```

`switchRoom()` 的 URL 改为：

```js
const params=new URLSearchParams(location.search);
params.set('r',id);
const u='?'+params.toString();
```

- [ ] **Step 4: 运行测试**

Run: `python tests/test_frontend_runtime.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add index.html tests/test_frontend_runtime.py
git commit -m "桌面壳：页面接受共享页签 id"
```

---

### Task 3: 桌面壳后端模块

**Files:**
- Create: `desktop_shell/__init__.py`
- Create: `desktop_shell/backend.py`
- Test: `tests/test_desktop_shell.py`

**Interfaces:**
- `find_core(frozen=None, executable=None) -> tuple[list[str], str] | None`
  - 打包：`CojamDesktop.exe` 同目录 `Cojam.exe`；
  - 源码：仓库根 `roundtable.py`，优先同目录 `pythonw.exe`；
  - 找不到返回 `None`。
- `probe(base, timeout=0.5) -> tuple[str, dict | None]`
  - `("cojam", info)`、`("free", None)`、`("foreign", None)`、`("timeout", None)`。
- `spawn_core(command, cwd) -> subprocess.Popen`
  - 复制当前环境，加 `ROUNDTABLE_NO_BROWSER=1`、`ROUNDTABLE_OWNER_LIFECYCLE=1`；
  - Windows 创建标志 `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`；
  - 不设置 stdout/stderr 管道。
- `wait_ready(base, proc=None, timeout=30, interval=0.2) -> dict | None`
- `fetch_token(base, timeout=3) -> str`，失败抛 `RuntimeError`。
- `post_session(base, token, tab, action, instance, timeout=3) -> None`，失败抛异常。
- `busy(base, token, timeout=3) -> bool`，接口失败返回 False。
- `new_tab_id() -> str`，`secrets.token_urlsafe(24)`。
- `TabHeartbeat(base, token, tab, interval=30)`：`start()` 发 `open` 并起守护线程；`stop()` 幂等发 `close`；发送失败只记录 `last_error`/`failures`。
- `stop_process(proc)`：进程还在就 `terminate()`，5 秒后 `kill()`。
- `log_path(core_dir) -> str`：`ROUNDTABLE_LOG_DIR/cojam.log` 或 `<core_dir>/data/cojam.log`。

- [ ] **Step 1: 写失败测试**

`tests/test_desktop_shell.py` 用 `ThreadingHTTPServer` 假服务，覆盖：
- `find_core` 三种情况（打包同目录、源码、缺失）；
- `probe`：合拍 / 空闲端口 / 非 JSON 占用 / 超时（handler `sleep` 超过 timeout）；
- `fetch_token`：能解析 `<meta name="rt-token" content="...">`，缺失抛 `RuntimeError`；
- `post_session`：body 与 `X-RT-Token` 正确；
- `TabHeartbeat(interval=0.02)`：收到 `open`、至少一次 `ping`、`stop()` 后 `close`；重复 `stop()` 不再发；
- `busy`：`running` 非空为 True、`queued>0` 为 True、接口 500 为 False；
- `new_tab_id` 匹配 `[A-Za-z0-9_-]{16,80}`；
- `spawn_core` 用 `sys.executable -c "..."` 验证环境变量与不弹窗标志（Windows 才断言 creationflags）。

- [ ] **Step 2: 运行确认失败**

Run: `python tests/test_desktop_shell.py -v`
Expected: FAIL / ImportError。

- [ ] **Step 3: 实现 `backend.py`**

关键实现：

```python
APP_NAME = "AI Bridge"
TAB_RE = re.compile(r"^[A-Za-z0-9_-]{16,80}$")

def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))

def probe(base, timeout=.5):
    try:
        with _opener().open(base.rstrip("/") + "/api/server/info", timeout=timeout) as r:
            info = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError:
        return "foreign", None
    except urllib.error.URLError as e:
        if isinstance(e.reason, ConnectionRefusedError):
            return "free", None
        if isinstance(e.reason, (TimeoutError, socket.timeout)):
            return "timeout", None
        return "foreign", None
    except (TimeoutError, socket.timeout):
        return "timeout", None
    except (ValueError, OSError):
        return "foreign", None
    if isinstance(info, dict) and info.get("app") == APP_NAME:
        return "cojam", info
    return "foreign", None
```

`post_session` 必须带 `Content-Type: application/json` 和 `X-RT-Token`。
`TabHeartbeat.start()` 先同步发一次 `open`，再起线程；`stop()` 用锁保证只发一次 `close`。
`busy()` 解析 `/api/rooms` 的 `rooms`，逐行判断 `bool(row.get("running")) or bool(row.get("queued"))`。

- [ ] **Step 4: 运行测试**

Run: `python tests/test_desktop_shell.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add desktop_shell/__init__.py desktop_shell/backend.py tests/test_desktop_shell.py
git commit -m "桌面壳：核心探测、启动与页签心跳"
```

---

### Task 4: 启动编排、窗口与兜底

**Files:**
- Create: `desktop_shell/main.py`
- Create: `desktop_launcher.py`
- Test: `tests/test_desktop_shell.py`

**Interfaces:**
- `open_window(url, on_closing, module=None) -> None`：`module` 仅测试注入；标题 `合拍 Cojam`，1280×860，最小 960×640。
- `show_error(text)` / `show_confirm(text) -> bool`：Windows `MessageBoxW`。
- `confirm_close(base, token, show_confirm=...) -> bool`：忙碌才确认。
- `main() -> int`：找不到核心 / 端口被占 / 核心失败 / token 失败返回 2；正常或浏览器兜底返回 0。

- [ ] **Step 1: 写失败测试**

用 `unittest.mock.patch` 覆盖：
- 找不到核心：`show_error` 收到“同一个文件夹”，返回 2，不启动。
- `probe` 返回 `foreign` / `timeout`：提示端口被占，返回 2，不启动。
- `probe` 返回 `free`：`spawn_core` 被调用，`wait_ready` 返回 info，`fetch_token` 返回 token，`open_window` 收到含 `?owner_tab=` 的 URL；`TabHeartbeat.start()` 被调用。
- `wait_ready` 返回 None：调用 `stop_process`，提示日志路径，返回 2。
- `fetch_token` 抛错：清理壳拉起的进程，提示版本不兼容，返回 2。
- `open_window` 抛 `ImportError`：`webbrowser.open` 被调用，heartbeat 不 `stop()`；浏览器也返回 False 时才 `stop()`。
- `confirm_close`：busy=True 且用户选否返回 False；选是返回 True；busy=False 不弹框直接 True。
- `open_window` 用假 webview 模块：`create_window` 的 title/URL/尺寸正确，`events.closing += handler`，`start()` 被调用。

- [ ] **Step 2: 运行确认失败**

Run: `python tests/test_desktop_shell.py -v`
Expected: FAIL，`desktop_shell.main` 不存在。

- [ ] **Step 3: 实现 `main.py` 与入口**

`main()` 的顺序严格按 spec 第 6 节：找核心 → 探测 → 启动 → 等就绪 → 取 token → 开窗口。
探测到 `cojam` 直接复用；`free` 才启动；`foreign`/`timeout` 直接报错退出。
窗口关闭回调：

```python
def on_closing():
    if not confirm_close(base, token):
        return False
    heartbeat.stop()
    return True
```

`open_window` 返回后再次 `heartbeat.stop()`；`open_window` 抛错时走浏览器兜底：

```python
try:
    open_window(url, on_closing)
except Exception:
    try:
        opened = webbrowser.open(url)
    except Exception:
        opened = False
    if not opened:
        heartbeat.stop()
        show_error("没能打开桌面窗口，也没能打开浏览器。")
        return 2
    show_error("没能打开桌面窗口（可能缺少 Microsoft Edge WebView2）。已改用浏览器打开合拍。")
    return 0
heartbeat.stop()
return 0
```

窗口外部链接用系统浏览器：优先设置 `webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER']=True`；
旧版 pywebview 若 `start()` 签名支持 `open_external_links_in_browser`，通过 `inspect.signature` 传入。

`desktop_launcher.py` 只做：

```python
from desktop_shell.main import main
if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行测试**

Run: `python tests/test_desktop_shell.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add desktop_shell/main.py desktop_launcher.py tests/test_desktop_shell.py
git commit -m "桌面壳：原生窗口启动与浏览器兜底"
```

---

### Task 5: 打包、发布与文档

**Files:**
- Modify: `pyproject.toml`
- Create: `build_desktop.ps1`
- Create: `packaging/desktop-readme.txt`
- Modify: `packaging/build_release.py`
- Test: `tests/test_headless.py`（或 `tests/test_desktop_shell.py`）

- [ ] **Step 1: 写静态断言测试**

断言：
- `pyproject.toml` 的 `dependencies = []` 仍在，且 `[project.optional-dependencies]` 含 `desktop = ["pywebview>=5.3"]`；
- `build_desktop.ps1` 含 `--onefile`、`--windowed`、`--collect-all webview`、`desktop_launcher.py`、`--icon roundtable.ico`；
- `packaging/desktop-readme.txt` 说明“和 Cojam.exe 放同一文件夹”、WebView2、打不开自动用浏览器；
- `packaging/build_release.py` 在 `dist/CojamDesktop.exe` 存在时生成 `CojamDesktop-<版本>-win64.zip`，内容为 exe + README。

- [ ] **Step 2: 运行确认失败**

Run: `python tests/test_headless.py -v`
Expected: FAIL。

- [ ] **Step 3: 实现打包**

`pyproject.toml` 增加：

```toml
[project.optional-dependencies]
desktop = ["pywebview>=5.3"]
```

`build_desktop.ps1`：

```powershell
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python -c "import webview" 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "没装 pywebview。先跑：pip install `"pywebview>=5.3`""; exit 1 }
pyinstaller --noconfirm --clean --onefile --windowed --name CojamDesktop --icon roundtable.ico --collect-all webview desktop_launcher.py
if ($LASTEXITCODE -ne 0) { exit 1 }
Write-Host "好了：dist\CojamDesktop.exe" -ForegroundColor Green
```

`packaging/build_release.py` 在现有 zip 之后追加桌面包（不存在 exe 就跳过）。

- [ ] **Step 4: 构建并做端到端冒烟**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_desktop.ps1
```

在 `dist\` 里启动 `CojamDesktop.exe`（随机 `ROUNDTABLE_PORT`、临时 `ROUNDTABLE_LOG_DIR`），
用 PowerShell 等待名为 `CojamDesktop` 的进程出现 `MainWindowTitle = 合拍 Cojam`；
curl `/api/server/info` 确认后台已起；`CloseMainWindow()` 后等待后台退出、端口关闭。
清理临时日志与测试进程。

- [ ] **Step 5: 跑全量测试**

Run: `python tests/run_all.py`
Expected: 全部 PASS。

- [ ] **Step 6: 提交**

```bash
git add pyproject.toml build_desktop.ps1 packaging/desktop-readme.txt packaging/build_release.py tests/test_headless.py
git commit -m "桌面壳：打包脚本与发布包"
```

---

## 手工验收（用户可见）

1. 源码：`python desktop_launcher.py` 打开标题「合拍 Cojam」的原生窗口。
2. 打包：`dist\CojamDesktop.exe` 与 `dist\Cojam.exe` 同目录，双击出现窗口，无终端、无浏览器。
3. 关闭窗口后约 6 秒后台退出（任务管理器确认）。
4. 先开浏览器页面再开桌面窗口：关桌面窗口后台继续；关浏览器页面后退出。
5. 两个桌面窗口：关一个另一个继续。
6. 有任务运行时关窗口出现确认框，选「否」窗口保留。
7. 已运行后台时启动桌面插件：直接复用，不出现第二个 Cojam 进程。

## 风险与边界

- pywebview 6.x 默认 `OPEN_EXTERNAL_LINKS_IN_BROWSER=True`；5.x 需要签名判断兼容。
- PyInstaller `--onefile` 的桌面壳首次启动会解包，可能比核心慢；等待就绪上限 30 秒。
- 打包后核心日志路径在 `dist\data\cojam.log` 或 `ROUNDTABLE_LOG_DIR`；验收前清理 `dist\data`，避免旧日志干扰。
