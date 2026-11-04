"""Small cross-platform member connector window (Python + Tk, no VPN driver)."""
import json
import os
from pathlib import Path
import queue
import sys
import threading

from .member import Client, LocalExecutor, configure_state


def main():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    state = configure_state(os.environ.get("AI_BRIDGE_MEMBER_STATE"))
    if sys.platform == "darwin":
        paths = [str(Path.home() / ".local/bin"), str(Path.home() / ".npm-global/bin"), "/opt/homebrew/bin", "/usr/local/bin"]
        os.environ["PATH"] = os.pathsep.join([p for p in paths if os.path.isdir(p)] + [os.environ.get("PATH", "")])
    from . import registry
    root = tk.Tk()
    root.title("合拍 Cojam · 加入会议室")
    root.geometry("650x640")
    root.minsize(500, 530)
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="把这台设备的 Harness 加入会议室", font=("", 16, "bold")).pack(anchor="w")
    ttk.Label(frame, text="收到 @ 后自动执行并回复，同一会话的成员无需运行在同一台电脑。", wraplength=580).pack(anchor="w", pady=(8, 16))
    server = tk.StringVar(value=os.environ.get("AI_BRIDGE_SERVER", ""))
    key = tk.StringVar(value=os.environ.get("AI_BRIDGE_TOKEN", ""))
    directory = tk.StringVar(value=str(Path.home()))
    agent = tk.StringVar()
    allow_work = tk.BooleanVar(value=False)
    controls = []

    def field(label, value, show=None):
        ttk.Label(frame, text=label).pack(anchor="w", pady=(8, 4))
        entry = ttk.Entry(frame, textvariable=value, show=show or "")
        entry.pack(fill="x")
        controls.append(entry)
        return entry

    field("会议室服务地址（使用可达的 HTTPS 或私有网络地址）", server)
    field("此成员的接入密钥（在会议室服务的「接入」中生成）", key, "•")
    def import_connection():
        path = filedialog.askopenfilename(title="导入会议室连接配置", filetypes=[("JSON", "*.json")])
        if path:
            try:
                config = json.loads(Path(path).read_text(encoding="utf-8-sig"))
                Client(config["server"], config["key"])  # Validate only; never auto-run commands from an imported file.
                server.set(config["server"])
                key.set(config["key"])
            except Exception:
                messagebox.showerror("无法导入", "请选择会议室导出的连接配置，其中应包含 server 和 key")
    import_button = ttk.Button(frame, text="导入会议室连接配置", command=import_connection)
    import_button.pack(anchor="e", pady=4)
    controls.append(import_button)
    field("本机工作文件夹（不会同步另一台电脑的文件）", directory)
    browse = ttk.Button(frame, text="选择工作文件夹", command=lambda: directory.set(filedialog.askdirectory() or directory.get()))
    browse.pack(anchor="e", pady=4)
    controls.append(browse)
    ttk.Label(frame, text="本机 Harness").pack(anchor="w", pady=(8, 4))
    combo = ttk.Combobox(frame, textvariable=agent, state="readonly")
    combo.pack(fill="x")
    controls.append(combo)

    def refresh():
        registry.load()
        ids = [s["id"] for s in registry.all_specs() if s["enabled"] and s["driver"] != "external"]
        combo["values"] = ids
        if agent.get() not in ids:
            agent.set(ids[0] if ids else "")

    def import_config():
        path = filedialog.askopenfilename(title="导入本机 CLI / HTTP 驱动", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            if not isinstance(raw, dict) or raw.get("driver") not in ("cli", "http"):
                raise ValueError("请选择一个 driver 为 cli 或 http 的本机驱动 JSON 对象")
            ok, error = registry.save_agent(raw)
            if not ok:
                raise ValueError(error)
            refresh()
            agent.set(raw["id"])
        except Exception as e:
            messagebox.showerror("导入失败", str(e))

    row = ttk.Frame(frame)
    row.pack(fill="x", pady=6)
    detect = ttk.Button(row, text="重新检测", command=refresh)
    detect.pack(side="left")
    imp = ttk.Button(row, text="导入自定义 CLI / HTTP 配置", command=import_config)
    imp.pack(side="left", padx=8)
    controls += [detect, imp]
    permission = ttk.Checkbutton(frame, text="允许在本机干活（仍需会话授予权限；默认只讨论）", variable=allow_work)
    permission.pack(anchor="w", pady=10)
    controls.append(permission)
    state_label = tk.StringVar(value="尚未连接")
    ttk.Label(frame, textvariable=state_label, wraplength=580).pack(anchor="w", pady=8)
    log = tk.Text(frame, height=6, wrap="word", state="disabled")
    log.pack(fill="both", expand=True)
    events = queue.Queue()
    current = {"client":None, "thread":None}
    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(14, 0))

    def start():
        if current["thread"] and current["thread"].is_alive():
            return
        try:
            client = Client(server.get().strip(), key.get(), workdir=directory.get(), allow_work=allow_work.get(), emit=events.put)
            executor = LocalExecutor(client, agent.get(), directory.get(), state)
        except Exception as e:
            messagebox.showerror("无法连接", str(e))
            return
        current["client"] = client
        for item in controls:
            item.configure(state="disabled")
        connect.configure(state="disabled")
        stop.configure(state="normal")
        state_label.set("正在连接会议室…")

        def run():
            try:
                client.run(executor)
            except Exception:
                events.put("连接端发生错误，请检查配置与网络后重新连接。")
            finally:
                events.put(None)

        current["thread"] = threading.Thread(target=run, daemon=True)
        current["thread"].start()

    def halt():
        if current["client"]:
            current["client"].stop()
            state_label.set("正在停止成员连接和当前任务…")

    connect = ttk.Button(buttons, text="连接并开启自动响应", command=start)
    connect.pack(side="right")
    stop = ttk.Button(buttons, text="停止连接", command=halt, state="disabled")
    stop.pack(side="right", padx=8)

    def pump():
        try:
            while True:
                item = events.get_nowait()
                if item is None:
                    for control in controls:
                        control.configure(state="readonly" if control is combo else "normal")
                    connect.configure(state="normal")
                    stop.configure(state="disabled")
                    continue
                state_label.set(item)
                log.configure(state="normal")
                log.insert("end", str(item) + "\n")
                log.see("end")
                log.configure(state="disabled")
        except queue.Empty:
            pass
        root.after(150, pump)

    def closing():
        halt()

        def wait():
            if current["thread"] and current["thread"].is_alive():
                root.after(200, wait)
            else:
                root.destroy()
        wait()

    refresh()
    root.protocol("WM_DELETE_WINDOW", closing)
    pump()
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
