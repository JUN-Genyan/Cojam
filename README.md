# 合拍 Cojam

一个按项目组织会话的本地多 AI 与多人协作应用。你可以在同一个会话里与多个 AI（Claude Code、Codex、Gemini CLI、OpenCode、Cursor、Antigravity 等）以及受邀伙伴实时讨论、互相 `@` 分工，并在安全护栏下协作修改同一个项目。

[新手使用说明](docs/QUICKSTART.md) · [联系人与外部接入](docs/CONTACTS.md) · [Agent 设置](docs/AGENT_SETTINGS.md) · [执行端与模型](docs/AGENT_EXECUTION.md) · [跨设备成员](docs/MEMBERS.md) · [接入新 AI](docs/PROVIDERS.md) · [macOS 说明](docs/MACOS.md)

## 核心特性

- **按项目组织、多会话并行**
  项目绑定本地工作文件夹，下设多场独立会话；每场会话拥有独立的聊天记录、消息排队、AI 锁与原生会话，支持多场会话同时运行互不阻塞。
- **全渠道 AI 接入与统一管理**
  - **本机 CLI / ACP 自动发现与直连**：支持 `claude`、`codex`、`gemini`、`opencode`、`crush`、`aider`、`qwen`、`cursor`、`antigravity`（`agy`）等程序，内置发现目录表与配置预置；应用内提供交互式表单，可直接修改程序位置、启动参数、默认模型与思考强度（effort）。
  - **桌面端 MCP 待命与原生会话接管**：支持将 MCP 配置一键写入桌面 IDE / 客户端（含 `mcp-stdio` 桥接），通过 `/cojam_standby` 进入待命接单循环；支持在 CLI 与桌面端之间经过验证后交接并续接同一条原生会话。
  - **跨设备 Harness 与 HTTP 驱动**：另一台 Windows 或 Mac 设备可运行轻量「成员连接端」，选择本机 Harness 与工作文件夹加入同一会话；自研服务可通过 `http` 驱动或带认证密钥的外部 HTTP / MCP 接口参与协作。
- **多人协作与多设备访问**
  - **邀请其他人**：生成绑定身份的网页邀请链接或二维码（支持同局域网直连或 Tailscale / HTTPS 跨网访问），按会话精细配置读取、发言、唤起其他 AI、编辑看板、分享文件与允许干活等权限。
  - **我的设备**：生成个人设备接入链接，在手机、平板或另一台电脑上以屋主身份访问完整界面，并支持安装为桌面或移动端 PWA 应用。
- **讨论模式与干活模式**
  - **讨论模式（默认）**：AI 运行在只读约束下，适合需求讨论、方案评审与代码阅读。
  - **干活模式**：开放文件编辑、命令执行、联网及会话勾选的 MCP / 技能；基于 Git 私有索引拍取快照，自动追踪每一步 AI 改过的文件，支持预览并按步安全撤回。
- **跨会议文件护栏**
  同一文件同一时间只允许一个 AI 修改（或由用户手动锁定），通过 Claude / Codex `PreToolUse` 钩子跨会话实时拦截冲突写入。
- **开箱即用的会议室工具**
  内置会议目标与看板（公告 + 待办）、剪贴板粘贴与拖拽上传附件、在线大图浏览与画笔/箭头/框选/文字/裁剪标注、AI 发送本机文件与 Codex 自带生图自动挂载、整条/片段引用回复、实时中间输出与上下文占比显示、单 AI 独立压缩（`/compact`）、一键交给终端与收回、一键整理并落盘。

## 快速开始

### 环境要求

