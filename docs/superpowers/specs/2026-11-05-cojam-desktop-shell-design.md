# 合拍 Cojam：可选桌面窗口插件设计

日期：2026-11-05
状态：设计已确认，待写实现计划

## 1. 背景

现在双击 `Cojam.exe` 或 `start.bat`，后台服务起来后打开的是浏览器标签页。用户希望像普通桌面应用一样，打开后直接看到一个独立的应用窗口。

同时，这个能力必须是**可选择的插件**：

- 不装插件的用户继续用浏览器 / PWA，体验完全不变；
- 核心 `Cojam.exe` 仍然零第三方依赖；
- 桌面窗口不能成为核心的一部分，也不能逼所有用户下载 WebView 运行时。

Windows 10/11 自带 Microsoft Edge WebView2 运行时（或随 Edge 安装），因此桌面插件用系统 WebView2，不打包 Chromium。

## 2. 目标

- 双击 `CojamDesktop.exe`，直接出现标题为「合拍 Cojam」的原生窗口，不弹终端，不弹浏览器。
- 桌面插件和 `Cojam.exe` 放在同一个文件夹就能用；找不到核心时给出清楚的原生提示。
- 关闭窗口后，合拍后台按现有规则退出；浏览器标签页还开着时继续运行。
- 可以同时开多个桌面窗口；关掉其中一个不影响其他窗口或浏览器页面。
- 有任务在运行时，关窗口前先确认一次。
- 桌面插件打不开时，自动退回浏览器，不让用户卡死。
- 核心和浏览器版的构建、安装包、依赖保持不变。

## 3. 非目标（v1 明确不做）

- 安装器集成、开始菜单快捷方式、自动更新
- 托盘图标、单实例限制、窗口位置记忆
- 自定义标题栏、原生菜单、深色标题栏
- 把后台服务嵌进桌面插件进程
- 打包 WebView2 运行时或 Chromium
- macOS / Linux（v1 只做 Windows x64）
- 改 AI 调用、房间协议、局域网或中继逻辑

## 4. 术语

| 术语 | 含义 |
|---|---|
| 核心 / 后台 | `Cojam.exe`，本机 HTTP 服务 + 房间 + AI 调度 |
| 桌面插件 / 壳 | `CojamDesktop.exe`，只负责开窗口和生命周期 |
| owner 页签 | 本机管理员页面在 `OwnerLifecycle` 里的登记项 |
| owner token | 本机管理员令牌，页面 HTML 里注入，只在本机有效 |
| 桌面页签 id | 壳生成的随机 id，窗口页面和壳共用同一个 |

## 5. 形态与边界

```
CojamDesktop.exe（可选，单独下载）
  ├─ 找同目录 Cojam.exe
  ├─ 探测 / 启动后台（无终端、无浏览器）
  ├─ 取 owner token（仅 127.0.0.1）
  ├─ 注册 owner 页签 + 30 秒心跳
  └─ WebView2 原生窗口 ──▶ http://127.0.0.1:<port>/?owner_tab=<id>
```

- 发布物：`CojamDesktop-<版本>-win64.zip`，里面只有 `CojamDesktop.exe` 和一份 `README.txt`。
- 使用方式：把 `CojamDesktop.exe` 放进 `Cojam.exe` 所在文件夹，双击。
- 核心发布包（portable zip / 安装器）不包含桌面插件，不改现有安装流程。
- 桌面插件是 Windows GUI 程序，不弹控制台。

### 5.1 使用说明（最终用户）

**安装**

1. 解压 `CojamDesktop-<版本>-win64.zip`。
2. 把 `CojamDesktop.exe` 复制到 `Cojam.exe` 所在文件夹。
   - 安装版默认在 `%LOCALAPPDATA%\Programs\Cojam`；
   - 便携版就是解压出来的 `Cojam.exe` 旁边。
3. 双击 `CojamDesktop.exe`。

**使用**

