"""Model indexes and requested/accepted/observed settings for the selected endpoint."""
import copy
import hashlib
import json
import os
import threading
import time

from . import agents, execution, registry
from .common import read_json, write_json

LOCK = threading.RLock()
CACHE = {}
TTL = 300


def _key(pid, room=None):
    spec = registry.get(pid) or {}
    fields = {key: spec.get(key) for key in ("driver", "bin", "args", "env", "models", "efforts", "model_efforts")}
    if spec.get("driver") == "acp" and room is not None:
        fields["workdir"] = room.workdir()
    return registry.LOG_DIR, pid, hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def _cached(key):
    with LOCK:
        if key not in CACHE:
            saved=read_json(os.path.join(key[0],"model_catalog.json"),{}) or {}
            row=saved.get(key[1]+":"+key[2],{}) if isinstance(saved,dict) else {}
            CACHE[key]=row if isinstance(row,dict) else {}
        return copy.deepcopy(CACHE[key])


def _store(key, result):
    with LOCK:
        CACHE[key]=result
        path=os.path.join(key[0],"model_catalog.json")
        saved=read_json(path,{}) or {}
        if not isinstance(saved,dict):saved={}
        saved[key[1]+":"+key[2]]=result
        if len(saved)>128:
            saved=dict(list(saved.items())[-128:])
        try:
            write_json(path,saved)
        except OSError:
            CACHE[key]=dict(result,error="索引已获取，但缓存无法保存，请检查应用数据目录",stale=True)


def _flatten(options):
    result = []
    for option in options or []:
        if not isinstance(option, dict):
            continue
        if isinstance(option.get("value"), str):
            result.append(option["value"])
        result.extend(_flatten(option.get("options")))
    return result


def refresh(room, pid):
    spec = registry.get(pid) or {}
    if pid not in room.ais:
        raise ValueError("此 Agent 不在会话中")
    if room.busy_for():
        raise ValueError("会话正在运行，请结束后再刷新模型索引")
    with room.ai_lock(pid):
        if room.busy_for() or execution.binding(room, pid)["phase"] != "ready":
            raise ValueError("会话正在运行或交接，请结束后再刷新模型索引")
        if execution.binding(room, pid)["endpoint"] == "desktop":
            return capabilities(room, pid)
        key = _key(pid, room)
        try:
            if spec.get("driver") == "codex":
                cmd, env = agents.codex_launch(None, "discuss")
                process = agents.CodexServer(cmd, env)
                try:
                    process.start()
                    rows, cursor = [], None
                    for _ in range(20):
                        response = process.request("model/list", {"limit": 100, "cursor": cursor}, timeout=20)
                        rows.extend(response.get("data") or [])
                        cursor = response.get("nextCursor")
                        if not cursor:
                            break
                finally:
                    process.stop()
                models, efforts = [], {}
                for row in rows:
                    if row.get("hidden") is True:
                        continue
                    slug = row.get("model") or row.get("id")
                    if not isinstance(slug, str):
                        continue
                    models.append(slug)
                    efforts[slug] = [item.get("reasoningEffort") or item.get("effort") for item in row.get("supportedReasoningEfforts", []) if isinstance(item, dict)]
                    efforts[slug] = [value for value in efforts[slug] if isinstance(value, str)]
                result = {"models": models, "efforts_by_model": efforts, "source": "Codex model/list"}
            elif spec.get("driver") == "acp":
                from . import acp
                process = None
                try:
                    command = acp._cmd_for(spec)
                    if not command:
                        raise ValueError("找不到 ACP 程序")
                    process = acp.AcpProcess(pid, command, room.workdir(), env=spec.get("env"), init_timeout=12)
                    process.ensure_session(None, [], timeout=15)
                    models, levels = [], []
                    for option in process.config_options:
                        category = option.get("category") or option.get("id")
                        if category == "model":
                            models = _flatten(option.get("options"))
                        if category in ("thought_level", "effort"):
                            levels = _flatten(option.get("options"))
                    result = {"models": models, "efforts_by_model": {model: levels for model in models},
                              "effort_supported": bool(levels), "source": "ACP 会话声明"}
                finally:
                    if process is not None:
                        process.close()
            else:
                # No universal CLI model-enumeration command exists. The form is its explicit index.
                result = {"models": list(spec.get("models") or []),
                          "efforts_by_model": {model: agents.effort_options(pid, model) for model in spec.get("models") or []},
                          "source": "应用内 Agent 设置"}
            if not result["models"]:
                raise ValueError("执行端未返回模型列表，保留上次索引；也可在 Agent 设置中补充")
            result.update(checked_at=time.time(), error="")
            _store(key, result)
        except Exception as error:
            previous = _cached(key)
            previous.update(error=str(error)[:500], stale=True)
            _store(key, previous)
        return capabilities(room, pid)


