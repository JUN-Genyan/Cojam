# Harness 自动发现与模型控制

这套功能随 Cojam 源码和构建产物分发，使用运行设备的用户目录、环境变量与系统安装信息。不会带入开发者的安装路径、模型账户或凭证，不会安装或升级任何 Harness。手机网页控制主机上的程序；其他执行设备由成员连接端独立检测和上报。

## 使用流程

「接入 → 连接 AI → 本机 Agent」自动发起后台扫描，已发现的程序可以先使用。「重新检索」读取当前安装状态，无需重启 Cojam。多个真实安装可以选择；卸载的程序从可用列表消失，已有会话和配对配置保留。

选中 Agent 后可查看模型目录、搜索和刷新。在引导里暂存的设置加入会话后生效；点击会话顶部的 Agent 也可修改该会话的设置。仅显示对应模型支持的档位、variant、思考开关或 token 预算。

「已选择」不代表执行端已确认。只有回复带有模型标识时，才显示最近一次执行端报告；目录候选和缓存都标明来源，账户额度与权限以实际调用为准。

## 扫描范围与扩展

- Windows：进程与注册表 PATH、App Paths、有效开始菜单快捷方式、用户命令目录、npm 自定义 prefix、pnpm/Bun、Scoop、WinGet、Python/pipx。只检查已知目录和包入口。
- macOS/Linux：标准路径、Homebrew、用户命令目录、npm 自定义 prefix、pnpm/Bun、nvm、pipx，以及描述中声明的应用内 CLI 路径。
- 符号链接、已知 npm 包装脚本会检查实际目标。没有接口的桌面应用与可调用 CLI 分开显示；不会把一个桌面图标当作已经接通的自动响应端。
- 安装扫描在后台构建新快照后一次替换。外部程序探测共享两个并发名额，每条目录/帮助/版本命令限时 8 秒；模型目录缓存 5 分钟，按安装、配置、账户环境和项目区分。

自定义 CLI 的 `data/agents.d/<id>.json` 可以声明：

```json
{
  "id": "my-agent",
  "driver": "cli",
  "bin": ["my-agent"],
  "discovery": {
    "commands": ["my-agent-cli"],
    "paths": {"darwin": ["~/.local/bin/my-agent"]},
    "packages": ["@vendor/my-agent"],
    "version_command": ["--version"],
    "help_command": ["--help"]
  },
  "catalog_command": ["models", "--json"],
  "args": {
    "model": ["--model", "{model}"],
    "effort": ["--effort", "{effort}"],
    "thinking_budget": ["--thinking-tokens", "{thinking_budget}"],
    "new": ["--print", "{prompt}"]
  },
  "models": ["model-one"],
  "reasoning": {"model-one": {"kind": "effort", "options": ["low", "high"]}}
}
```

需要子命令后参数时设置 `options_position: "after_command"`。目录可返回模型数组或 `{"models":[...]}`，每个模型包含 `id`、可选 `name` 和 `reasoning`。后者的 `kind` 为 `effort`、`variant`、`toggle`、`budget` 或 `none`；预算可声明 `min`、`max`。静态 `models`、`efforts`、`model_efforts` 继续兼容。

## 预置适配与验证边界

| Harness | 目录和参数来源 | 验证覆盖 |
|---|---|---|
| Claude | 现有别名/候选与专用驱动 | 实机版本检测，原有命令回归 |
| Codex | 其本机模型缓存与专用驱动 | 实机版本/缓存读取，原有命令回归 |
| OpenCode | 1.x verbose 目录；2.x 模型 API；按版本使用 variant 参数或 `provider/model#variant` | 2.2.5 实机读取 46 个模型、39 个带 variant 的模型；免费模型短回复和会话 ID 验证成功；新建/续接模拟 CLI 执行 |
| Antigravity | `agy models` 的真实 slug/标签；从已列出的同族档位生成 effort 选项 | 1.5.3 实机帮助和模型表；兼容 stderr 与制表符输出 |
| Qoder CLI | `qodercli`/`qoder`，账户目录与模型参数 | 官方接口形状样本和参数测试，未做已登录实机调用 |
| Cursor Agent | 已有 `cursor-agent`/`agent` 或应用内入口，版本实际提供的参数 | 目录形状样本和参数测试，未做已登录实机调用 |
| Gemini CLI | 本机配置，缺失时官方候选；独立 system settings 覆盖思考参数 | 参数、预算配置隔离与清理的真实模拟 CLI 执行 |
| Qwen Code | modelProviders 配置和明确的提供方路由 | 同名模型跨提供方隔离、开关/参数测试 |
| Aider | CLI 模型候选，已安装模型设置资源/用户 YAML 的 accepts_settings | 输出样本、模型和预算参数测试 |
| Crush | CLI 候选及配置元数据；Cojam 会话独立状态目录 | 输出样本、模型参数位置测试 |
| Devin CLI | 已有 CLI 的 JSON 模型目录；与桌面应用分开 | 官方接口形状样本与参数测试，未做已登录实机调用 |

Gemini 官方候选的思考参数参考 [Google 模型说明](https://ai.google.dev/gemini-api/docs/generate-content/thinking)；候选声明不保证账户授权或将来服务端路由始终不变。模型参数及列表优先跟随执行设备可提供的元数据。只支持换模型而没有额外思考接口的版本，仍保留模型选择。

## API 与远程成员

- `GET /api/agents/models?ai=<id>&room=<可选会话>`：模型目录、已有安装与可选会话设置。
- `POST /api/agents/models/refresh`：后台刷新，可携带 `room`。
- `POST /api/agents/installation`：选择已发现的安装 ID，执行任务期间拒绝更换。
- `GET /api/r/<room>/models/catalog?ai=<id>`、`POST /api/r/<room>/models/refresh`：当前会话的目录和刷新。
- 原 `POST /api/r/<room>/models` 保留 `model`、`effort`，新增可选 `reasoning: {"kind":"budget","value":2048}` 等结构。空对象清除覆盖，非法类型和超范围预算拒绝保存。

以上为管理界面接口，继续执行原有身份认证。能力输出只提取模型字段，不返回密钥、请求头或原始诊断输出。

成员协议保持 `protocol: 1`，增加可选 `capabilities` 上报和 `model_settings` 任务字段。目录按成员、当前连接端和有效凭证绑定；旧连接端显示未提供模型目录。新版连接端收到空设置也会清除之前的覆盖，支持恢复默认。更换执行设备、重连或撤销凭证后，不复用另一位成员的模型能力。

连接端的本机 MCP 服务不加载网页资源，源码连接包可以解压到另一目录启动。能力上报单独允许最大 1 MiB 的请求，避免模型多时撞上普通消息的 64 KiB 限制；普通消息限制保持原值。

HTTP 服务可显式配置 `capabilities_endpoint`，返回统一目录结构；不配置则只使用静态声明，不自动探测服务的未知接口。模型、effort 和 reasoning 随每轮任务发送。

## 验证与发布

兼容性 workflow 覆盖 Windows/macOS/Linux 和 Python 3.11/3.12，运行扫描、适配器、API、远程任务与前端状态测试；发布流程依赖该矩阵通过后才构建。当前本地实际运行覆盖 Windows，macOS/Linux 的原生 CI 结果须在下一次推送后确认，不能把路径模拟测试当作三个系统都已实机通过。

本轮未调用未安装的 Harness，也未替用户安装程序或重新登录。没有可供外部控制的已有接口时，会明确显示限制；不会伪装成已经验证过的模型控制。
