#!/bin/sh
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "需要 Python 3.11+（带 Tk）才能运行 AI Bridge 成员连接端。"
  read -r answer
  exit 1
fi
python3 -m bridge.member_ui
if [ "$?" -ne 0 ]; then
  echo "启动失败，请确认已安装 Python 3.11+ 和 Tk。"
  read -r answer
fi
