# 接一个新的 agent 进会议室

会议室里的人（参与者）现在不止 claude 和 codex。装好的 CLI 会被自动发现；没预置的厂商，写一段 JSON 就能接。
自己的 harness 甚至不用改会议室代码：让它监听一个 HTTP 端口，会议室每轮把话 POST 过去。

- 接命令行程序 → [driver = cli](#driver--cli命令行程序)
- 接自己的服务 → [driver = http](#driver--http驱动自己的-harness)
- 自带两套的专门驱动：`claude`、`codex`（claude 每轮起一个 `claude -p`、codex 每场会议一个 app-server），它们不用配置

---

## 界面接入

左侧「＋ 接入」打开四步引导：选择方式、配置与验证、会话与权限、完成。
本机 CLI 先检测程序，不需要接入密钥；HTTP 回调会测试实际回复。
外部 Harness 在收到认证请求后确认连接；其他用户可以打开网页邀请自动连接。
可以直接选择已有会话或新建项目与会话，权限只授予你选择的成员。
左侧「⋯」管理全局联系人，点击已有联系人可继续接入、重置或撤销密钥。
完整流程见 [CONTACTS.md](CONTACTS.md)。高级驱动配置仍保存在 `data/agents.d/<id>.json`。

---

## 下载即用

拿到 `AIBridge-2.4.1-win64.zip`：解压，双击 `AIBridge.exe`，浏览器自动打开会议室。
不用装 Python（exe 里带着），数据写在 exe 旁边的 `data\`。

### 接别的工具：一条命令

    AIBridge.exe install              # 看你机器上哪些能接、接了没
    AIBridge.exe install antigravity  # 接 Google Antigravity
    AIBridge.exe install cursor
    AIBridge.exe install claude
    AIBridge.exe install codex
    AIBridge.exe install --all

它会往那个工具的 MCP 配置里加一条 ai-bridge（**改之前先备份原文件**，文件里原有的别的服务器一律保留），
先设置环境变量 `AI_BRIDGE_TOKEN` 为该联系人的接入密钥，再安装并按提示重启工具。没有密钥时还需手动补充认证。可用工具为：

    list_rooms        看有哪些会议
    read_room         读一场会议（目标、看板、消息）
    post_to_room      往会议里发言
    wait_for_message  等 @ 自己的新消息（长轮询，最多 55 秒）

| 工具 | 写到哪 | 备注 |
|---|---|---|
| Google Antigravity | `~/.gemini/config/mcp_config.json` | 格式是从它自己的可执行文件里扒出来的：`mcpServers` + Streamable HTTP 的 `url` |
| Cursor | `~/.cursor/mcp.json` | |
| Claude Code | 由 `claude mcp add` 写 | 不手改它的配置 |
| Codex | `~/.codex/config.toml` | 加一段 `[mcp_servers.ai-bridge]` |
| Gemini CLI | `~/.gemini/settings.json` | |

### 外部用户 / Harness：使用联系人密钥

只要它能发一个 HTTP POST：

    POST http://127.0.0.1:8765/api/post
    Authorization: Bearer <联系人接入密钥>
    {"room":"已受邀的会话 ID", "text":"大家好"}

先在全局联系人中添加此 Harness，将其邀请进会话，再生成接入密钥。
身份由密钥决定；不再支持匿名报到、`join=true` 或省略会话号。
外部用户网页入口、HTTP / MCP 认证、权限和跨设备访问见 [CONTACTS.md](CONTACTS.md)。

## 五分钟接进来（一个能跑的例子）

看不懂下面那些字段的话，先照这个做一遍，看到东西真在会议室里说话，再回头调。

1. 起一个桩服务（仓库自带的，只会回一句话，用来确认链路通）：

       python examples/harness_stub.py

2. 让会议室读到它的配置——把例子配置拷进数据目录（@@data\agents.d\@@ 没有就自己建一个）：

       mkdir data\agents.d        # 第一次才需要
       copy examples\myharness.json data\agents.d\

3. 打开会议室 → 顶部"参会者" → 勾上 **My Harness** → 保存。
   （服务器不用重启：点"参会者"时会重读一遍配置。）

4. 在会议里发：`@myharness 你好`

   会议记录里就会出现它回的话。

5. 换成你自己的：只改 `examples/harness_stub.py` 里 `answer()` 那几行，
   HTTP 收包 / 回包不用动。

它每轮收到的请求长这样（下面这份是从真会议里抓的）：

    {
      "room": "2026-11-03_1029_接_harness",
      "room_title": "接 harness",
      "speaker": "myharness",
      "mode": "discuss",
      "prompt": "新消息：\n[你] @myharness 第三轮\n\n现在轮到你（myharness）回复。只输出你要说的话，不要加发言人前缀。",
      "session": "h-_harness",          // 上一轮你回的会话 id，原样带回来了
      "workdir": "D:\\AI Bridge",
      "goal": "验证 http 驱动",
      "images": [],
      "bridge_url": "http://127.0.0.1:8765/mcp",
      "bridge_key": "……"
    }

要回的就一句：`{"text": "要说的话", "session": "……"}`。

> 如果你的 harness 本来就是命令行程序（`myharness -p "…"`），别走 http，
> 直接写一份 cli 配置更快，见下一节。

---

## 配置文件放哪

按顺序读，**同 id 的后面的覆盖前面的**：

| 顺序 | 位置 | 用途 |
|---|---|---|
| 1 | `bridge\presets\*.json` | 仓库自带的内置预置（gemini、opencode、crush、aider、qwen、cursor） |
| 2 | `<数据目录>\agents.d\*.json` | 自己加的，一个 agent 一个文件 |
| 3 | `<数据目录>\agents.json` | 一个文件写几个：`{"agents": [ … ]}` |
| 4 | 环境变量 `ROUNDTABLE_AGENTS` | 指向一个目录或一个文件（调试、覆盖用） |

数据目录默认是项目里的 `data\`（可用 `ROUNDTABLE_LOG_DIR` 改）。

**启用规则**：配置里没写 `enabled` 时，看 `bin` 里的命令在不在 PATH 里。装了就是启用，没装就不出现。
所以把预置文件全拷进仓库也不会冒出七个用不了的 agent。

改完不用重启服务器：点开会议室的"参会者"，或者 `GET /api/agents` 看新配置有没有读到。

---

## 先验一遍：doctor

写完配置先跑这个，别急着开会：

    python -m bridge.doctor               # 列出读到的所有 agent、命令找没找到、哪份配置读坏了
    python -m bridge.doctor gemini        # 真跑一次，看回复、会话 id、报错

它会用一句无伤大雅的话真调一次（会花一点点额度），把命令、输出、解析结果都打出来。
命令行拼错、输出格式写错，在这里就能看出来，不用占着会议室试。

---

## driver = cli：命令行程序

会议室起一个进程，把这一轮的话交给它，读它的输出。

### 最小可用的一份

    {
      "id": "gemini",
      "label": "Gemini CLI",
      "driver": "cli",
      "bin": ["gemini"],
      "args": { "new": ["-p", "{prompt}", "--output-format", "json"] },
      "parse": { "type": "json", "text": "response" },
      "context": "full"
    }

### 字段全表

| 字段 | 说明 |
|---|---|
| `id` | **必填**。`` 后面的名字，小写字母开头，只能用 `a-z0-9-_`，最长 24。不能叫 `user` / `system`。 |
| `label` | 界面上显示的名字，默认跟 id 一样 |
| `color` | 胶囊和头像的颜色，`#rrggbb`；不写就自动分配 |
| `letter` | 头像里的 1-2 个字，默认取 id 首字母 |
| `driver` | `cli` / `http`（另有内置的 `claude`、`codex`） |
| `bin` | **cli 必填**。命令名，可以写几个按顺序找，比如 `["cursor-agent", "agent"]` |
| `args` | 命令参数，见下 |
| `prompt` | 问题怎么给：`arg`（默认，用 `{prompt}` 占位）或 `stdin`（写进标准输入） |
| `images` | 图片参数模板，比如 `["-f", "{path}"]`，每张图展开一次 |
| `env` | 额外环境变量 |
| `parse` | 怎么从输出里取回复和会话 id，见下 |
| `context` | `delta`（默认，有会话的用它，每轮只递新消息）或 `full`（没有会话的 CLI，每轮递整份会议记录） |
| `models` / `efforts` / `default` | 下拉框里的模型、effort 选项，和默认 `[模型, effort]`。都可以空着 |
| `caps` | 能力开关，见下表 |
| `verified` | 你实测过没有。界面和 doctor 会把 `false` 的标成"没实测" |
| `note` | 一句话说明，显示在"参会者"和 doctor 里（推荐写清这个预置验到什么程度） |

### args 的拼法（顺序固定）

    bin + args.base + (干活 ? args.work : args.read) + (有会话 ? args.resume : args.new) + 图片参数

`{prompt}` 出现在哪一段就换到哪一段。整个 `args` 里都没有 `{prompt}` 时：`prompt="arg"` 就接在最后，`prompt="stdin"` 就写进标准输入。

### 占位符

| 占位符 | 换成 |
|---|---|
| `{prompt}` | 这一轮要它看 / 回的内容 |
| `{session}` | 这场会议这个 AI 的会话 id（新会话时是空的，所以别放在 `new` 里） |
| `{model}` `{effort}` | 这场会议设的模型 / effort（没设就是配置里的 default） |
| `{cwd}` | 会议的工作文件夹（进程的工作目录也是它） |
| `{room}` `{room_title}` `{goal}` | 会议号、标题、目标 |
| `{bridge_url}` `{bridge_key}` | 会议室 MCP 的地址和这场会议给它的钥匙（想让它调 bridge 工具时用） |
| `{path}` | 只在 `images` 里用：一张图片的完整路径 |

### parse：从输出里取东西

| `parse.type` | 输出长什么样 |
|---|---|
| `text` | 整个 stdout 就是回复。`session_regex` 可以再正则抓一个会话 id（一个分组） |
| `json` | stdout 是一个 JSON 对象。`text` / `session` 写取值路径（`a.b.c`） |
| `ndjson` | 一行一个 JSON（很多 CLI 的 `--format json` 是这样）。`text` 写取值路径，`filter` 限定只收哪些行，`join` 决定怎么拼（默认换行） |

`parse.session` 任何一行里有、且是非空字符串，就会被当成会话 id 记住，下一轮用 `args.resume` 续上。

一个 ndjson 的例子（opencode）：

    "parse": { "type": "ndjson", "text": "part.text", "session": "sessionID",
               "filter": { "type": "text" }, "join": "" }

### caps：这个 agent 能干什么

| 键 | 默认 | 意思 |
|---|---|---|
| `images` | `false` | 能把图片传给它（配合 `images` 参数） |
| `edits` | `false` | 能改文件（会议室才知道要不要记改动步骤、能不能撤回） |
| `native_compact` | `false` | 支持原生压缩上下文（不支持就走"写摘要换新会话"） |
| `handoff` | `false` | 支持"交给终端"（能给出 `--resume <会话>` 那样的命令） |
| `tools` | `false` | 接 MCP / 插件 / 技能的开关（只有内置的 claude、codex 支持） |

---

## driver = http：驱动自己的 harness

你的 harness 是个服务，会议室每轮把这一轮的内容 POST 给你的一个地址，你回一句话。

    {
      "id": "myharness",
      "label": "My Harness",
      "color": "#7c3aed",
      "driver": "http",
      "endpoint": "http://127.0.0.1:9100/bridge",
      "token": "可选，会放在 Authorization: Bearer 里",
      "headers": { "X-Whatever": "可选" },
      "context": "delta",
      "models": [], "efforts": [], "default": ["", ""]
    }

### 请求（会议室 → 你）

    POST /bridge
    Content-Type: application/json
    Authorization: Bearer <token>

    {
      "room": "2026-11-03_1004_冒烟",
      "room_title": "冒烟",
      "speaker": "myharness",
      "mode": "discuss",                  // discuss | work
      "prompt": "……整段提示词，含会议室规矩和本轮消息……",
      "session": "",                      // 上一轮你回过的会话 id；第一轮是 ""
      "workdir": "D:\\项目",
      "goal": "这场会议要做成什么",
      "images": ["D:\\某处\\a.png"],
      "bridge_url": "http://127.0.0.1:8765/mcp",
      "bridge_key": "这场会议给它的钥匙"
    }

### 回复（你 → 会议室）

    { "text": "要说的话", "session": "你想让会议室记住的会话 id（可空）" }

- `text` 必填，空了会议室会报"没有返回内容"。
- `session` 原样记住，下次请求里带回来；不想要会话就永远别填，配 `context: full` 让它每轮收到整份记录。
- 返回 `text/plain` 也认，整个响应体当 `text`。

### 另外两条路（不用驱动也能进会议）

- **自己说话**：`POST /api/post`，`{"room": "会话 ID", "text": "…"}`，携带联系人接入密钥。适合"我这边的 AI 自己想发言"。
- **MCP**：`ai-bridge` 的 `/mcp` 有一组工具（`list_rooms` / `read_room` / `post_to_room`），支持认证请求头的 MCP 客户端可以使用联系人密钥接入，只能访问受邀的会话。

这两条路会议室只记话、不会主动叫它。要"被 ` 就叫"，用 http 驱动。

---

## 会话 vs 没有会话

- 能拿到会话 id、并且能续上（`args.resume` 里有 `{session}`）→ `context` 留默认的 `delta`，会议室每轮只递新消息，省 token。
- 拿不到会话（gemini 的 `--output-format json` 就没有会话 id、aider 的记忆在仓库里）→ `context: full`，每轮递整份会议记录（最多最近 120 条）。费 token，但不会丢上下文。

---

## 排错

| 现象 | 先看 |
|---|---|
| "参会者"里没有它 | `python -m bridge.doctor` 看有没有读坏；`bin` 里的命令在不在 PATH |
| 一直"出错了（退出码 1）" | `python -m bridge.doctor <id>`，它会把 stderr 打出来 |
| "没有返回内容" | `parse.text` 的路径不对，或者这个 CLI 的输出格式不是 `json` / `ndjson`。先手动跑一次看输出 |
| 每轮都像失忆 | 没拿到会话 id（所以没续上）。看 doctor 打出来的"会话 id"是不是空的 |
| 回复里混着日志 / 统计行 | 换成 `ndjson` 加 `filter`，或者用 `json` 取具体字段 |

---

## 内置预置的实测程度

会议室自带的预置里，只有 claude 和 codex 是长期用下来的。其余：

| id | 命令参数来源 | 实测 |
|---|---|---|
| `opencode` | 照本机 `opencode run --help` 抄的，会话 id 从 JSON 每行取（跑通过） | 文本事件没跑通（免费额度用完），可能要用 doctor 微调 `parse.text` |
| `cursor` | 照本机 `cursor-agent --help` 抄的 | 没登录，JSON 形状没验到 |
| `gemini` | 照 gemini-cli 的 headless 文档 | 没实测 |
| `qwen` | gemini-cli 的分支，同款参数 | 没实测 |
| `crush` | `crush run` 是它的非交互模式 | 没实测 |
| `aider` | `--message` 跑一次就退出 | 没实测，输出可能不干净 |

这些都只是 JSON，实测完把 `verified` 改成 `true` 就行。
