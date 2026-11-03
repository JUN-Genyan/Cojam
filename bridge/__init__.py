# -*- coding: utf-8 -*-
"""AI Bridge：本机的多会议室服务（用户 + Claude Code + Codex）。只用标准库，只绑 127.0.0.1。

模块：
- common     路径、上限、规矩文本、小工具
- registry   参与者注册表：能有哪些 AI、每一个怎么叫（agents.d 配置，见 docs/PROVIDERS.md）
- workspace  干活模式的存档点、改动清单、撤回（git）
- guard      文件护栏：谁占着哪个文件，跨会议室
- drivers    通用驱动：cli（起命令行）/ http（POST 给自己的服务）
- agents     内置的两个专门驱动：claude（每轮一个 claude -p）、codex（每个会议室一个 codex app-server）
- room       一场会议 = 一个 Room：消息、排队、叫 AI、看板、压缩、停……；会议目录的增删
- mcp        会议室自己的 MCP 服务（给会议里的 AI 和外面的 AI 用）
- web        HTTP 接口和启动
"""
from . import registry as _registry

try:      # 先把注册表读进来：common.AIS 会被原地改成当前启用的列表
    _registry.load()
except Exception:   # 配置坏了也不该让整个会议室起不来（reg.start 里会再试一次，界面上能看到错）
    pass
