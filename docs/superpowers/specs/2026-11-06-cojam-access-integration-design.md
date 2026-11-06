# Cojam 接入整合设计

日期：2026-11-06
状态：待审阅
目标版本：2.6.0（API_REVISION 2）

## 1. 目标

把现有四条并列的接入方式收成两类，并解决三个实际使用问题：

1. **用户接入**与 **Harness 接入**分开。用户接入里只保留“我的设备（Cojam 客户端）”和“游客扫码/网址”；Harness 接入里只显示本机实际扫到的 harness，并保留自定义入口。
2. **游客同一设备只保留一个身份**：邀请变成“门票”，首次加入时才创建游客联系人；同一设备再次打开邀请复用原联系人，不再每扫一次码多一个联系人。
3. **游客会话可保活**：刷新、切页、重新打开页面自动恢复原身份；屋主端能看到在线/最近在线，而不是永远“待接入”。
4. **恢复自有设备的 Tailscale/远程完整界面**：屋主给自己的其他设备发一次性接入码，设备换取长期凭证后获得完整界面，并统一以“我”的身份参与会话。
5. **给出不同网络接入的可行方案**，本期只实现“可达地址”路径，不建中继、不做 WebRTC。

## 2. 已确认的决定

- 游客模型：邀请变门票，设备领身份；旧 `#key=` 链接继续兼容。
- MCP 配置：一键写入 + 状态回显；不做反向发现，不做公共 MCP 目录。
- 自有设备：完整界面 + 同一“我”身份；设备凭证可撤销。
- Harness：不预置展示；扫描到才显示；同一 harness 只出现一次；自定义永远保留。
- 跨网：本期只走可达地址（局域网 / Tailscale / 用户自己的隧道），不建中继，不做 WebRTC。

## 3. 非目标

- 不做云账户、登录、跨设备云同步。本期的“账户”是本机账户，账户 ID 存在本机；数据结构预留 `account_id`。
- 不做公共中继、不运营中继、不做 WebRTC、不做 TURN。
- 不提供访客附件、文件上传、管理员设置。
- 不改 MCP 服务器名 `ai-bridge`。旧配置继续有效，界面显示为“Cojam MCP（ai-bridge）”。
- 不改内置联系人的旧字段和已存在的 `data/agents.d` 配置；只做兼容和隐藏未安装项。
- 不给远程设备单独做一套“受限客户端”；屋主设备拿到完整界面。

## 4. 现状与根因

### 4.1 自有设备被当成游客

`bridge/web.py` 的 `GET /` 目前按来源分流：

```python
forwarded = any(self.headers.get(h) for h in ("Forwarded", "X-Forwarded-For", ...))
if forwarded or self.client_address[0] not in ("127.0.0.1", "::1") or host not in ("localhost", "127.0.0.1"):
    return self._static("join.html", ...)
```

Tailscale Serve 会带转发头，直接访问 Tailscale 地址也不是 loopback，因此 `/` 返回游客页。服务本身只监听 `127.0.0.1` 和 `common.lan_ipv4()` 返回的局域网 IPv4；`pick_lan_ip` 明确排除 `100.64.0.0/10`，所以直接访问 Tailscale IP 也不会命中监听。

这是本次要修复的回归。修复不能靠“信任 100.x 或转发头”，否则任何能访问端口的人都能拿完整界面；改为按凭证分流。

### 4.2 游客每次邀请都新建联系人

现在“其他用户”流程在生成邀请前必须先创建一个联系人和密钥。邀请链接绑定这个联系人；每新建一次邀请就多一个联系人。游客页面只把密钥放在内存里（`static/join.html` 的 `let token=''`），刷新就丢；屋主端看到一个从未连接的“待接入”联系人。

### 4.3 Harness 入口重复

Cursor、Gemini 同时出现在本机 CLI 预置和 MCP 宿主列表里；界面把 7 个 CLI 预置全部列出，未安装的也显示“未找到程序”。MCP 一键写入只覆盖 5 个宿主，且状态只在手动 MCP 分支里出现。

## 5. 身份与凭证模型

### 5.1 四类身份

