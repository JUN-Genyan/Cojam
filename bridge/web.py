# -*- coding: utf-8 -*-
"""HTTP 接口和启动。

会议相关的接口都带会议号：/api/r/<会议号>/<动作>，每个浏览器标签页可以开着不同的会议，互不影响。
管理员接口只认页面注入的 X-RT-Token；
外部程序用 /api/post 或 /mcp；钩子用 /api/guard/*（带钥匙）。"""
import hmac, json, mimetypes, os, re, secrets, socket, sys, threading, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import access, agents, common, conn_settings, connections, devices, discovery, install, invites, mcp, members, projects, registry, room as R
from .common import (AIS, EXTERNAL_SPEAKERS, HERE, HOST, MAX_ANN, MAX_BODY, MAX_UPLOAD, MEET_DIR, PORT, PROJECT,
                     in_dir, kind_of)
from .guard import USER, guard
from .version import VERSION, API_REVISION

RT_TOKEN = secrets.token_urlsafe(24)   # 每次启动随机；页面加载时注入 HTML，前端每个请求带 X-RT-Token
USER_ONLY_MSG = "这个接口只给网页上的用户用；外部程序请用 /api/post 或 /mcp 并写明自己的身份"
STATIC = os.path.join(HERE, "static")
INDEX = os.environ.get("ROUNDTABLE_INDEX") or os.path.join(HERE, "index.html")   # 页面；开发时可以指到别的文件
ROOM_PATH = re.compile(r"^/api/r/([^/]+)/([a-z/]+)$")