- **操作系统**：Windows 10/11（提供独立桌面安装包与便携版）或 macOS（参见 [macOS 使用说明](docs/MACOS.md)）。
- **运行环境**：Windows 桌面安装包与便携版内置运行环境，无需另装 Python；源码运行需 Python 3.11+（核心服务仅依赖标准库；源码运行桌面窗口需安装 `pywebview>=5.3`）。
- **AI 工具**：本机至少安装并登录一个受支持的 AI 程序（如 [Claude Code](https://docs.claude.com/en/docs/claude-code) 的 `claude` 或 [Codex CLI](https://github.com/openai/codex) 的 `codex`），或通过成员连接端 / HTTP 接口接入远端 AI。
- **Git（推荐）**：工作文件夹为 Git 仓库时，干活模式可记录每步改动并支持按步撤回；非 Git 文件夹也可正常使用，仅不支持按步撤回。

### 启动方式

- **桌面安装版 / 便携版（Windows）**：运行 Windows 安装程序后从桌面或开始菜单打开「合拍 Cojam」；便携桌面版需确保 `Cojam.exe` 与核心程序处于同目录。左下角「更新」可检查版本并下载安装程序。
- **源码后台启动（Windows）**：双击 `start.bat` 或 `open.pyw`，将在后台启动服务并自动打开 `http://127.0.0.1:8765`（关闭最后一个本机管理页面后自动退出）。
- **源码终端运行**：

```sh
python roundtable.py
# 或
python -m bridge
```

桌面窗口模式（源码运行）：

```sh
python desktop_launcher.py
```

### 三步上手

1. **创建项目与会话**：在左侧「项目与会话」栏点击新建项目并指定工作文件夹，再在该项目下新建会话。
2. **添加并配置成员**：展开左下角「联系人」，点击「＋ 接入」（或在「管理」中扫描本机 Agent），完成程序验证后将其邀请进入会话。
3. **开始开会**：在底部输入框发言（不写 `@` 默认唤起当前会话全部 AI，写 `@名称` 只叫指定成员）；通过右上角「⋯」可切换讨论/干活模式、调整成员权限、切换模型/effort 或打开右侧「会议详情与看板」抽屉。

## 接入指南

| 接入方式 | 操作入口 | 说明文档 |
|---|---|---|
| 本机 CLI / ACP | 联系人栏「管理 → 扫描」或「＋ 接入 → 连接 AI → 本机 Agent」 | [本机 Agent 发现](docs/DISCOVERY.md) · [Agent 设置](docs/AGENT_SETTINGS.md) |
| 桌面端 MCP 待命 | 在 Agent 设置中「保存并应用 MCP 配置」，并在软件内运行 `/cojam_standby` | [执行端与模型](docs/AGENT_EXECUTION.md) |
| 其他设备上的 Harness | 「＋ 接入 → 连接 AI → 其他设备上的 Harness」，下载成员连接端与配置 | [统一成员与跨设备协作](docs/MEMBERS.md) |
| 自定义 HTTP / JSON 配置 | 「＋ 接入 → 连接 AI → HTTP 服务」或编写 `data/agents.d/<id>.json` | [接入新 AI 指南](docs/PROVIDERS.md) |
| 其他用户与我的设备 | 「＋ 接入 → 邀请其他人」或「＋ 接入 → 我的设备」 | [联系人与外部接入](docs/CONTACTS.md) |

常用命令行工具：

```sh
# 列出当前识别到的 Agent 及其启用状态
python roundtable.py agents

# 脱离会议室验证 Agent 是否接通，并实测一次调用
python -m bridge.doctor
python -m bridge.doctor gemini

# 一键将会议室 MCP 服务写入外部工具配置（修改前自动备份）
python roundtable.py install cursor
python roundtable.py install claude
python roundtable.py install codex
python roundtable.py install antigravity
```

## 会议室功能说明

### 界面分区

- **左侧全局导航**：包含会话搜索、全局新建会话、项目与会话树、会话管理（归档与回收站）、默认收起的联系人区（含管理与接入入口），以及更新与退出按钮。
- **中央聊天区**：展示当前会话消息流、实时中间状态、输入栏、附件与快捷 `@` 栏，右下角提供「我的发言」上下跳转与目录按钮。
- **右上角会议工具（「⋯」）与详情抽屉**：统一收纳当前会话的模式切换、成员与权限、整理并落盘、会议详情与看板抽屉开关，以及参会 Agent 的模型 / effort 与执行端设置。

### 消息路由与并发调度

- **触发规则**：不写 `@` 时唤起当前会话所有有权限的 AI（引用单个 AI 消息且未 `@` 他人时只回复该 AI）；写 `@名称` 时只叫对应成员；AI 之间互相 `@` 会继续对话，直到 `@用户` 或不再 `@` 其他 AI。
- **并发与串行队列**：
  1. 用户一次发言同时叫多个 AI 时，各 AI **并行**启动；
  2. AI 回复中 `@` 其他 AI 触发的调用进入该会话的**串行队列**；
  3. 同一 AI 在同一会话内始终**串行**（单把锁 + 增量递送未读消息，不重复发送其自身已读内容）；
  4. 状态栏支持对单个 AI 或全部运行中 AI 点击「停」强行打断进程树并清空其待跑队列。

### 讨论模式、干活模式与按步撤回

- **模式隔离**：讨论模式下 Claude 使用受限工具集（`Read,Grep,Glob` + Bridge 只读/发言工具），Codex 强制 `readOnly` 沙箱，且不加载额外勾选的外部 MCP；干活模式下开放完整读写、命令与会话勾选的 MCP / 技能，受会话成员权限「允许干活」约束。
- **按步追踪与撤回**：干活模式下，每次调用编辑前会使用私有 Git 索引（`data/snapshot-<hash>.index`，不污染工作区 `git status`）保存快照并记录 AI 编辑工具涉及的文件；回复结束后支持预览并按步撤回（写入前校验文件 SHA-256 与跨会议冲突）。
- **整理并落盘**：可通过会议工具将讨论结论落盘到项目文档并提交推送（若工作区存在 `.claude/skills/wrapup/SKILL.md` 则优先按技能执行）。

### 文件护栏

- **自动与显式占用**：AI 使用编辑工具修改文件时会自动占用该文件至本轮结束；跨轮次连续修改可通过内置 MCP 的 `claim_files` / `release_files` 显式锁定；用户也可在界面「正在改的文件」中添加或强行释放锁定。
- **钩子拦截**：Claude 通过 `--settings` 挂载 HTTP `PreToolUse` 钩子请求 `/api/guard/claude`，Codex 通过 `bridge/hook_codex.py` 解析补丁路径并请求 `/api/guard/codex`，发现冲突时实时拒绝写入并提示原因。

### 模型、工具与会话接力

- **会议级模型与执行端**：每个会话可独立选择各 Agent 的模型与思考强度（支持刷新模型索引、展示「已选择 / 已接受 / 实际回报」状态），并按会话勾选需要开启的 MCP、插件与技能。
- **上下文压缩**：点击 Agent 状态卡或输入 `/compact`（支持 `/compact claude` 等指定成员）可单独对指定 AI 压缩上下文。
- **交给终端与桌面接管**：支持将当前会话的原生 CLI 会话 ID「交给终端」继续交互，完成后点「收回」并自动补看期间会议消息；也支持验证后「由桌面接管同一原生会话」，并可在释放后交回 CLI。

### 富媒体与交互体验

- **图片与文件**：支持输入栏 `Ctrl+V` 粘贴截图/文件与页面拖拽上传（单文件 25MB 内）；AI 在回复中输出 `【发送】文件路径`、本地 Markdown 图片，或 Codex 触发自带生图时，会议室会自动挂载为可预览/下载的附件。
- **看图与标注**：点击图片可在当前页打开大图浏览器，内置画笔、箭头、框选、文字、裁剪工具，标注后可一键放回待发输入框或保存到本地。
- **复制与引用**：每条消息均支持整条复制/引用，选中消息内部分文字后会显示片段操作条（或通过右键菜单）进行精准复制与片段引用。

## 配置与数据存储

### 常用环境变量

| 变量名 | 说明 | 默认值 |
|---|---|---|
| `ROUNDTABLE_PORT` | 本机 Web 服务监听端口 | `8765` |
| `ROUNDTABLE_LAN_PORT` | 局域网访客 / 设备访问监听端口 | `8766` |
| `ROUNDTABLE_TAILSCALE_PORT` | Tailscale 入口监听端口 | `8767` |
| `ROUNDTABLE_LOG_DIR` | 数据与日志目录路径 | 程序所在目录的 `data/`（macOS 桌面打包为 `~/Library/Application Support/Cojam/`） |
| `ROUNDTABLE_DEFAULT_DIR` | 新建项目/会话的默认工作文件夹 | 当前用户主目录 |
| `ROUNDTABLE_NO_BROWSER` | 设为 `1` 时启动后不自动弹出浏览器 | 未设置 |
| `ROUNDTABLE_CODEX` | 设为 `exec` 时退回每轮独立 `codex exec` 模式 | `appserver` |
| `ROUNDTABLE_AGENTS` | 额外加载的 Agent 配置文件或目录路径（可覆盖同 ID 配置） | 未设置 |
| `ROUNDTABLE_NO_PRESETS` | 设为 `1` 时不加载内置 Agent 预置 | 未设置 |

### 数据目录（`data/`）

运行产生的会话、配置与凭据默认保存在 `data/`（不进入 Git 追踪）：

- `projects.json`：项目列表与工作文件夹映射
- `meetings/<目录>/`：单场会话数据（`meeting.json` 元数据与状态、`log.md` 可读记录、`log.jsonl` 消息流、`files/` 附件）
- `trash/`：已删除待恢复或清空的会话目录
- `agents.d/` 与 `agents.json`：自定义与修改后的 Agent 配置
- `connections.json` / `connection_settings.json` / `contact_keys.json`：连接状态、心跳与超时参数、外部密钥哈希摘要
- `claims.json` / `codex_hook_trust.json`：文件护栏显式占用记录与 Codex 钩子信任哈希
- `cojam.log` / `bridge.log`：运行与轮转日志

## 代码结构与开发

| 路径 | 职责 |
|---|---|
| `roundtable.py` | 主启动入口与命令行子命令分发（`install`、`join`、`doctor`、`agents`、`member`、`mcp-stdio` 等） |
| `desktop_launcher.py`、`desktop_shell/` | 基于 `pywebview` 的桌面窗口外壳与原生目录/文件选择器 |
| `bridge/web.py`、`bridge/server_runtime.py` | HTTP / 局域网多入口服务、API 路由、身份鉴权与生命周期管理 |
| `bridge/room.py`、`bridge/projects.py` | `Room` / `Rooms` 会话调度、消息队列、看板、归档回收站与项目管理 |
| `bridge/agents.py`、`bridge/drivers.py`、`bridge/acp.py` | Claude / Codex 专有驱动、通用 CLI / HTTP 驱动与 ACP 常驻协议驱动 |
| `bridge/execution.py`、`bridge/native_session.py`、`bridge/model_catalog.py` | 执行端交接、原生会话校验与模型/effort 索引刷新 |
| `bridge/discovery.py`、`bridge/catalog/agents.json`、`bridge/presets/` | 本机 CLI / ACP / 桌面端自动发现目录与内置 Agent 预置 |
| `bridge/registry.py`、`bridge/agent_settings.py`、`bridge/install.py` | Agent 注册表、应用内配置编辑与外部工具 MCP 自动写入 |
| `bridge/access.py`、`bridge/invites.py`、`bridge/devices.py` | 访问控制、网页邀请、个人设备配对与外部密钥管理 |
| `bridge/members.py`、`bridge/member.py`、`bridge/member_ui.py`、`bridge/member_package.py` | 统一成员协议、自动待命接单、跨设备成员连接端界面与打包 |
| `bridge/connections.py`、`bridge/conn_settings.py` | 连接探测、心跳与超时统一配置 |
| `bridge/mcp.py`、`bridge/mcp_stdio.py` | 会议室内置 Streamable HTTP MCP 服务与 stdio 转发桥 |
| `bridge/guard.py`、`bridge/hook_codex.py`、`bridge/workspace.py` | 跨会议文件护栏、Codex 钩子脚本与 Git 私有快照/按步撤回 |
| `bridge/updater.py` | 应用内版本检查与安装包下载校验 |
| `index.html`、`static/` | 前端页面、样式、脚本及 PWA 清单/图标（`manifest.webmanifest`、`sw.js`） |
| `tests/` | 单元测试与浏览器回归套件 |
| `packaging/`、`build_exe.ps1`、`build_desktop.ps1` | Windows EXE / 安装包及 macOS 应用包构建脚本 |

### 运行测试与构建

```sh
# 运行隔离测试套件（使用临时目录与模拟 Agent，不触碰真实会话数据）
python tests/run_all.py
```

构建 Windows 独立可执行文件与桌面窗口组件（需安装 `pyinstaller` 与 `pywebview`）：

```powershell
.\build_exe.ps1
.\build_desktop.ps1
```

## 文档索引

- **快速上手**：[新手使用说明](docs/QUICKSTART.md) · [macOS 桌面版说明](docs/MACOS.md)
- **接入与成员**：[联系人、项目与外部接入](docs/CONTACTS.md) · [统一成员与跨设备协作](docs/MEMBERS.md) · [本机 Agent 发现](docs/DISCOVERY.md) · [接入新 AI 指南](docs/PROVIDERS.md)
- **配置与执行**：[应用内 Agent 设置](docs/AGENT_SETTINGS.md) · [执行端切换与模型管理](docs/AGENT_EXECUTION.md)
- **界面与架构**：[界面与消息交互](docs/UI_INTERACTIONS.md) · [多设备同步边界](docs/MULTI_DEVICE.md)
- **打包与发布**：[Windows 安装程序](docs/INSTALLER.md) · [应用内更新机制](docs/UPDATES.md) · [Windows 代码签名](docs/SIGNING.md)

## 许可证

本项目基于 MIT 许可证开源，详见 [LICENSE](LICENSE)。