| 身份 | 凭证 | 界面 | 房间身份 |
|---|---|---|---|
| 本机屋主 | 进程内 `RT_TOKEN`，每次启动随机 | 完整界面 | `user` |
| 我的其他设备 | 长期设备凭证，可撤销 | 完整界面 | `user` |
| 游客 | 游客联系人密钥，存浏览器本地 | 游客页 | 游客联系人 ID |
| Harness | 联系人密钥 | 无界面 | Harness 联系人 ID |

“同一账户下注册的设备共享一个用户联系人身份”在本期落为：本机账户 `account_id` 下所有设备都使用房间保留身份 `user`，不额外创建设备联系人。未来接入云账户时，只需把 `account_id` 换成云端账户 ID。

### 5.2 新数据文件

所有文件都放在 `LOG_DIR`（真实运行时是数据目录），用 `threading.RLock` 保护，用 `common.read_json` / `common.write_json` 原子写入。文件缺失时按空集合处理；`account.json` 首次启动自动生成。密钥只存 SHA-256 摘要。

`data/account.json`
```json
{"id": "acct_<24>", "label": "我的 Cojam", "created": 1760000000}
```

`data/devices.json`
```json
{"devices": [{
  "id": "dev_<24>", "label": "我的 iPhone",
  "secret_hash": "<sha256>", "account_id": "acct_<24>",
  "created": 1760000000, "last_seen": 1760000000, "revoked": false
}]}
```

`data/device_codes.json`
```json
{"codes": [{
  "id": "code_<24>", "hash": "<sha256>", "label": "新设备",
  "created": 1760000000, "expires": 1760000600, "used": false
}]}
```

`data/invites.json`
```json
{"invites": [{
  "id": "inv_<24>", "hash": "<sha256>", "room": "<room id>",
  "permissions": {"read": true, "post": true, "trigger": true,
                  "board": false, "files": false, "work": false},
  "label": "游客邀请", "created": 1760000000, "expires": 1760604800,
  "revoked": false
}]}
```

`data/guests.json`
```json
{"guests": {
  "guest-<8hex>": {
    "invites": ["inv_<24>"], "device": "iPhone · Safari",
    "device_hash": "<sha256>", "created": 1760000000
  }
}}
```

`invites` 记录这台设备的游客领取过的门票。同一游客领取第二张邀请时追加，不覆盖第一张，屋主才能在每张邀请下看到“已加入游客”。

游客联系人本身仍写入 `data/agents.d/guest-<8hex>.json`，字段为 `kind=user`、`driver=external`、`enabled=true`、`label=<游客显示名>`。这样现有的 `access.identify`、`connections.status`、房间权限和成员隔离全部复用，不新增一套游客鉴权。

### 5.3 凭证格式与校验

- 设备凭证：`dev_<secrets.token_urlsafe(32)>`，只在签发时返回明文；`devices.json` 存摘要。
- 设备接入码：`code_<id>.<secrets.token_urlsafe(24)>`，10 分钟有效，一次性；`device_codes.json` 存 secret 的摘要。
- 游客邀请：`inv_<id>.<secrets.token_urlsafe(32)>`，默认 7 天有效，可撤销，可被多台设备使用；`invites.json` 存 secret 的摘要。
- 游客联系人密钥：复用 `access.credential(contact_id)`，只存摘要；游客页首次领取时拿到明文，之后只用自己的本地副本。
- 游客设备标识：`deviceKey`，浏览器生成的 32 字节随机值，存 `localStorage`；服务端在 `guests.json` 存摘要。它只用于同一设备幂等和密钥丢失后的身份恢复，不替代联系人密钥。

## 6. 自有设备接入

### 6.1 屋主界面

“＋ 接入 → 用户接入 → 我的设备”显示：

- 当前设备（本机）状态。
- 账户信息：`本机账户`，并注明“以后登录同一账户，设备自动共享「我」身份”。
- 已注册设备列表：名称、在线/最近在线（90 秒内更新过 `last_seen` 算在线，否则显示最近时间）、当前设备标记、撤销按钮。
- “接入我的其他设备”：生成一次性接入码和链接/二维码，有效期 10 分钟；地址可选局域网、Tailscale、自定义。

接口（`/api/devices/code`、`/api/devices`、`/api/devices/revoke` 均需屋主鉴权）：