- 等几秒出现标题为「合拍 Cojam」的窗口，功能和浏览器版一致。
- 关掉窗口后，如果没有别的合拍页面开着，后台约 6 秒后退出。
- 浏览器页面和桌面窗口可以同时开；关一个不影响另一个。
- 有任务正在运行或排队时，关窗口会先弹一次确认。
- 页面里的「退出合拍」仍然可用，效果和浏览器版一样。

**不装桌面插件**

- 继续双击 `Cojam.exe`、`start.bat` 或 `open.pyw`，还是原来的浏览器 / PWA 用法，不需要改任何设置。

**常见问题**

| 现象 | 处理 |
|---|---|
| 提示找不到 `Cojam.exe` | 两个 exe 没放同一文件夹 |
| 窗口打不开，自动跳浏览器 | 系统缺少 Microsoft Edge WebView2；装好后重开桌面版 |
| 提示端口被占用 | 先退出占用该端口的程序；改过 `ROUNDTABLE_PORT` 的，两边要用同一个值 |
| 后台没启动 | 看 `Cojam.exe` 同目录的 `data\cojam.log`（或 `ROUNDTABLE_LOG_DIR` 指向的目录） |

## 6. 启动与复用

端口取 `ROUNDTABLE_PORT`，没设就是 `8765`。壳和它拉起的核心继承同一份环境变量，所以端口始终一致。

启动顺序：

1. **找核心**
   - 打包后：`CojamDesktop.exe` 同目录的 `Cojam.exe`。
   - 源码运行：仓库根目录的 `roundtable.py`，优先用同目录 `pythonw.exe`，没有就用当前解释器。
   - 都找不到：原生错误框「找不到 Cojam.exe。请把 CojamDesktop.exe 和 Cojam.exe 放在同一个文件夹里。」然后退出。

2. **探测 `GET /api/server/info`**（超时 0.5 秒）
   - 返回 `{"app": "AI Bridge", ...}`：已有合拍后台，直接复用，不再启动第二个。
   - 连接被拒绝：没有后台，进入第 3 步。
   - 有 HTTP 响应但不是合拍（或返回的不是 JSON）：端口被别的程序占用，原生错误框「端口 <port> 被其他程序占用，合拍无法启动。」然后退出。
   - 超时：按端口被占用处理，提示同上，避免和状态不明的进程抢端口。

3. **启动核心**
   - 环境变量：
     - `ROUNDTABLE_NO_BROWSER=1`：不开浏览器；
     - `ROUNDTABLE_OWNER_LIFECYCLE=1`：见第 8 节；
     - 其余环境变量原样继承（包括 `ROUNDTABLE_PORT`、`ROUNDTABLE_LOG_DIR`）。
   - 工作目录：核心所在目录。
   - Windows 创建标志：`CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`，不弹终端。
   - 不设置 stdout/stderr 管道；窗口模式下核心会自己把输出写进 `data/cojam.log`（或 `ROUNDTABLE_LOG_DIR`）。

4. **等就绪**
   - 每 200 毫秒探测一次 `/api/server/info`，最多 30 秒。
   - 出现合拍后台 → 就绪。
   - 子进程退出且仍没有合拍后台 → 原生错误框「合拍后台没有启动成功。日志：<日志路径>」；只清理壳自己拉起、且仍在运行的进程。
   - 子进程已经退出但端口上出现了合拍后台（两个桌面窗口同时启动的竞争）：按复用处理。
   - 记录本次后台是不是壳拉起的；复用的后台不归壳清理。

5. **取 owner token**
   - `GET http://127.0.0.1:<port>/`（Host 为 `127.0.0.1:<port>`，服务端会返回管理员页面）。
   - 从 HTML 的 `<meta name="rt-token" content="...">` 取 token。
   - 取不到：原生错误框「已运行的合拍后台版本不兼容，请先退出旧版再打开桌面版。」然后退出。如果后台是壳刚拉起的，先终止它，避免留下没有页签的后台。
   - token 只放内存，不写日志、不写磁盘。