def capabilities(room, pid):
    base = agents.model_capabilities(pid)
    owner = execution.binding(room, pid)
    if owner["endpoint"] == "desktop":
        from . import native_session
        actual = native_session.observed(pid, room.meta.get(pid + "_session") or "")
        return dict(base, model_supported=False, effort_supported=False, models=[actual["model"]] if actual["model"] else [],
                    efforts_by_model={}, observed=actual, source="桌面原生会话", checked_at=None, stale=False, error="",
                    note="此桌面 MCP 接入支持接单与读取实际模型，未提供模型/effort 写入接口；可继续参与，切换控件受限。")
    cached = _cached(_key(pid, room))
    source = "本机 Codex 缓存" if (registry.get(pid) or {}).get("driver") == "codex" else "应用内 Agent 设置"
    explicit = list((registry.get(pid) or {}).get("models") or [])
    models = list(dict.fromkeys(cached["models"] + explicit)) if cached.get("models") else base["models"]
    effort_map = dict(base["efforts_by_model"], **cached.get("efforts_by_model", {}))
    # Explicit per-model preferences remain editable, including an empty effort list.
    effort_map.update((registry.get(pid) or {}).get("model_efforts") or {})
    model_supported = base["model_supported"] or ((registry.get(pid) or {}).get("driver") == "acp" and bool(cached.get("models")))
    effort_supported = base["effort_supported"] or bool(cached.get("effort_supported"))
    note = "模型列表来自执行端查询；可在 Agent 设置中补充自定义模型，账户权限以实际调用为准。" if cached.get("models") else base["note"]
    return dict(base, note=note, models=models, efforts_by_model=effort_map, source=cached.get("source", source),
                checked_at=cached.get("checked_at"), error=cached.get("error", ""),
                stale=bool(cached.get("error") or (cached.get("checked_at") and time.time() - cached["checked_at"] > TTL)),
                model_supported=model_supported, effort_supported=effort_supported)


def request_id(room, pid):
    model, effort = room.effective_models(pid)
    return hashlib.sha256(json.dumps([model, effort, execution.version(room, pid)]).encode()).hexdigest()[:24]


def state(room, pid):
    owner = execution.binding(room, pid)
    selected = room.effective_models(pid)
    requested = {"model": selected[0], "effort": selected[1], "revision": request_id(room, pid)}
    saved = room.meta.get("model_state", {}).get(pid) or {}
    status = saved.get("status", "pending") if saved.get("request_id") == requested["revision"] else "pending"
    if owner["endpoint"] == "desktop":
        status = "read_only"
    return {"requested": requested, "accepted": saved.get("accepted"), "observed": saved.get("observed"),
            "apply_status": status, "apply_error": saved.get("error", "") if status == "failed" else ""}


def accepted(room, pid, revision, source, observed=None):
    if revision != request_id(room, pid):
        return
    model, effort = room.effective_models(pid)
    with room.lock:
        old = copy.deepcopy(room.meta.get("model_state", {}))
        room.meta.setdefault("model_state", {})[pid] = {"request_id": revision, "status": "applied", "error": "",
            "accepted": {"model": model, "effort": effort, "source": source, "at": time.time()}, "observed": observed}
        try:
            room.save_meta()
        except OSError:
            room.meta["model_state"] = old
            raise


def result(room, pid, revision, ok, text, sid):
    if execution.binding(room, pid)["endpoint"] == "desktop" or revision != request_id(room, pid):
        return
    if ok:
        from . import native_session
        actual = native_session.observed(pid, sid) if native_session.provider(pid) else None
        accepted(room, pid, revision, "使用指定设置的调用已完成", actual)
    elif any(marker in text for marker in ("ACP 拒绝", "设置接口", "model_not_found", "unsupported model", "invalid model")):
        with room.lock:
            row = room.meta.setdefault("model_state", {}).setdefault(pid, {})
            row.update(request_id=revision, status="failed", error=text[:500])
            room.save_meta()