- `POST /api/devices/code` `{"label": "我的 iPhone", "base": "http://192.168.1.9:8766"}` → `{"code": "code_...", "url": "<base>/enroll#code=...", "expires": 1760000600}`。
- `GET /api/devices` → `{"account": {"id": "...", "label": "..."}, "devices": [{"id", "label", "created", "last_seen", "current"}]}`，不返回摘要。
- `POST /api/devices/revoke` `{"id": "dev_..."}`。
- `POST /api/devices/enroll` 公开，见 6.2。

### 6.2 设备接入页

新增 `static/enroll.html`，路由 `GET /enroll`。页面从 `#code=` 读取接入码，要求填写设备名（默认从 UA 推断），提交 `POST /api/devices/enroll`。

成功后：

1. 服务端创建设备记录，返回 `{"ok": true, "token": "dev_...", "device": {...}}`。
2. 响应设置 `cojam_device=<token>; HttpOnly; SameSite=Lax; Path=/; Max-Age=31536000`；如果请求是 HTTPS（直接 TLS 或 `X-Forwarded-Proto: https`），加 `Secure`。
3. 页面直接跳转 `/`，不把凭证写入 localStorage。`GET /` 会把 cookie 里的凭证注入 `meta[name=rt-token]`，现有前端拦截 `fetch` 的逻辑照常带上 `X-RT-Token`。

设备凭证长期有效，重启服务不失效；撤销后立即失效。设备丢失时屋主在列表里撤销即可。

Cookie 只用于 `GET /` 的页面分流和 `/files/` 的图片/附件读取；API 请求由页面注入的 `meta[name=rt-token]` 放到 `X-RT-Token`。清除网站数据或换浏览器后 cookie 丢失，需要重新接入。

浏览器按 origin 隔离存储，所以设备身份绑定在接入时使用的地址上：从 `http://192.168.x.x:8766` 接入的设备，改用 Tailscale 域名打开时没有本地凭证。设备和屋主应固定使用同一个地址；接入页会提示这一点。地址变化时重新生成一次接入码即可，不需要改账户或联系人。

### 6.3 完整界面的分流

`GET /` 改为按凭证判断：

1. 请求带有效 `cojam_device` cookie → 返回完整界面，注入该设备凭证作为 `__RT_TOKEN__`。
2. 本机请求（来源 loopback、Host 是 localhost/127.0.0.1、无转发头）且无凭证 → 返回完整界面，注入进程 `RT_TOKEN`（保持现在的本地体验，桌面壳也走这条）。
3. 其他情况 → 返回 `join.html`。

不再根据“是否带转发头”直接判游客。远程 Tailscale Serve 请求只要带有效设备 cookie，就拿完整界面。

公开路由在管理员检查之前处理：`GET /enroll`、`GET /join`、`POST /api/devices/enroll`、`POST /api/guest/redeem`。它们自己校验一次性码或邀请票，不读取 `RT_TOKEN`。

鉴权分两层，避免 cookie 变成 CSRF 入口：

- `_from_browser()` 只接受请求头 `X-RT-Token`：进程 `RT_TOKEN`，或 `devices.verify` 通过且未撤销的设备凭证。所有管理 API 沿用这一层。
- 只读资源（`/files/`、`/static/member-connector.zip`）额外接受 `cojam_device` cookie，让 `<img>` 和下载链接能用。cookie 本身不能调用管理 API。

设备凭证校验成功时顺带更新 `last_seen`（每设备最多每 30 秒写一次）。`POST /api/ui/session` 收到设备凭证时返回 `{"ok": true, "tracked": false}`，不更新 `OwnerLifecycle`，避免手机页面让桌面壳误判还有管理员标签。

`GET /api/server/info` 增加 `tailscale_url`（如果检测到）和 `owner_devices_supported: true`。`/api/server/info` 仍是公开只读接口，不返回账户、设备或邀请列表。

### 6.4 生命周期

`POST /api/ui/session` 只在凭证等于进程 `RT_TOKEN` 时更新 `OwnerLifecycle`。远程设备标签不参与“最后一个标签关闭就退出后台”，避免手机页面影响桌面壳生命周期。

### 6.5 Tailscale 地址

新增 `common.tailscale_ipv4()`：

1. 优先运行 `tailscale ip -4`（带 2 秒超时、无窗口），取第一个 `100.64.0.0/10` 地址。
2. 找不到命令时尝试 Windows 默认安装路径 `C:\Program Files\Tailscale\tailscale.exe`。
3. 都失败返回 `None`，不报错。