6. **开窗口**
   - pywebview + 系统 WebView2。
   - 标题：`合拍 Cojam`。
   - 默认尺寸 1280×860，最小 960×640。
   - 地址：`http://127.0.0.1:<port>/?owner_tab=<桌面页签 id>`。
   - 外部链接用系统默认浏览器打开（`OPEN_EXTERNAL_LINKS_IN_BROWSER`）。

## 7. 窗口与关闭

桌面页签 id 用 `secrets.token_urlsafe(24)` 生成，满足现有页签校验 `[A-Za-z0-9_-]{16,80}`。壳和窗口页面共用这一个 id。

关闭流程：

1. `window.events.closing` 触发。
2. 查 `GET /api/rooms`（带 owner token）：任意房间 `running` 非空或 `queued` 大于 0 即视为忙碌。查询失败按不忙碌处理，不阻塞关闭。
3. 忙碌时弹原生确认框：
   「还有任务正在运行或排队。现在关闭窗口，如果这是最后一个合拍页面，任务会中断。确定关闭吗？」
   - 选「否」：取消关闭，窗口保留，心跳继续。
   - 选「是」：继续。
4. 停掉心跳线程。
5. `POST /api/ui/session`，body `{"tab": "<id>", "action": "close", "instance": "<id>"}`，带 `X-RT-Token`。
6. 窗口关闭，壳进程退出。
7. `webview.start()` 正常返回后，再补发一次同样的 `close`（幂等），覆盖页面关闭瞬间迟到的 `open` / `ping` 登记；失败忽略。

关闭请求发完就结束，不等待后台退出。后台由 owner 页签机制在最后一个页签关闭约 6 秒后自行停止。

页面正常关闭时也会发一次同样的 `close`；重复注销是幂等的，不影响结果。

## 8. 后台生命周期

现有规则：本机管理员页面通过 `POST /api/ui/session` 登记 `open` / `ping` / `close`；最后一个页签关闭后等 6 秒退出，页签心跳超过 180 秒视为过期。

桌面插件复用这套规则，只加两处小改动：

### 8.1 核心：允许无浏览器时开启生命周期

`bridge/web.py` 的 `main()` 增加环境开关。生命周期是否创建的完整条件：

```
未设置 ROUNDTABLE_NO_BROWSER=1
或
设置了 ROUNDTABLE_OWNER_LIFECYCLE=1
```

- 满足条件就创建 `OwnerLifecycle`；所以桌面壳同时设 `NO_BROWSER=1` 和 `OWNER_LIFECYCLE=1` 时，既不开浏览器，又有页签生命周期。
- 浏览器是否自动打开仍然只看 `ROUNDTABLE_NO_BROWSER`。
- 局域网监听器继续共用同一个 `OwnerLifecycle` 实例。
- 不设置 `ROUNDTABLE_OWNER_LIFECYCLE` 时，行为与现在完全一致。

### 8.2 页面：接受桌面页签 id

`index.html` 的 owner 页签脚本增加：

- 从 `location.search` 读 `owner_tab`，只有匹配 `[A-Za-z0-9_-]{16,80}` 才采用。
- 采用时，`tab` 和 `instance` 都使用这个 id，不再读写 `sessionStorage` 的 `cojam_owner_tab`。
- 采用时，不注册 `beforeunload` 的忙碌提示（关闭确认由壳负责，避免弹两次）。
- `switchRoom()` 重写地址栏时必须保留 `owner_tab`。现在它把地址改成 `?r=<会议号>`，会把桌面页签 id 丢掉；首次带 `?r=` 加载、切换会议、刷新都会受影响。
- 没有 `owner_tab` 或是非法值时，完全保持现在随机会话、刷新复用、`beforeunload` 提示的行为。

### 8.3 心跳与多窗口

