# 本机 Agent 发现：只保留一套实现

2.7.0 把两条并行开发线合成一条，发现/接入只保留 `bridge/discovery.py`。本文记录这个决定，避免以后又出现两套扫描逻辑各说各话。

## 决定

- **`main` 是唯一开发线。** 2.6.12 发布在 `codex/release-2-6-3`，那个分支只用于出安装包；除扫描以外的修复（关窗杀进程树、允许下载等）后续按普通改动合回 `main`。
- **`bridge/discovery.py` 是唯一的扫描实现。** 发布分支上的 `bridge/scan.py` / `static/scan.js` 不再合并，也不在 `main` 上保留。
- **版本号从 2.7.0 起。** 2.7.0 > 2.6.12，安装包可以正常升级，不会触发降级保护。

## 为什么不是反过来

`discovery.py` 覆盖了 `scan.py` 的全部能力，并且多了三件发布分支没有的东西：

1. **目录化清单**：`bridge/catalog/agents.json` 描述 20 个 Agent 的 CLI / ACP / 桌面 / MCP 四种接入方式；加新 Agent 只改数据，不改代码。
2. **连接策略分级**：`direct`（直接驱动）、`mcp`（写 MCP 配置 + 待命）、`unsupported`，由扫描结果直接决定一键连接的路径。
3. **与成员协议打通**：连接后直接生成联系人密钥、写 MCP 配置、返回 `/cojam_standby` 提示词，和 `members.py` 的待命协议是同一套。

`scan.py` 是单文件写死的宿主列表，继续维护会分叉出第二份「装没装」的判断。

## 扫描看到什么

同一份快照，按联系人归并成一行：

| 状态 | 含义 |
|---|---|
| `ready` | 已接入且验证可用 |
| `attention` | 找到了程序，但还没接或验证失败 |
| `connected` | 已接入，且待命端 / 自动响应端在线 |
| `desktop_cli` | 桌面软件和命令行都在 |
| `desktop_external` | 桌面软件通过 MCP 接入 |
| `desktop_only` | 只有桌面软件，暂不支持 CLI / ACP / MCP |
| `missing` | 清单里有，这台机器上没装 |

- 结果缓存 60 秒；`/api/discovery?scan=1` 或 `POST /api/discovery/scan` 强制重扫。
- 「装没装」以 `common.installed_app_names()` 为准：固定路径 + Windows 卸载注册表。`install.present()` 和 `discovery` 用同一份数据，不会出现「扫描说已连接、一键接入说没装」。
- 一个品牌/设备只出现一行；桌面端和 CLI 同时存在时合并展示。

## 连接方式

| 路径 | 适用 | 行为 |
|---|---|---|
| `direct` | 有 CLI 或 ACP | 写入联系人配置，Cojam 直接启动并驱动 |
| `mcp` | 只有桌面软件，但支持 MCP | 写 MCP 配置 + 生成密钥，软件内输入 `/cojam_standby` 进入待命 |
| `unsupported` | 只有图形界面 | 只展示，不假装能接 |

待命通道不区分驱动类型：Claude / Codex 内置联系人、ACP 联系人、`external` 联系人，只要桌面端在 MCP 待命，会话里 @ 它时都会优先派给待命端；待命端离线才回落到本机 CLI / ACP。用户不需要为同一个人建两个联系人。

## 相关文件

- `bridge/discovery.py`：扫描、归并、`connect()`
- `bridge/catalog/agents.json`：Agent 清单
- `bridge/install.py`：MCP 配置写入、宿主检测
- `bridge/common.py`：`installed_app_names()` / `app_installed()`
- `bridge/connections.py`：连接状态检查（ACP 走真实握手）
- `static/connections.js`：发现弹窗与待命徽标