启动时如果拿到地址，额外在该地址上监听 `ROUNDTABLE_TAILSCALE_PORT`（默认 8767），设置 `server.tailscale_url = "http://<ip>:8767"` 并在启动日志和 `/api/server/info` 中给出。端口被占或监听失败就跳过，不影响 LAN 监听。

不能和 LAN 监听共用 8766：`ExclusiveServer` 在 Windows 上设置了 `SO_EXCLUSIVEADDRUSE`，同一端口即使绑不同地址也会失败。两个监听各自独立，任何一侧失败都不阻止另一侧。

Tailscale Serve 路径不需要新监听：Serve 代理到 `127.0.0.1:8765`，设备 cookie 生效后 `/` 给完整界面。本期不解析 `tailscale serve status`，Serve 的 HTTPS 地址放在“自定义地址”里由屋主填写。直接 IP 和 Serve 两条路径都在“我的设备”地址选择里展示。

## 7. 游客接入

### 7.1 屋主生成邀请

“＋ 接入 → 用户接入 → 游客”：

1. 选择会话（可新建）和权限（复用现有权限网格）。
2. 选择可达地址（局域网 / Tailscale / 自定义）。
3. 生成邀请票，展示二维码和链接：`<base>/join#invite=<url-encoded invite>`。
4. 邀请列表显示：会话、创建时间、过期时间、状态（等待加入 / 已加入 N 位游客 / 已过期 / 已撤销）、已加入游客（名称、设备、在线状态）、撤销按钮。
5. 这一步不创建联系人。

### 7.2 游客首次加入

`POST /api/guest/redeem`（公开，不需要屋主 token）：

请求：
```json
{"invite": "inv_<id>.<secret>", "token": "<可选，已有联系人密钥>",
 "device_key": "<浏览器生成的随机值>", "name": "<可选显示名>",
 "device": "<可选设备描述>"}
```

处理（邀请校验、游客查找、创建都在同一把锁内，避免双击或多标签同时兑换创建两个联系人）：

1. 校验邀请存在、未撤销、未过期；邀请里的会话必须存在且未归档。失败返回 404/403/410/409，不创建任何联系人。会话先校验，避免出现“联系人建了但会话不可用”。
2. 如果 `token` 有效且对应一个 `kind=user`、`driver=external` 的联系人 → 复用该联系人。其他类型的 token（agent、harness、管理员）一律忽略并继续下一步，不报错也不复用。复用 `guest-*` 联系人时，也把邀请记到对应游客记录（见第 6 步）。
3. 否则如果 `device_key` 的摘要命中 `guests.json` 中的游客，且其联系人在 `agents.d` 中仍存在、已启用、未删除 → 复用该游客联系人，并用 `access.credential` 轮换一个新密钥返回。这是同一设备密钥丢失后的恢复路径；游客已被屋主移除或停用时继续下一步。
4. 否则创建游客联系人：
   - ID 为 `guest-<8hex>`，确保不重复。
   - 显示名取 `name`，为空则用“访客”；长度 1–40，去控制字符。
   - 写入 `data/agents.d/<id>.json`，`kind=user`、`driver=external`、`enabled=true`。
   - 调用 `access.credential(id)` 生成联系人密钥。
   - 写入 `guests.json`；`invites` 里没有这个邀请 ID 时才追加，记录设备描述和 `device_hash`。
5. 把联系人加入邀请指定的会话：读取房间，`set_members` 保留现有成员和权限。新加入时按邀请里的权限写入；已经是成员时不覆盖现有权限（屋主可在成员设置里单独调整）。加入前再次确认联系人未在会话中，避免重复写入。
6. 把邀请 ID 记到游客记录：联系人 ID 是 `guest-*` 时，找到对应记录；没有记录就按当前设备补建一条，已有记录时不覆盖 `device_hash`。`invites` 里已有这个邀请 ID 就不重复追加。邀请的“已加入游客”由 `guests.json` 反查，不再单独维护计数。
7. 返回：
```json
{"ok": true, "token": "<仅新建或轮换时返回>",
 "contact": {"id": "...", "label": "...", "kind": "user"},
 "rooms": [ ... ], "room": "<邀请的会话 ID>"}
```

同一设备重复提交同一邀请是幂等的：有 token 就复用，不会多创建联系人。双击由页面 `busy` 标志和服务端锁共同挡住。

