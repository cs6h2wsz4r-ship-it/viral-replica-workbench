#!/bin/bash
# 双击启动爆款复刻工作台（只使用本文件夹 runtime/ 里的环境）
cd "$(dirname "$0")"
PORT=8765
if [ ! -f runtime/INSTALLED.txt ] || [ ! -x runtime/venv/bin/python ]; then
  echo "第一次启动：自动安装 Python、依赖、FFmpeg 和拆解模型，全部装在本文件夹 runtime/ 里（约 10 分钟，只装一次）"
  if ! bash ./install.sh "$@"; then echo "安装没完成。网络好的时候再双击一次 start.command，已装好的部分不会重复下载。"; read -n1 -p "按任意键退出"; exit 1; fi
fi
# 先关掉还在运行的旧工作台（只关这个文件夹的 server.py，或占着 8765 端口的 server.py）
OLD=""
[ -f runtime/server.pid ] && OLD="$(cat runtime/server.pid)"
OLD="$OLD $(lsof -ti tcp:$PORT -sTCP:LISTEN 2>/dev/null)"
for pid in $OLD; do
  if ps -p "$pid" -o command= 2>/dev/null | grep -q "server.py"; then
    echo "关闭旧的工作台进程 $pid"; kill "$pid" 2>/dev/null
  fi
done
for i in 1 2 3 4 5 6 7 8 9 10; do lsof -ti tcp:$PORT -sTCP:LISTEN >/dev/null 2>&1 || break; sleep 0.5; done
if lsof -ti tcp:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  echo "端口 $PORT 被别的程序占用（不是工作台），请先关闭它。"; read -n1 -p "按任意键退出"; exit 1
fi
echo "正在检查更新…"
runtime/venv/bin/python updater.py --auto || true
export PATH="$PWD/runtime/ffmpeg:$PATH"
(sleep 1.5 && open "http://127.0.0.1:$PORT") &
exec runtime/venv/bin/python server.py