- 壳在开窗口前先发一次 `open`，之后每 30 秒发一次 `ping`；页面加载后也会用同一个 id 发 `open` / `ping`。
- 同一个 id 的重复登记只是刷新时间，不会产生多个页签。
- 页面没加载成功时，壳自己的登记仍能保证关闭窗口后后台可退出。
- 壳的登记 / 心跳是尽力而为：失败只记在内存状态里，不阻塞开窗口，页面自己的登记仍可生效。
- 浏览器标签页、多个桌面窗口都登记为各自独立的页签；最后一个关闭后才退后台。
- 复用的后台如果本来就是无生命周期模式（例如用户手工 `NO_BROWSER=1` 启动），登记和注销都是空操作，关窗口不会杀它。

## 9. 失败兜底

| 情况 | 表现 |
|---|---|
| 找不到 `Cojam.exe` | 原生错误框，提示把两个 exe 放同一目录，退出 |
| 端口被非合拍程序占用 | 原生错误框，退出，不启动核心 |
| 核心启动失败 / 超时 | 原生错误框，附日志路径；清理壳拉起的进程，退出 |
| 已有合拍后台 | 直接复用，关闭桌面窗口只注销自己的页签 |
| 已有后台但取不到 owner token | 原生错误框「已运行的合拍后台版本不兼容，请先退出旧版再打开桌面版。」；若是壳刚拉起的后台，一并终止后退出 |
| `pywebview` 导入失败 | 原生提示「没能打开桌面窗口（可能缺少 Microsoft Edge WebView2）。已改用浏览器打开合拍。」→ 默认浏览器打开同一地址 → 壳退出 |
| 创建窗口 / `webview.start()` 抛错 | 同上，退回浏览器 |
| 用户正常关闭窗口 | 不触发浏览器兜底；注销页签后壳退出 |
| 浏览器也打不开 | 注销壳的页签后退出，避免后台空转 180 秒 |
| 壳被强制结束 | 页签停止心跳，后台按现有规则 180 秒后过期退出；窗口页面若还活着会继续心跳 |
| 用户点页面里的「退出合拍」 | 走现有 `/api/server/exit`，窗口显示已退出页，壳不额外处理 |

原生提示统一用 `ctypes.windll.user32.MessageBoxW`；不引入 Tk 或其他 GUI 依赖。

## 10. 安全

- 壳只和 `127.0.0.1` 通信，不新增监听端口，不向局域网或公网暴露任何东西。
- owner token 只从本机管理员页面读取，只存在内存里，用于 `X-RT-Token` 请求头。
- `owner_tab` 只接受固定格式的随机 id，不参与 HTML 注入，也不带来额外权限。
- 启动核心只用固定路径 + 参数列表，不经过 shell，不拼接用户输入。
- 桌面插件不读取、不改写会议数据；数据仍由核心管理。

## 11. 构建与发布

- 源码目录：`desktop_shell/`
  - `backend.py`：找核心、探测、启动、等就绪、取 token、登记/注销页签、忙碌查询；
  - `main.py`：编排启动流程、pywebview 窗口、关闭确认、浏览器兜底、原生提示。
  - 只依赖标准库；`pywebview` 在真正开窗口时才导入，单元测试不需要安装它。
- 仓库根目录的 `desktop_launcher.py`：唯一入口，只做 `from desktop_shell.main import main`。源码用 `python desktop_launcher.py`，PyInstaller 也打这个文件，避免包内脚本被当作顶层脚本执行时相对导入失效。
- `pyproject.toml` 增加可选依赖：
  ```toml
  [project.optional-dependencies]
  desktop = ["pywebview>=5.3"]
  ```
  核心 `dependencies` 仍为空。
- `build_desktop.ps1`：
  - 检查 PyInstaller 与 pywebview，缺了给出安装命令；
  - PyInstaller `--onefile --windowed --name CojamDesktop --icon roundtable.ico --collect-all webview desktop_launcher.py`；
  - 输出 `dist\CojamDesktop.exe`；
  - 第一个实现任务先做最小打包冒烟：在真实 Windows 上确认 WebView2Loader 等资源齐全、窗口能开；缺什么就在脚本里补 `--add-binary`。