### 7.3 游客页面

`static/join.html` 调整：

- 存储键：`cojam_guest_token`、`cojam_guest_device_key`、`cojam_guest_name`（localStorage，按 origin 隔离）。
- localStorage 按 origin 隔离，同一设备换访问地址（局域网 ↔ Tailscale）或换浏览器会被当成新游客。屋主发邀请时应固定一个双方都能打开的地址。
- 打开顺序：
  1. 有 `#invite=` → 没有本地 token 且没有保存过名称时，先显示“怎么称呼你？”输入框（默认“访客”），确认后才调 `/api/guest/redeem`（带上本地 token/device_key/name）；已有身份时直接兑换。成功后保存返回的 token 和名称，清除 fragment，刷新会话列表并打开邀请的会话。兑换失败（404/410/409）时保留现有登录状态，只显示失败原因。
  2. 有 `#key=` → 走旧流程，验证 `/api/external/hello` 成功后保存 token，清除地址栏 key，并清除本地 `deviceKey`：身份以刚验证的密钥为准，避免设备密钥指向另一个游客。
  3. 没有 fragment → 如果有本地 token，调 `/api/external/hello` + `/api/external/rooms` 自动恢复；如果 401，清除本地 token 并提示重新打开邀请。这里不自动用 deviceKey 恢复，避免在屋主已撤销后静默换发新密钥。
- 称呼输入框就是首次加入的唯一额外步骤；不填也能继续，联系人名称为“访客”。名称只影响显示，不改变联系人 ID。
- 页面顶部显示“当前身份：<名称> · 此设备已记住”，提供“退出此设备”：清除 token、deviceKey 和名称，不删除屋主端联系人。退出后同一浏览器的下一次邀请会创建一个新游客身份；旧联系人仍留在屋主列表，可由屋主移除。游客卡片不提供单独的“重置密钥”，要断开设备就用“移除游客”。
- 心跳：有 token 时每 20 秒 `POST /api/external/hello`，`client` 传 `Cojam 游客`；后台标签页被节流也不影响 90 秒在线窗口。
- 会话列表和消息轮询保持不变；自动打开逻辑保留：邀请指定的会话优先，否则只有一个受邀会话时直接打开。

### 7.4 旧链接与旧联系人

- 旧 `#key=` 链接继续可用；页面保存密钥，刷新自动恢复。
- 旧流程预建的用户联系人仍在联系人列表里。对“有密钥、但从未收到连接证据”的用户联系人，列表显示“邀请已生成，从未连接”，并提供“删除”入口。删除复用现有 `/api/agents/remove` 流程：先从所有会话移除，再删除联系人；该接口同时清掉密钥摘要。旧联系人没有 `guests.json` 记录，不需要额外迁移。
- 新邀请不再产生“待接入联系人”；等待状态挂在邀请上，而不是联系人上。

### 7.5 屋主管理游客

在“游客”卡片下显示两类数据：

- 邀请：等待加入 / 已加入 N 位游客 / 已过期 / 已撤销；可撤销。
- 游客：名称、设备、在线/最近在线、所属邀请（可能有多张）、移除按钮。

新增接口：

- `POST /api/invites`（屋主）`{"room": "...", "permissions": {...}, "label": "游客邀请", "expires_days": 7, "base": "http://192.168.1.9:8766"}` → 生成邀请票，返回 `invite`、`url`（`<base>/join#invite=...`）、`expires`。`base` 只用于拼链接，服务端不请求它；权限键必须在 `PERMISSIONS` 内，未知键丢弃。
- `GET /api/invites`（屋主）→ 邀请列表 + 已加入游客摘要。
- `POST /api/invites/revoke`（屋主）`{"id": "inv_..."}`：只阻止新的兑换，不踢出已经加入的游客。
- `POST /api/guests/remove`（屋主）`{"id": "guest-..."}`：从所有会话移除、撤销密钥、删除联系人和游客记录。

## 8. Harness 接入

### 8.1 统一扫描模型

新增 `bridge/harnesses.py`，把 `registry` 的 CLI 规格和 `install.HOSTS` 的 MCP 配置能力按 harness ID 合并，输出：