class Handler(BaseHTTPRequestHandler):
    server_version = "AIBridge"

    def log_message(self, *a):
        pass

    # ---------- 回应 ----------
    def _send(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj, extra=None):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", extra)

    def _length(self):
        try:
            return int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return -1

    def _read_json(self):
        n = self._length()
        if n < 0 or n > MAX_BODY:
            self._json(413, {"error": "too large"})
            return None
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            self._json(400, {"error": "bad json"})
            return None
        if not isinstance(data, dict):
            self._json(400, {"error": "bad json"})
            return None
        return data

    def _from_browser(self):
        """管理员能力：进程内令牌，或请求头里的未撤销设备凭证。cookie 不在这里放行。"""
        tok = self.headers.get("X-RT-Token") or ""
        if not tok:
            return False
        if hmac.compare_digest(tok.encode("utf-8"), RT_TOKEN.encode("utf-8")):
            self._device_id = ""
            return True
        device = devices.verify(tok)
        if not device:
            return False
        self._device_id = device["id"]
        return True

    def _cookies(self):
        out = {}
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, sep, value = part.strip().partition("=")
            if sep and name:
                out[name] = value
        return out

    def _owner_cookie(self):
        return self._cookies().get("rt_owner") == RT_TOKEN

    def _device_cookie(self):
        token = self._cookies().get("cojam_device") or ""
        return token if token and devices.verify(token) else ""

    def _is_local_owner(self):
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        forwarded = any(self.headers.get(h) for h in ("Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Real-IP"))
        return (not forwarded and self.client_address[0] in ("127.0.0.1", "::1")
                and host in ("localhost", "127.0.0.1"))

    def _https(self):
        return (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"

    def _device_cookie_header(self, token):
        parts = ["cojam_device=" + token, "HttpOnly", "SameSite=Lax", "Path=/", "Max-Age=31536000"]
        if self._https():
            parts.append("Secure")
        return "; ".join(parts)

    @staticmethod
    def _base_url(value):
        try:
            url = urlsplit(str(value or "").strip())
        except ValueError:
            return ""
        if url.scheme not in ("http", "https") or not url.netloc:
            return ""
        if url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
            return ""
        return "%s://%s" % (url.scheme, url.netloc)

    def _owner_page(self, token, extra=None):
        html = getattr(self.server, "ui_index", None)
        if html is None:
            with open(INDEX, "rb") as f:
                html = f.read()
        html = html.replace(b"__RT_TOKEN__", token.encode("ascii"))
        return self._send(200, html, "text/html; charset=utf-8", extra)

    def _bearer(self):
        a = self.headers.get("Authorization") or ""
        if a.lower().startswith("bearer "):
            return a[7:].strip()
        return (self.headers.get("X-Bridge-Key") or "").strip()

    # ---------- 静态 ----------
    def _static(self, name, ctype):
        if name == "member-connector.zip":
            if not self._from_browser() and not self._owner_cookie() and not self._device_cookie():
                return self._json(403, {"error": USER_ONLY_MSG})
            if not os.path.isfile(os.path.join(STATIC, name)) and not getattr(sys, "frozen", False):
                from .member_package import build
                build()
        p = os.path.join(STATIC, name)
        if not os.path.isfile(p):
            return self._send(404, b"not found", "text/plain")
        body = getattr(self.server, "ui_assets", {}).get(name)
        if body is None:
            with open(p, "rb") as f:
                body = f.read()
        extra = {"Service-Worker-Allowed": "/"} if name == "sw.js" else None
        self._send(200, body, ctype, extra)

    def _serve_file(self, rel):
        rel = unquote(rel)
        if "\x00" in rel:
            return self._send(400, b"bad", "text/plain")
        base = os.path.realpath(MEET_DIR)
        full = os.path.realpath(os.path.join(base, rel.replace("/", os.sep)))
        if not in_dir(full, base):
            return self._send(403, b"forbidden", "text/plain")
        parts = os.path.relpath(full, base).split(os.sep)
        if len(parts) < 3 or parts[1] != "files":  # 只放行 <会议>/files/ 里的东西
            return self._send(403, b"forbidden", "text/plain")
        if not os.path.isfile(full):
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        safe = kind_of(full) in ("image", "audio") or ctype == "application/pdf"
        if ctype == "image/svg+xml":
            safe = False
        total = os.path.getsize(full)
        start, end, code = 0, total - 1, 200
        mt = re.match(r"bytes=(\d*)-(\d*)$", (self.headers.get("Range", "")).strip())
        if mt and (mt.group(1) or mt.group(2)):
            if mt.group(1):
                start = int(mt.group(1))
                if mt.group(2):
                    end = min(end, int(mt.group(2)))
            else:
                start = max(0, total - int(mt.group(2)))
            if start > end or start >= total:
                return self._send(416, b"", "text/plain", {"Content-Range": "bytes */%d" % total})
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "private, max-age=3600")
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, total))
        if not safe:
            self.send_header("Content-Disposition", "attachment")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(full, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(left, 1 << 20))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    # ---------- GET ----------
    def do_GET(self):
        path, _, qs = self.path.partition("?")
        q = parse_qs(qs)
        if path == "/api/server/info":
            return self._json(200, {"app": "AI Bridge", "version": VERSION, "api_revision": API_REVISION,
                                    "lan_url": getattr(self.server, "lan_url", None),
                                    "tailscale_url": getattr(self.server, "tailscale_url", None),
                                    "owner_devices_supported": True})
        if path == "/enroll":
            return self._static("enroll.html", "text/html; charset=utf-8")
        if path == "/join":
            return self._static("join.html", "text/html; charset=utf-8")
        if path in ("/", "/index.html"):
            device_token = self._device_cookie()
            if device_token:
                return self._owner_page(device_token)
            if self._is_local_owner():
                return self._owner_page(RT_TOKEN, {"Set-Cookie": "rt_owner=" + RT_TOKEN + "; HttpOnly; SameSite=Strict; Path=/"})
            return self._static("join.html", "text/html; charset=utf-8")
        if path == "/sw.js":
            return self._static("sw.js", "text/javascript; charset=utf-8")
        if path == "/manifest.webmanifest":
            return self._static("manifest.webmanifest", "application/manifest+json")
        if path.startswith("/static/") and re.match(r"^/static/[\w.\-]+$", path):
            name = path[len("/static/"):]
            return self._static(name, mimetypes.guess_type(name)[0] or "application/octet-stream")
        if path.startswith("/files/"):
            if not self._from_browser() and not self._owner_cookie() and not self._device_cookie():
                return self._json(403, {"error": USER_ONLY_MSG})
            return self._serve_file(path[len("/files/"):])
        if path == "/mcp":
            return self._json(405, {"error": "这个 MCP 服务只收 POST"}, {"Allow": "POST"})
        if path.startswith("/api/external/"):
            return self._external_get(path, q)
        if path.startswith("/api/") and not self._from_browser():
            return self._json(403, {"error": USER_ONLY_MSG})
        if path == "/api/update/check":
            from . import updater
            return self._json(200, updater.check())
        if path == "/api/update/status":
            from . import updater
            return self._json(200, updater.status())
        if path == "/api/server/runtime":
            from .common import LOG_DIR
            from .server_runtime import installation
            return self._json(200, {"install": installation(), "data": os.path.realpath(LOG_DIR)})
        if path == "/api/rooms":
            rows = R.rooms.listing()
            for row in rows:
                row["project"] = row.get("project") or projects.legacy_id(row["workdir"])
            return self._json(200, {"rooms": rows, "projects": projects.listing(rows), "last": R.last_room(), "default_workdir": PROJECT})
        if path == "/api/trash":
            return self._json(200, {"trash": R.list_trash()})
        if path == "/api/devices":
            return self._json(200, {"account": devices.account(),
                                    "devices": devices.listing(getattr(self, "_device_id", ""))})
        if path == "/api/devices/code/status":
            status = devices.code_status((q.get("id") or [""])[0])
            return self._json(200, status) if status is not None else self._json(404, {"error": "接入码不存在"})
        if path == "/api/invites":
            return self._json(200, {"invites": invites.listing(), "guests": invites.guest_listing()})
        if path == "/api/claims":
            return self._json(200, {"claims": guard.listing()})
        if path == "/api/discovery":
            if (q.get("scan") or [""])[0] in ("1", "true", "yes"):
                return self._json(200, discovery.scan())
            discovery.ensure_scanned(async_scan=False)
            return self._json(200, discovery.status())
        if path == "/api/settings/connection":
            return self._json(200, conn_settings.snapshot())
        if path == "/api/agents":
            if (q.get("reload") or [""])[0] in ("1", "true", "yes"):
                registry.load()      # 用户刚改了 data/agents.d：重读一遍，不用重启服务器
            # 待命通道不属于某一种 driver：Claude/Codex 的桌面端也能待命，界面要能看到
            member_rows = {}
            for s in registry.all_specs():
                if s.get("kind") == "user":
                    continue
                st = members.broker.status(s["id"])
                if st["automatic"] or s["driver"] == "external":
                    member_rows[s["id"]] = st
            return self._json(200, dict(registry.catalog(), discovered_presets=registry.discover_hidden_presets(), default_dir=PROJECT, hosts=install.hosts_status(),
                                        local_workspace=self._is_local_owner() and not bool(getattr(self, "_device_id", "")),
                                        owner_devices=devices.listing(getattr(self, "_device_id", "")),
                                        connections=connections.catalog(), members=member_rows,
                                        discovery=discovery.status(), connection_settings=conn_settings.snapshot(),
                                        mcp_url="http://127.0.0.1:%d/mcp" % PORT))
        if path in ("/api/state", "/api/messages"):   # 老页面（服务器换了新版、页面还没刷新）：按最近打开的那场回
            rid = R.last_room()
            r = R.rooms.get(rid) if rid else None
            if r is None:
                return self._json(200, {"meeting": None, "messages": [], "next": 0, "running": [], "queued": 0, "reload": True})
            if path == "/api/state":
                return self._json(200, dict(r.api_state(), reload=True))
            return self._json(200, dict(r.messages_since(_int(q, "since")), reload=True))
        m = ROOM_PATH.match(path)
        if m:
            r = R.rooms.get(unquote(m.group(1)))
            if r is None:
                return self._json(404, {"error": "没有这场会议（可能删了）", "gone": True})
            act = m.group(2)
            if act == "messages":
                return self._json(200, r.messages_since(_int(q, "since")))
            if act == "state":
                return self._json(200, r.api_state())
            if act == "tools":
                ai = (q.get("ai") or [""])[0]
                if ai not in r.ais:
                    return self._json(400, {"error": "ai 只能是这场会议里的：" + "、".join(r.ais)})
                return self._json(200, r.tools_info(ai))
        return self._send(404, b"not found", "text/plain")

    do_HEAD = do_GET

    # ---------- POST ----------
    def do_POST(self):
        path, _, qs = self.path.partition("?")
        if path == "/mcp":
            return self._mcp()
        if path.startswith("/api/guard/"):
            return self._guard(path[len("/api/guard/"):])
        if path == "/api/post":
            return self._external_post()
        if path.startswith("/api/external/member/"):
            data = self._read_json()
            if data is None:
                return
            key = self._bearer()
            pid = access.identify(key)
            if not pid:
                return self._json(401, {"error": "接入密钥无效"})
            try:
                result = members.handle(pid, key, path.rsplit("/", 1)[1], data)
                connections.seen(pid, key, "member", data.get("device"))
                return self._json(200, result)
            except members.MemberError as e:
                return self._json(e.code, {"error": str(e)})
        if path == "/api/external/hello":
            data = self._read_json()
            if data is None:
                return
            key = self._bearer()
            pid = access.identify(key)
            if not pid:
                return self._json(401, {"error": "接入密钥无效或已撤销"})
            connections.seen(pid, key, "web" if data.get("client") == "AI Bridge 网页" else "http", data.get("client"))
            return self._json(200, {"ok": True, "contact": registry.public(registry.get(pid))})
        if path == "/api/guest/redeem":
            data = self._read_json()
            if data is None:
                return
            try:
                result = invites.redeem(str(data.get("invite") or ""),
                                        str(data.get("token") or ""),
                                        str(data.get("device_key") or ""),
                                        str(data.get("name") or ""),
                                        str(data.get("device") or ""))
            except invites.InviteError as error:
                return self._json(error.code, {"error": str(error)})
            return self._json(200, {"ok": True, "token": result["token"],
                                    "contact": result["contact"], "room": result["room"],
                                    "rooms": result["rooms"], "created": result["created"]})
        if path == "/api/devices/enroll":
            data = self._read_json()
            if data is None:
                return
            try:
                result = devices.enroll(str(data.get("code") or ""), str(data.get("label") or ""))
            except devices.DeviceError as error:
                return self._json(error.code, {"error": str(error)})
            return self._json(200, {"ok": True, "token": result["token"], "device": result["device"]},
                              {"Set-Cookie": self._device_cookie_header(result["token"])})
        if path == "/api/devices/restore":
            data = self._read_json()
            if data is None:
                return
            token = str(data.get("token") or "")
            device = devices.verify(token)
            if not device:
                return self._json(401, {"error": "设备凭证无效或已撤销"})
            return self._json(200, {"ok": True}, {"Set-Cookie": self._device_cookie_header(token)})
        if path == "/api/agents/add":         # 页面上"手动添加"，不让人碰 json
            data = self._read_json()
            if data is None:
                return
            if not self._from_browser():
                return self._json(403, {"error": USER_ONLY_MSG})
            try:
                ok, msg = registry.save_agent(data.get("spec") or {})
            except OSError:
                return self._json(500, {"error": "无法保存联系人，请检查应用数据目录的写入权限和剩余空间"})
            return self._json(200 if ok else 400, {"ok": ok, "error": msg, "agents": registry.catalog()["agents"]})
        if path == "/api/agents/remove":
            data = self._read_json()
            if data is None:
                return
            if not self._from_browser():
                return self._json(403, {"error": USER_ONLY_MSG})
            pid = str(data.get("id") or "")
            used = [r["title"] for r in R.rooms.listing() if pid in r.get("participants", [])]
            if used:
                return self._json(409, {"error": "请先从这些会话移除此联系人：" + "、".join(used)})
            ok, msg = registry.remove_agent(str(data.get("id") or ""))
            if ok:
                # Remove any credential even if the same ID is added again later.
                with access.LOCK:
                    from .common import LOG_DIR, read_json, write_json
                    path = os.path.join(LOG_DIR, "contact_keys.json")
                    keys = read_json(path) or {}
                    keys.pop(pid, None)
                    write_json(path, keys)
            return self._json(200 if ok else 409, {"ok": ok, "error": msg})
        if path == "/api/agents/restore":
            data = self._read_json()
            if data is None:
                return
            if not self._from_browser():
                return self._json(403, {"error": USER_ONLY_MSG})
            if not registry.restore_preset(str(data.get("id") or "")):
                return self._json(404, {"error": "未找到已安装的程序，请重新检测"})
            return self._json(200, {"ok": True})
        if path == "/api/agents/install":     # 页面上的"一键接入"，不让人碰命令行
            data = self._read_json()
            if data is None:
                return
            if not self._from_browser():
                return self._json(403, {"error": USER_ONLY_MSG})
            token = str(data.get("token") or "")
            if not token or access.identify(token) != str(data.get("contact") or data.get("host") or ""):
                return self._json(400, {"error": "请先生成此联系人的接入密钥"})
            ok, msg = install.host_install(str(data.get("host") or ""), token)
            return self._json(200 if ok else 409, {"ok": ok, "msg": msg, "hosts": install.hosts_status()})
        if not self._from_browser():
            return self._json(403, {"error": USER_ONLY_MSG})
        m = ROOM_PATH.match(path)
        if m:
            r = R.rooms.get(unquote(m.group(1)))
            if r is None:
                return self._json(404, {"error": "没有这场会议（可能删了）", "gone": True})
            return self._room_post(r, m.group(2), qs)
        if path == "/api/upload" or path == "/api/send":
            return self._json(409, {"error": "页面是旧版的，刷新一下再发", "reload": True})
        data = self._read_json()
        if data is None:
            return
        if path == "/api/update/download":
            if not self._is_local_owner():
                return self._json(403, {"error": "请在运行合拍的电脑上下载更新"})
            from . import updater
            try:
                if data.get('auto_install') is True:
                    return self._json(200, updater.start_download(auto_install=True,
                        busy=lambda: any(row.get('running') or row.get('queued') for row in R.rooms.listing()),
                        stop_backend=lambda: stop_idle_for_update(self.server)))
                return self._json(200, updater.start_download())
            except ValueError as error:
                return self._json(409, {"error": str(error)})
        if path == "/api/update/install":
            if not self._is_local_owner() or getattr(self, "_device_id", ""):
                return self._json(403, {"error": "请在运行合拍的电脑上安装更新"})
            if any(row.get("running") or row.get("queued") for row in R.rooms.listing()):
                return self._json(409, {"error": "还有任务正在运行或排队，请等待完成后再更新"})
            from . import updater
            try:
                return self._json(200, updater.start_install(lambda: stop_idle_for_update(self.server)))
            except ValueError as error:
                return self._json(409, {"error": str(error)})
        if path == "/api/invites":
            room = str(data.get("room") or "")
            r = R.rooms.get(room)
            if r is None or common.is_archived(r.meta.get("status")):
                return self._json(400, {"error": "会话不存在或已归档"})
            try:
                issued = invites.create(room, data.get("permissions"), str(data.get("label") or "游客邀请"),
                                        data.get("expires_days") or 7)
            except invites.InviteError as error:
                return self._json(error.code, {"error": str(error)})
            base = self._base_url(data.get("base")) or getattr(self.server, "lan_url", None) or ""
            url = (base.rstrip("/") + "/join#invite=" + quote(issued["token"])) if base else ""
            return self._json(200, {"ok": True, "token": issued["token"],
                                    "invite": invites.public(issued), "url": url,
                                    "expires": issued["expires"]})
        if path == "/api/invites/revoke":
            try:
                invites.revoke(str(data.get("id") or ""))
            except invites.InviteError as error:
                return self._json(error.code, {"error": str(error)})
            return self._json(200, {"ok": True})
        if path == "/api/guests/remove":
            try:
                return self._json(200, invites.remove_guest(str(data.get("id") or "")))
            except invites.InviteError as error:
                return self._json(error.code, {"error": str(error)})
        if path == "/api/ui/session":
            if getattr(self, "_device_id", ""):
                return self._json(200, {"ok": True, "tracked": False})
            lifecycle = getattr(self.server, "owner_lifecycle", None)
            if lifecycle is not None:
                try:
                    lifecycle.update(data.get("tab"), data.get("action"), data.get("instance"))
                except ValueError as error:
                    return self._json(400, {"error": str(error)})
            return self._json(200, {"ok": True})
        if path == "/api/server/exit":
            self._json(200, {"ok": True})
            threading.Thread(target=lambda: stop_application(self.server), daemon=True).start()
            return
        if path == "/api/server/stop":
            # The launcher uses this only after confirming the same installation/data.
            if any(row.get("running") or row.get("queued") for row in R.rooms.listing()):
                return self._json(409, {"error": "仍有任务运行或排队，请等待结束后再更新"})
            self._json(200, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if path == "/api/devices/code":
            base = self._base_url(data.get("base")) or getattr(self.server, "lan_url", None) or ""
            result = devices.create_code(str(data.get("label") or ""))
            return self._json(200, {"ok": True, "id": result["id"], "code": result["token"], "expires": result["expires"],
                                    "url": (base.rstrip("/") + "/enroll#code=" + quote(result["token"])) if base else ""})
        if path == "/api/devices/revoke":
            try:
                devices.revoke(str(data.get("id") or ""))
            except devices.DeviceError as error:
                return self._json(error.code, {"error": str(error)})
            return self._json(200, {"ok": True})
        if path == "/api/discovery/scan":
            return self._json(200, discovery.scan())
        if path == "/api/discovery/connect":
            ok, res = discovery.connect(str(data.get("id") or ""), data.get("via"))
            return self._json(200 if ok else 409, res)
        if path == "/api/discovery/dismiss":
            return self._json(200, discovery.dismiss(str(data.get("id") or "all")))
        if path == "/api/settings/connection":
            snap = conn_settings.save(
                global_values=data.get("global") if isinstance(data.get("global"), dict) else None,
                pid=str(data.get("id") or "") or None,
                contact_values=data.get("contact") if isinstance(data.get("contact"), dict) else None,
                clear_contact=bool(data.get("clear_contact")),
            )
            return self._json(200, {"ok": True, "settings": snap})
        if path == "/api/members/standby/stop":
            pid = str(data.get("id") or "")
            if not pid:
                return self._json(400, {"error": "缺少成员 id"})
            return self._json(200, members.broker.stop_standby(pid))
        if path == "/api/contacts/key":
            try:
                token = access.credential(str(data.get("id") or ""), data.get("revoke") is True)
                return self._json(200, {"ok": True, "token": token})
            except ValueError as e:
                return self._json(400, {"error": str(e)})
        if path == "/api/contacts/check":
            try:
                return self._json(200, {"ok": True, "connection": connections.check(str(data.get("id") or ""))})
            except ValueError as e:
                return self._json(400, {"error": str(e)})
        if path == "/api/contacts/status":
            pid = str(data.get("id") or "")
            return self._json(200, {"connection": connections.status(pid), "member": members.broker.status(pid)})
        if path == "/api/projects/new":
            if not self._is_local_owner() or getattr(self, "_device_id", ""):
                return self._json(403, {"error": "不能从其他设备指定这台电脑的文件夹；请在运行合拍的设备上创建项目。"})
            try:
                p = projects.create(data.get("name"), data.get("workdir"))
                return self._json(200, {"ok": True, "project": p})
            except ValueError as e:
                return self._json(400, {"error": str(e)})
        if path == "/api/rooms/new":
            if data.get("workdir") and (not self._is_local_owner() or getattr(self, "_device_id", "")):
                return self._json(403, {"error": "不能从其他设备指定这台电脑的文件夹。"})
            ann = str(data.get("announcement", ""))
            if len(ann.encode("utf-8")) > MAX_ANN:
                return self._json(413, {"error": "too large (max 16KB)"})
            pid = str(data.get("project") or "")
            project = next((p for p in projects.listing(R.rooms.listing()) if p["id"] == pid), None)
            if pid and project is None:
                return self._json(400, {"error": "项目不存在"})
            member_ids = data.get("participants")
            if member_ids is not None and (not isinstance(member_ids, list) or any(not isinstance(a, str) or a not in registry.enabled_ids() for a in member_ids)):
                return self._json(400, {"error": "请选择已有且启用的联系人"})
            wd = (project["workdir"] if project else str(data.get("workdir") or "").strip().strip('"')) or PROJECT
            wd = os.path.normpath(os.path.expandvars(os.path.expanduser(wd)))
            if not os.path.isabs(wd) or not os.path.isdir(wd):
                return self._json(400, {"error": "工作文件夹不存在：" + wd})
            r = R.rooms.create(str(data.get("title", "")), ann, wd, pid, member_ids)
            return self._json(200, {"ok": True, "id": r.id})
        if path == "/api/rooms/archive":
            ok, msg = R.set_archived(str(data.get("id", "")), bool(data.get("on")))
        elif path == "/api/rooms/delete":
            ok, msg = R.trash_meeting(str(data.get("id", "")))
        elif path == "/api/trash/restore":
            ok, msg, _new = R.restore_meeting(str(data.get("id", "")))
        elif path == "/api/trash/purge":
            ok, msg = R.purge_all()
        elif path == "/api/claims/release":
            it = guard.force_release(str(data.get("key", "")))
            if it is None:
                return self._json(409, {"ok": False, "error": "已经放开了"})
            r = R.rooms.loaded.get(it["room"])
            if r is not None:
                r.add_message("系统", "你放开了 %s 占着的 %s" % (it["ai"], it["shown"]))
            return self._json(200, {"ok": True})
        elif path == "/api/claims/mine":   # 用户自己占：我在手动改这些，AI 别动
            paths = [os.path.normpath(os.path.expanduser(str(p).strip().strip('"'))) for p in data.get("paths") or [] if str(p).strip()]
            if not paths or not all(os.path.isabs(p) for p in paths):
                return self._json(400, {"error": "填完整路径"})
            res = guard.claim(("", USER), paths, data.get("minutes"), str(data.get("reason") or "用户自己在改"), "")
            return self._json(200, {"ok": True, "claimed": [p for p, ok_, _ in res if ok_],
                                    "blocked": [{"path": p, "by": hit["ai"]} for p, ok_, hit in res if not ok_]})
        else:
            return self._send(404, b"not found", "text/plain")
        return self._json(200 if ok else 409, {"ok": ok, "error": msg})

    def _room_post(self, r, act, qs):
        if act in ('send', 'compact', 'wrapup'):
            from . import updater
            if updater.status().get('state') == 'installing':
                return self._json(409, {"error": "合拍正在更新，请重新打开后再操作"})
        if act == "upload":
            n = self._length()
            if n <= 0:
                return self._json(400, {"error": "empty"})
            if n > MAX_UPLOAD:
                return self._json(413, {"error": "too large (max 25MB)"})
            name = (parse_qs(qs).get("name") or [""])[0]
            try:
                full = r.save_upload(name, self.rfile.read, n)
            except Exception as e:
                return self._json(500, {"error": str(e)})
            return self._json(200, R.make_attachment(full))
        data = self._read_json()
        if data is None:
            return
        if act == "open":   # 页面打开了这场会议：记成"最近打开的"（外部程序不指定会议时发到这里）
            R.set_last(r.id)
            return self._json(200, {"ok": True})
        if act == "send":
            text = str(data.get("text", "")).strip()
            atts = r.clean_attachments(data.get("attachments"))
            if not text and not atts:
                return self._json(400, {"error": "empty"})
            reply = r.make_reply(data.get("reply_to"), data.get("reply_part")) if data.get("reply_to") is not None else None
            R.set_last(r.id)
            r.handle_message("你", text, atts, reply_to=reply)
            return self._json(200, {"ok": True})
        if act == "announcement":
            text = str(data.get("text", ""))
            if len(text.encode("utf-8")) > MAX_ANN:
                return self._json(413, {"error": "too large (max 16KB)"})
            r.write_announcement(text)
            return self._json(200, {"ok": True})
        if act == "board":
            try:
                ids = [int(data.get("id"))] if data.get("id") is not None else []
            except (TypeError, ValueError):
                return self._json(400, {"error": "bad id"})
            res = r.board_op("你", str(data.get("op", "")), str(data.get("kind", "")), str(data.get("text", "")), ids)
            if not res:
                return self._json(400, {"error": "没改成"})
            r.board_note("你", [res])
            return self._json(200, {"ok": True})
        if act == "mode":
            ok, msg = r.set_mode(str(data.get("mode", "")))
            return self._json(200 if ok else 409, {"ok": ok, "error": msg})
        if act in ("undo", "undo/preview"):
            try:
                seq = int(data.get("step"))
            except (TypeError, ValueError):
                return self._json(400, {"ok": False, "error": "bad step"})
            if act == "undo/preview":
                ok, msg, plan = r.undo_preview(seq)
                return self._json(200 if ok else 409, {"ok": ok, "error": msg, "plan": plan})
            ok, msg = r.undo_step(seq)
            return self._json(200 if ok else 409, {"ok": ok, "error": msg})
        if act == "models":
            ok, msg, changed = r.set_models(str(data.get("ai", "")), str(data.get("model") or ""), str(data.get("effort") or ""))
            return self._json(200 if ok else 409, {"ok": ok, "error": msg, "model_changed": changed})
        if act == "tools":
            ok, msg = r.set_tools(str(data.get("ai", "")), data.get("tools"))
            return self._json(200 if ok else 409, {"ok": ok, "error": msg})
        if act == "handoff":
            ok, msg = r.set_handoff(str(data.get("ai", "")), bool(data.get("on")))
            return self._json(200 if ok else 409, {"ok": ok, "error": msg})
        if act == "wrapup":
            ok, msg = r.request_wrapup()
            return self._json(200 if ok else 409, {"ok": ok, "error": msg})
        if act == "stop":
            who = str(data.get("ai") or "")
            if who and who not in r.ais:
                return self._json(400, {"error": "ai 只能是这场会议里的：" + "、".join(r.ais) + "；不填 = 全停"})
            return self._json(200, {"ok": True, "msg": r.stop_ais([who] if who else None)})
        if act == "participants":
            ok, msg = r.set_participants(data.get("participants"))
            return self._json(200 if ok else 409, {"ok": ok, "error": msg, "participants": list(r.ais)})
        if act == "members":
            try:
                ok, msg = r.set_members(data.get("participants"), data.get("permissions"))
            except OSError:
                return self._json(500, {"error": "无法保存会话，请检查数据目录权限或文件是否被占用后重试"})
            return self._json(200 if ok else 409, {"ok": ok, "error": msg})
        if act == "invite":
            pid = str(data.get("contact") or "")
            value = data.get("permissions")
            if not isinstance(value, dict):
                return self._json(400, {"error": "请选择此联系人的会话权限"})
            try:
                with r.lock:
                    ids = list(dict.fromkeys(list(r.ais) + [pid]))
                    perms = {a: access.permissions(r, a) for a in r.ais}
                    perms[pid] = value
                    ok, msg = r.set_members(ids, perms)
            except OSError:
                return self._json(500, {"error": "无法保存会话，请检查数据目录权限或文件是否被占用后重试"})
            return self._json(200 if ok else 409, {"ok": ok, "error": msg})

        if act == "compact":
            who = str(data.get("ai") or "")
            if who and who not in r.ais:
                return self._json(400, {"error": "ai 只能是这场会议里的：" + "、".join(r.ais) + "；不填 = 全压"})
            todo = r.request_compact([who] if who else None)
            if not todo:
                return self._json(409, {"error": "%s已经在压缩了" % (who or "两个都")})
            return self._json(200, {"ok": True, "ais": todo})
        return self._send(404, b"not found", "text/plain")

    def _external_post(self):
        """给外部程序用：桌面应用里的 Claude、终端里的 Claude / Codex 等。room 不填 = 用户最近打开的那场。"""
        from . import updater
        if updater.status().get('state') == 'installing':
            return self._json(409, {"error": "合拍正在更新，请重新打开后再操作"})
        data = self._read_json()
        if data is None:
            return
        speaker = access.identify(self._bearer())
        if not speaker:
            return self._json(401, {"error": "请使用联系人接入密钥"})
        if data.get("speaker") and data["speaker"] != speaker:
            return self._json(403, {"error": "密钥不能冒用其他联系人"})
        rid = str(data.get("room") or "").strip()
        r = R.rooms.get(rid) if rid else None
        if r is None:
            return self._json(409 if not rid else 404, {"error": "room 必填，请先列出受邀会话" if not rid else "没有这场会话：" + rid})
        if not access.allowed(r, speaker, "read") or not access.allowed(r, speaker, "post"):
            return self._json(403, {"error": "未加入此会话或没有发言权限"})
        text = str(data.get("text", "")).strip()
        if not text:
            return self._json(400, {"error": "empty"})
        r.handle_message(speaker, text, origin="external")
        connections.seen(speaker, self._bearer(), "http")
        return self._json(200, {"ok": True, "speaker": speaker, "origin": "external", "room": r.id})

    def _external_get(self, path, q):
        pid = access.identify(self._bearer())
        if not pid:
            return self._json(401, {"error": "接入密钥无效或已撤销"})
        if path == "/api/external/rooms":
            connections.seen(pid, self._bearer(), "http")
            return self._json(200, {"contact": registry.public(registry.get(pid)), "rooms": access.visible_rooms(pid)})
        rid = (q.get("room") or [""])[0]
        r = R.rooms.get(rid) if rid else None
        if r is None or not access.allowed(r, pid):
            return self._json(403, {"error": "没有此会话的读取权限"})
        if path == "/api/external/messages":
            connections.seen(pid, self._bearer(), "http")
            result = r.messages_since(_int(q, "since"))
            # External clients receive text and message metadata, never local file paths.
            result["messages"] = [{k: m[k] for k in ("id", "time", "speaker", "text", "to_user") if k in m} for m in result["messages"] if not m.get("quiet")]
            result["permissions"] = access.permissions(r, pid)
            return self._json(200, result)
        return self._json(404, {"error": "unknown endpoint"})

    def _mcp(self):
        origin = self.headers.get("Origin")
        if origin and not re.match(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$", origin):
            return self._json(403, {"error": "origin not allowed"})   # 防网页借浏览器打本机端口（DNS rebinding）
        n = self._length()
        if n < 0 or n > MAX_BODY * 4:
            return self._json(413, {"error": "too large"})
        code, resp = mcp.handle_http(self.rfile.read(n), self._bearer())
        if resp is None:
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._json(code, resp)

    def _guard(self, kind):
        """编辑前的钩子来问：能不能改这个文件。钥匙 = 会议室起 AI 时给的。"""
        who = R.who_by_key(self._bearer())
        n = self._length()
        if n < 0 or n > MAX_BODY * 16:
            return self._json(413, {"error": "too large"})
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            data = {}
        if who is None:   # 钥匙对不上（会议室重启过之类）：不挡，免得 AI 白白卡住
            return self._json(200, agents.guard_allow(kind))
        r = R.rooms.get(who[0])
        if r is not None and (not access.allowed(r, who[1], "work") or who[1] not in r.ais):
            return self._json(200, {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "此成员没有干活权限"}})
        return self._json(200, agents.guard_decide(kind, r, who[1], data))


def _int(q, name):
    try:
        return max(0, int((q.get(name) or ["0"])[0]))
    except ValueError:
        return 0


class ExclusiveServer(ThreadingHTTPServer):
    """端口独占：已经有会议室在跑时绑定失败，而不是两个进程悄悄共用一个端口。"""
    allow_reuse_address = os.name != "nt"   # Windows 的 SO_REUSEADDR 会允许重复绑定；别的系统上它只管 TIME_WAIT，留着
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        # Keep one UI/backend generation together until restart. Previously an
        # old process hot-served a new UI from disk and silently lost its APIs.
        with open(INDEX, "rb") as f:
            self.ui_index = f.read()
        self.ui_assets = {}
        for name in os.listdir(STATIC):
            if name.endswith((".js", ".css", ".html", ".webmanifest")):
                with open(os.path.join(STATIC, name), "rb") as f:
                    self.ui_assets[name] = f.read()
        super().__init__(*args, **kwargs)

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def stop_application(server):
    """Stop only processes and jobs owned by this application."""
    for room in R.rooms.all():
        try:
            with room.lock:
                busy = bool(room.state["running"] or room.pending or not room.jobs.empty())
            if busy:
                room.stop_ais()
            agents.stop_codex(room)
        except Exception as error:
            print("退出时停止会话失败:", error, file=sys.stderr)
    server.shutdown()


def stop_idle_for_update(server):
    """Do not interrupt work if it started while an update was downloading."""
    if any(row.get('running') or row.get('queued') for row in R.rooms.listing()):
        return False
    def shutdown():
        # Unlike the normal exit action, never stop active work during update.
        for room in R.rooms.all():
            with room.lock:
                if room.state['running'] or room.pending or not room.jobs.empty():
                    return
            agents.stop_codex(room)
        server.shutdown()
    threading.Thread(target=shutdown, daemon=True).start()
    return True


def start_lan_listener(port=None):
    """在同一份 Handler 上再开一个只绑局域网地址的口子。没有局域网或端口被占就 (None, None)。
    管理员接口仍然只认本机页面注入的令牌，所以这个口子对外只到“加入会话”为止。"""
    ip = common.lan_ipv4()
    if not ip:
        return None, None
    port = int(os.environ.get("ROUNDTABLE_LAN_PORT", "8766")) if port is None else port
    try:
        srv = ExclusiveServer((ip, port), Handler)
    except OSError as error:
        print("局域网入口没开成（%s:%s）：%s" % (ip, port, error), file=sys.stderr)
        return None, None
    srv.lan_url = "http://%s:%d" % (ip, srv.server_address[1])
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.lan_url


def start_tailscale_listener(port=None):
    """在 Tailscale 地址上再开一个入口；没有 Tailscale 或端口被占就跳过，不影响其他入口。"""
    ip = common.tailscale_ipv4()
    if not ip:
        return None, None
    port = int(os.environ.get("ROUNDTABLE_TAILSCALE_PORT", "8767")) if port is None else port
    try:
        srv = ExclusiveServer((ip, port), Handler)
    except OSError as error:
        print("Tailscale 入口没开成（%s:%s）：%s" % (ip, port, error), file=sys.stderr)
        return None, None
    srv.tailscale_url = "http://%s:%d" % (ip, srv.server_address[1])
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.tailscale_url


def owner_lifecycle_enabled(env=None):
    env = os.environ if env is None else env
    return env.get("ROUNDTABLE_NO_BROWSER") != "1" or env.get("ROUNDTABLE_OWNER_LIFECYCLE") == "1"


def main():
    url = "http://%s:%d/" % (HOST, PORT)
    try:
        srv = ExclusiveServer((HOST, PORT), Handler)
    except OSError:
        from .server_runtime import reuse_or_stop, show_startup_error
        action, message = reuse_or_stop(url)
        if action == "reuse":
            if os.environ.get("ROUNDTABLE_NO_BROWSER") != "1":
                webbrowser.open(url)
            return
        if action != "stopped":
            show_startup_error(message)
            return
        try:
            srv = ExclusiveServer((HOST, PORT), Handler)
        except OSError:
            show_startup_error("旧后台尚未退出，请稍后重新打开 AI Bridge。")
            return
    lan_srv, lan_url = start_lan_listener()
    ts_srv, tailscale_url = start_tailscale_listener()
    for extra in (srv, lan_srv, ts_srv):
        if extra is not None:
            extra.lan_url = lan_url
            extra.tailscale_url = tailscale_url
    if owner_lifecycle_enabled():
        from .owner_lifecycle import OwnerLifecycle
        srv.owner_lifecycle = OwnerLifecycle(lambda: stop_application(srv))
        for extra in (lan_srv, ts_srv):
            if extra is not None:
                extra.owner_lifecycle = srv.owner_lifecycle
    agents.start_reaper()
    discovery.ensure_scanned(async_scan=True)
    print("会议室已启动：" + url)
    if lan_url:
        print("同一局域网（同一个 WiFi）可扫码或打开：" + lan_url)
    if tailscale_url:
        print("Tailscale 网络可打开：" + tailscale_url)
    if os.environ.get("ROUNDTABLE_NO_BROWSER") != "1":
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        for extra in (lan_srv, ts_srv):
            if extra is not None:
                extra.shutdown()
                extra.server_close()