- 发布：`packaging/build_release.py` 在 `dist/CojamDesktop.exe` 存在时，额外生成 `dist/CojamDesktop-<版本>-win64.zip`，放入 `CojamDesktop.exe` 和 `packaging/desktop-readme.txt`（zip 内改名 `README.txt`）。没有构建桌面插件时跳过，不影响现有 `Cojam-<版本>-win64.zip`。
  - `README.txt` 写明：和 `Cojam.exe` 放同一文件夹；需要 WebView2（Win10/11 一般已有）；打不开会自动用浏览器。
  - 核心安装包、portable zip、官网下载结构不变。
- 文档：`README.md` 增加一小段可选桌面插件说明（安装位置、启动、关闭行为、浏览器兜底），其余安装说明不动；`packaging/desktop-readme.txt` 写完整的使用说明和常见问题。

## 12. 测试

自动测试：

1. **核心开关**
   - `ROUNDTABLE_NO_BROWSER=1` 且 `ROUNDTABLE_OWNER_LIFECYCLE=1`：打开页签再关闭，后台在宽限时间后退出。
   - `ROUNDTABLE_NO_BROWSER=1` 且没有生命周期开关：同样操作后后台不退出。
   - 用随机 `ROUNDTABLE_PORT` 起真实子进程，避免抢 8765；`ROUNDTABLE_LAN_PORT=0` 避免局域网端口冲突，`ROUNDTABLE_LOG_DIR` 指向临时目录。
2. **页面 owner_tab**
   - 合法 id：`tab` / `instance` 都采用它，不发 `beforeunload`。
   - 非法 id：忽略，退回随机会话。
   - 没有参数：行为与现在一致。
   - `switchRoom()` 改写地址后，`owner_tab` 仍在。
3. **壳单元测试**（假 HTTP 服务 + 临时目录）
   - 找核心：同目录 exe、源码模式、缺失三种情况。
   - 探测：合拍 / 空闲 / 非合拍占用 / 超时。
   - 取 token：能从 HTML 解析，缺失时报错。
   - 页签：`open` / `ping` / `close` 的 body 与 `X-RT-Token` 正确；重复 close 幂等。
   - 忙碌判断：`running` 非空、`queued` 大于 0、接口失败三种情况。
   - 关闭确认：选「否」不注销，选「是」注销（对话框注入假实现）。
4. **兜底**
   - 注入假的 `webview` 模块：导入失败、`start()` 抛错时调用浏览器打开，且不注销页签。
   - 浏览器也失败时注销页签。

手工验收：

- 源码模式：`python desktop_launcher.py` 打开原生窗口。
- 打包模式：`CojamDesktop.exe` + `Cojam.exe` 同目录，双击出现窗口，无终端、无浏览器。
- 关闭窗口后约 6 秒后台退出（任务管理器确认）。
- 先开浏览器页面，再开桌面窗口：关桌面窗口后台继续；关浏览器页面后退出。
- 两个桌面窗口：关一个另一个继续。
- 有任务运行时关窗口，确认框出现；选「否」窗口保留。
- 已有后台时启动桌面插件：直接复用，不出现第二个进程。

## 13. 兼容与约束

- Windows 10/11 x64；需要系统 WebView2 运行时。
- 桌面插件与核心同版本发布；`API_REVISION` 不变，未新增或修改 HTTP API。
- 核心 `pip install` 依赖仍为 0；桌面插件是可选构建产物。
- 桌面窗口和浏览器页面可以同时存在，生命周期按 owner 页签统一计算。
- 不依赖 Tailscale、中继或任何网络配置。

## 14. 交付物

- 设计文档：本文件。
- 实现计划：`docs/superpowers/plans/2026-11-05-cojam-desktop-shell.md`。
- 代码：`desktop_shell/`、`desktop_launcher.py`、`bridge/web.py` 生命周期开关、`index.html` 页签参数、`build_desktop.ps1`、`packaging/build_release.py`、`packaging/desktop-readme.txt`、测试。
- 文档：`README.md` 的可选桌面插件说明。
- 产物：`dist/CojamDesktop.exe`、`CojamDesktop-<版本>-win64.zip`。