```json
[{
  "id": "cursor", "label": "Cursor",
  "present": true,
  "cli": {"present": true, "contact": "cursor", "label": "Cursor · 本机执行"},
  "mcp": {"present": true, "contact": "cursor-mcp", "label": "Cursor · MCP 接入",
          "installed": false, "path": "~/.cursor/mcp.json", "restart": "重启 Cursor"}
}]
```

规则：

- 同一 ID 只输出一条；Cursor、Gemini 不再重复出现在两个入口。
- 顶层 `present = cli.present or mcp.present`；为 false 的 harness 不返回给界面。内部预置仍保留，用作检测规则和参数模板。
- `cli.present` 用 `registry.exe_of` / `spec["enabled"]` 判断；`mcp.present` 用 `install.present(host)` 判断。
- `mcp.installed` 只用 `install.installed(host)` 判断配置文件里是否已有 `ai-bridge`；是否连上另取 `connections.status`。服务端只有密钥摘要，无法比对配置文件里的明文是否还是当前密钥。
- 自定义 harness 不进入扫描列表，由界面固定提供。

`GET /api/agents?reload=1` 增加 `harnesses` 字段；现有 `hosts`、`connections`、`members` 保留，避免破坏旧页面。

### 8.2 界面

“Harness 接入”第二步只显示：

1. 扫描结果卡片（只显示 `present` 的）。
2. “自定义 Harness”卡片。

每张扫描卡片按能力显示按钮：

- **让 Cojam 调用它**（有 `cli`）：走现有本机 Agent 流程，联系人 ID 用预置 ID（如 `cursor`），验证 `--version`，再选会话权限。
- **接入 Cojam MCP**（有 `mcp`）：一键创建外部联系人 `<id>-mcp`，生成联系人密钥，备份并写入该 harness 的 MCP 配置，显示“已写入，重启后生效 / 已连接”。
- 两种能力都有时，两个按钮并列；卡片本身只出现一次。
- 已接入的 MCP 显示“重新写入 / 更新密钥 / 查看配置”，仍保留复制配置和手动入口。重新写入会轮换密钥；旧配置立即失效，重启 harness 加载新配置后才会恢复连接。

“自定义 Harness”展开四个原有流程：

- 本机 CLI（名称、命令、参数 JSON）。
- HTTP 服务（回调地址、服务密钥）。
- 其他设备上的成员（成员连接端 / 手动参与）。
- 手动 MCP（地址、密钥、复制配置）。

### 8.3 MCP 一键接口

新增 `POST /api/harnesses/mcp`（屋主）：

```json
{"harness": "cursor", "name": "Cursor MCP", "rewrite": false}
```

请求可带 `"rewrite": true`，表示重新写入或轮换密钥。处理：

1. 校验 harness 在本机扫描结果中且 `mcp.present`。
2. 联系人 ID 固定为 `<harness>-mcp`；不存在则 `registry.save_agent` 创建 `kind=agent`、`driver=external`、`enabled=true`。已存在但未启用时先启用。
3. 配置已经写入且 `rewrite` 不为 true：只返回当前状态和手动配置，不生成新密钥。`access.credential` 只存摘要，服务端拿不回旧明文，所以“复用旧密钥写配置”不成立。
4. 首次写入或 `rewrite=true`：调用 `access.credential` 轮换出新密钥（同时使旧配置的密钥失效），备份目标配置文件，再调 `install.host_install(harness, token)` 写入。写入失败时恢复备份和旧密钥摘要，返回手动配置和错误，不假装成功。
5. 成功时明文密钥只在本次响应里出现，用于写入配置，不落盘、不进日志。
6. 返回联系人、配置写入结果、`restart` 提示、`installed` 状态。

配置名继续是 `ai-bridge`，地址是 `http://127.0.0.1:<PORT>/mcp`，请求头 `Authorization: Bearer <联系人密钥>`。重启 harness 后，它的 MCP 列表里出现 `ai-bridge`；Cojam 界面文案写“Cojam MCP（配置名 ai-bridge）”。

### 8.4 状态

- 配置已写入、没有连接证据：`已写入，重启 <harness> 后生效`。
- 90 秒内收到认证请求：`已连接`。
- 重新写入后：`配置已更新，重启 <harness> 生效`；旧配置的请求会 401，不会显示已连接。Cojam 无法识别是哪台 harness 拿了旧密钥，所以不做“密钥已失效”的自动判断。
- 本机 CLI：沿用 `connections.status` 的“程序验证通过 / 验证失败 / 未验证”。

