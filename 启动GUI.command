#!/bin/bash
# 双击启动「音频 → 纯黑屏 mp4」图形界面
# 注意：托管 venv 的 python 缺 tcl9.0，tkinter 起不来，故优先级里跳过 venv，
#       先试托管基础解释器，再退回系统/brew 解释器。
cd "$(dirname "$0")" || exit 1

# tkinterdnd2 装在托管 venv 里，而 GUI 跑在托管基础解释器上（venv 的 Tk 是坏的），
# 用 PYTHONPATH 把 venv 的 site-packages 挂进去，这样拖拽库能被 import 到。
export PYTHONPATH="/Users/czjoker/.workbuddy/binaries/python/envs/default/lib/python3.13/site-packages${PYTHONPATH:+:$PYTHONPATH}"

CANDIDATES=(
  "/Users/czjoker/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
  "/opt/homebrew/bin/python3"
  "/usr/bin/python3"
  "/Users/czjoker/anaconda3/bin/python3"
)

for P in "${CANDIDATES[@]}"; do
  if [ -x "$P" ] && "$P" -c "import tkinter as t; r=t.Tk(); r.withdraw(); r.destroy()" >/dev/null 2>&1; then
    exec "$P" mp3_to_mp4_gui.py
  fi
done

echo "未找到可用的 Python（需要带可用的 tkinter/Tcl）"
read -n 1 -s -r -p "按任意键关闭..."