## 9. 接入界面结构

`＋ 接入` 第一步：

```
谁来接入？
[用户接入]    朋友、手机、我的其他设备
[Harness 接入] 本机 AI、Cursor/Claude/Codex 等
```

第二步：

- 用户接入：
  - 我的设备（完整界面 · 同一“我”身份）
  - 游客（扫码 / 网址，只进指定会话）
- Harness 接入：
  - 扫描到的 harness 卡片
  - 自定义 Harness

后续步骤复用现有“配置与验证 → 会话与权限 → 完成”，但“我的设备”不需要会话权限步骤，完成页直接给设备列表和设备接入码。

`static/connections.js` 拆分，避免单个文件继续膨胀：

- `static/access.js`：向导外壳、分组、步骤条。
- `static/access-user.js`：我的设备 + 游客。
- `static/access-harness.js`：扫描卡片 + 自定义 + MCP 一键。
- `index.html` 按顺序引入；现有 `connections.js` 中可复用的函数保留或迁移。

## 10. 不同网络接入

| 方案 | 跨网络 | 普通用户成本 | 是否走你的中继 | 本期 |
|---|---|---|---|---|
| 局域网直连 | 否 | 零 | 否 | 支持 |
| Tailscale / ZeroTier | 是 | 装客户端、建账号 | 否，走第三方网络 | 支持自有设备；游客不默认 |
| 用户自带隧道（Cloudflare Tunnel / ngrok / frp） | 是 | 配置或自备 VPS | 否 | 支持填地址，不代管 |
| WebRTC + STUN/信令 | 多数可，对称 NAT 需 TURN | 中 | 否 | 不做 |
| 自建中继（VPS） | 是 | 每月服务器费 | 是 | 不做 |
| 公共中继 | 是 | 产品方承担 | 是 | 不做 |

本期行为：

- 自有设备：Tailscale Serve、Tailscale IP 监听、局域网、用户自己的隧道都可以作为设备接入地址。
- 游客：同 WiFi 扫码；跨网需要屋主提供一个双方可达的 HTTPS/HTTP 地址。Cojam 不自动创建隧道，不改防火墙，不运营中继。
- 文档新增 `docs/NETWORKING.md`，把这张表和配置示例写清楚。

## 11. 安全与隐私

- 设备凭证 32 字节随机，服务端只存 SHA-256；cookie 为 HttpOnly、SameSite=Lax，HTTPS 时加 Secure；接入码 10 分钟一次性。
- 游客邀请 32 字节随机，服务端只存摘要；默认 7 天，可撤销；链接泄露等同于把门票给了别人，屋主可随时撤销。
- 游客联系人密钥复用现有机制，只存摘要；联系人只能访问受邀会话，不能调用管理接口、成员接口或项目接口。
- 设备凭证等于完整管理权限，界面必须明确提示“只发给可信设备”，并提供撤销列表。
- 游客页继续用 `textContent` 写消息和名称，避免把邀请/联系人名称当 HTML。
- 设备接入页和游客页都在公开路由上；接口对无效凭证返回 401/403，不泄露联系人列表。
- 撤销设备只影响完整界面；撤销游客会同时撤销联系人密钥并从会话移除。已经读到的消息不会从对方设备删除，文档要写清。
- 前端全局 `fetch` 注入 `X-RT-Token` 时只对同源 URL 注入，避免把设备凭证带到外部地址。
- 日志不记录明文设备凭证、邀请 secret 或联系人密钥。

## 12. 兼容与迁移

- 旧 `#key=` 链接、旧用户联系人、旧 MCP 配置（`ai-bridge`）全部继续有效。
- 现有 `data/agents.d`、内置 Claude/Codex、自定义 CLI/HTTP 联系人不需要迁移。
- 未安装的预置不再显示，但仍可被已有联系人使用；不删除文件。
- 远程 Tailscale 用户升级后需要在本机生成一次设备接入码；之后长期有效。
- 旧“从未连接”的用户联系人可手动删除；新邀请不再产生这类联系人。
- 版本升到 2.6.0，`API_REVISION` 升到 2；`static/connections.js` 的版本守卫、`static/sw.js` 缓存名、`pyproject.toml`、官网下载版本同步更新。
- 旧页面在新后台会收到“版本不一致，请重新打开”的提示，这是现有机制。

## 13. 错误与状态

| 场景 | 行为 |
|---|---|
| 设备接入码不存在 | 401，提示重新生成 |
| 设备接入码过期/已用 | 410，提示重新生成 |
| 设备凭证被撤销 | 下次请求 401，完整界面跳回游客页或显示“设备已撤销” |
| 邀请不存在 | 404，提示邀请无效 |
| 邀请过期 | 410，提示让屋主重新生成；已加入的会话仍可访问 |
| 邀请被撤销 | 403，提示让屋主重新生成；已加入的会话仍可访问 |
| 邀请里的会话已删除/归档 | 409，不创建联系人，提示会话不可用 |
| MCP 配置写入失败 | 恢复备份配置和旧密钥，返回错误和可复制配置，不显示“已接入” |
| 游客密钥失效 | 401，清除本地 token，提示重新打开邀请；不自动换发新密钥 |
| Harness 未安装 | 扫描列表不出现；自定义仍可用 |
| 房间正在运行 | 沿用现有“等待结束后再改成员”的 409 |

## 14. 测试

后端：

- `tests/test_devices.py`：账户创建、接入码一次性/过期、凭证校验、撤销、`/` 分流（本机无凭证、远程无凭证、远程带设备 cookie、带转发头 + cookie、桌面壳 loopback）、`/files/` 接受设备 cookie 而管理 API 拒绝仅 cookie 的请求、设备标签不触发生命周期、Tailscale IP 解析（mock）。
- `tests/test_invites.py`：邀请创建/过期/撤销；会话无效时不创建联系人；首次兑换创建联系人；同 token 重复兑换复用；agent token 不被游客复用；新设备兑换创建第二个联系人；同一 deviceKey 恢复并轮换密钥；多张邀请追加到同一游客；权限和会话成员正确；旧 `#key=` 不受影响；移除游客清理房间/密钥/元数据；游客不能访问管理接口。
- `tests/test_harnesses.py`：扫描合并 Cursor/Gemini 不重复；未安装不返回；自定义保留；MCP 一键写入备份、配置名 `ai-bridge`、失败回滚；已安装且未要求 rewrite 时不轮换密钥；状态字段。
- `tests/test_lan.py`：现有 LAN 行为不回归；Tailscale 监听（mock `tailscale_ipv4`）；远程 `/` 带设备 cookie 时返回完整界面。
- `tests/test_contacts_access.py`：现有认证、隔离、MCP 测试继续通过；`_from_browser` 新凭证路径。
- `tests/test_frontend_runtime.py`：游客页 localStorage 恢复、邀请兑换、心跳、旧 key、401 清理；接入向导两步分组；Harness 卡片去重。

手工端到端：

1. 本机生成设备接入码，用 Tailscale Serve/MagicDNS 在手机打开，得到完整界面；关掉再开仍是完整界面；屋主撤销后失效。
2. 游客 A 打开邀请 → 创建一个联系人；刷新仍同一身份；再开一个新邀请仍是同一联系人；屋主看到在线；换一台设备得到另一个联系人。
3. 旧 `#key=` 链接仍能加入并保活。
4. Harness 扫描只显示已安装项；Cursor/Gemini 只出现一次；一键写入后重启 Cursor，MCP 列表出现 `ai-bridge`，Cojam 显示已连接。
5. 完整测试套件和打包产物。

## 15. 分期

1. **自有设备凭证 + Tailscale 完整界面恢复**：`devices.py`、`enroll` 页、`/` 分流、`_from_browser`、生命周期、Tailscale 监听、我的设备界面。
2. **游客邀请票 + 懒创建 + 保活**：`invites.py`、`/api/guest/redeem`、`guests.json`、join 页本地存储、邀请/游客管理、旧联系人清理。
3. **接入入口整合 + Harness 扫描/MCP**：`harnesses.py`、界面拆分、扫描去重、一键 MCP、状态回显。
4. **跨网文档与回归**：`docs/NETWORKING.md`、`docs/CONTACTS.md`、README、官网版本、全量测试和打包。

每一期独立可验收；第 1 期先修回归，第 2 期解决游客身份，第 3 期再做界面整合，避免在身份模型未定时反复改 UI。
