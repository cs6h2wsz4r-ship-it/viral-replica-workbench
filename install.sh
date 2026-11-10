#!/bin/bash
# 爆款复刻工作台 · 安装（macOS）
# 解压到哪里，就装在哪里：Python、依赖、模型、FFmpeg 全部放进本文件夹的 runtime/。
# 删除整个文件夹即完全卸载。Windows 请运行 install.bat，两边做的事完全一样。
#
#   ./install.sh               Python 3.12 + faster-whisper + FFmpeg + Whisper 模型
#   ./install.sh --whisperx    另外安装 WhisperX（与 Hypit 相同的逐词对齐，含 PyTorch，约 2GB）
#   ./install.sh --mirror      国内网络：Python / pip / 模型都走国内镜像
#   ./install.sh --model small 指定 Whisper 模型（默认 medium）
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"; RT="$ROOT/runtime"; VENV="$RT/venv"; PY="$VENV/bin/python"
WITH_WHISPERX=0; USE_MIRROR=0; MODEL="medium"
while [ $# -gt 0 ]; do
  case "$1" in
    --whisperx) WITH_WHISPERX=1 ;; --mirror) USE_MIRROR=1 ;; --model) MODEL="$2"; shift ;;
    *) echo "未知参数: $1"; exit 2 ;;
  esac; shift
done

# ---- every download, cache and temp file stays in runtime/ ----
mkdir -p "$RT"/{uv,python,ffmpeg,models/huggingface,models/torch,temp}
export UV_UNMANAGED_INSTALL="$RT/uv" UV_PYTHON_INSTALL_DIR="$RT/python" UV_PYTHON_BIN_DIR="$RT/uv/bin"
export UV_CACHE_DIR="$RT/temp/uv" UV_NO_CONFIG=1 UV_NO_MODIFY_PATH=1
export HF_HOME="$RT/models/huggingface" TORCH_HOME="$RT/models/torch" NLTK_DATA="$RT/models/nltk"
export XDG_CACHE_HOME="$RT/temp/cache" XDG_CONFIG_HOME="$RT/temp/config" TMPDIR="$RT/temp"
export PIP_NO_CACHE_DIR=1 HF_HUB_DISABLE_TELEMETRY=1
PIP_MIRROR="https://pypi.tuna.tsinghua.edu.cn/simple"
HF_MIRROR="https://hf-mirror.com"
PY_MIRROR="https://registry.npmmirror.com/-/binary/python-build-standalone"
if [ "$USE_MIRROR" = 1 ]; then
  export UV_DEFAULT_INDEX="$PIP_MIRROR" UV_PYTHON_INSTALL_MIRROR="$PY_MIRROR" HF_ENDPOINT="$HF_MIRROR"
fi
UV="$RT/uv/uv"
say() { printf '\n==> %s\n' "$*"; }
set_env_line() {  # write KEY=VALUE into this folder's console.env
  local f="$ROOT/console.env"; touch "$f"; chmod 600 "$f"
  if grep -q "^$1=" "$f"; then sed -i '' "s|^$1=.*|$1=$2|" "$f"; else echo "$1=$2" >> "$f"; fi
}
pkg() {  # install packages into runtime/venv, retry once through the domestic mirror
  "$UV" pip install --python "$PY" "$@" || { [ "$USE_MIRROR" = 1 ] && return 1
    echo "官方源失败，改用清华镜像重试"; UV_DEFAULT_INDEX="$PIP_MIRROR" "$UV" pip install --python "$PY" "$@"; }
}

say "1/5 安装 uv 和 Python 3.12（runtime/uv、runtime/python）"
uv_from_pypi() {  # 备用：从 PyPI（官方或清华镜像）下载 uv 的 wheel，取出里面的 uv 程序
  local arch; arch="$(uname -m)"; local tag="macosx_11_0_arm64"; [ "$arch" = "x86_64" ] && tag="macosx_10_12_x86_64"
  local index url
  for index in https://pypi.org/simple https://pypi.tuna.tsinghua.edu.cn/simple; do
    url="$(curl -fsSL --retry 2 "$index/uv/" | grep -oE 'href="[^"]*uv-[0-9.]+-py3-none-'"$tag"'\.whl[^"]*"' | tail -1 | sed -E 's/^href="//; s/"$//; s/#.*//')" || continue
    [ -n "$url" ] || continue
    case "$url" in http*) ;; *) url="$index/uv/$url" ;; esac
    curl -fsSL --retry 2 -o "$RT/temp/uv.whl" "$url" || continue
    mkdir -p "$RT/uv" && unzip -o -j -q "$RT/temp/uv.whl" '*/scripts/uv' -d "$RT/uv" && chmod +x "$UV" && return 0
  done
  return 1
}
if [ ! -x "$UV" ]; then
  curl -LsSf --retry 3 https://astral.sh/uv/install.sh | sh || { echo "官方地址下载 uv 失败，改用 PyPI 下载"; uv_from_pypi; }
fi
[ -x "$UV" ] || { echo "uv 下载失败：网络好的时候再运行一次（已装好的部分不会重复下载）"; exit 1; }
if [ ! -x "$PY" ]; then
  "$UV" python install 3.12 || { [ "$USE_MIRROR" = 1 ] || { echo "改用国内镜像下载 Python"; UV_PYTHON_INSTALL_MIRROR="$PY_MIRROR" "$UV" python install 3.12; }; }
  "$UV" venv --python 3.12 --python-preference only-managed "$VENV"
fi
"$PY" --version

say "2/5 安装 faster-whisper、static-ffmpeg、Pillow（runtime/venv）"
pkg faster-whisper static-ffmpeg pillow numpy

say "3/5 准备 FFmpeg（runtime/ffmpeg）"
"$PY" - "$RT/ffmpeg" <<'PY' || true
import shutil, sys, pathlib, static_ffmpeg.run as r
dst = pathlib.Path(sys.argv[1]); dst.mkdir(parents=True, exist_ok=True)
for src in r.get_or_fetch_platform_executables_else_raise():
    shutil.copy2(src, dst / pathlib.Path(src).name)
print("FFmpeg ->", dst)
PY
if [ -x "$RT/ffmpeg/ffmpeg" ]; then "$RT/ffmpeg/ffmpeg" -version | head -1
elif command -v ffmpeg >/dev/null; then echo "下载失败，先使用本机已有的 FFmpeg：$(command -v ffmpeg)"
else echo "⚠️ FFmpeg 下载失败：网络好的时候重新运行 ./install.sh 即可（已装好的部分不会重复下载）"; fi

say "4/5 下载 Whisper $MODEL 模型（runtime/models）"
fetch() { "$PY" -c "from faster_whisper import WhisperModel; WhisperModel('$MODEL', device='cpu', compute_type='int8'); print('模型就绪')"; }
if ! fetch; then
  [ -n "${HF_ENDPOINT:-}" ] && exit 1
  echo "连不上 Hugging Face，改用镜像 $HF_MIRROR"; export HF_ENDPOINT="$HF_MIRROR"; fetch
fi
[ -n "${HF_ENDPOINT:-}" ] && set_env_line HF_ENDPOINT "$HF_ENDPOINT"
set_env_line ASR_MODEL "$MODEL"

if [ "$WITH_WHISPERX" = 1 ]; then
  say "5/5 安装 WhisperX 与中文对齐模型（runtime/venv、runtime/models）"
  pkg whisperx
  if ! "$PY" "$ROOT/tools/fetch_punkt_tab.py"; then
    echo "警告：punkt_tab 准备失败，工作台会自动回退到 faster-whisper；可稍后重试 WhisperX 安装步骤。"
  fi
  "$PY" -c "import whisperx; whisperx.load_align_model(language_code='zh', device='cpu'); print('中文对齐模型就绪')"
  "$PY" "$ROOT/tools/check_align_models.py"
else
  say "5/5 跳过 WhisperX（需要时运行 ./install.sh --whisperx）"
fi

rm -rf "$RT/temp"; mkdir -p "$RT/temp"
{ echo "installed_at=$(date '+%Y-%m-%d %H:%M:%S')"; echo "platform=macOS"; echo "python=$("$PY" --version 2>&1)"
  "$UV" pip list --python "$PY" 2>/dev/null | grep -Ei '^(faster-whisper|ctranslate2|whisperx|torch|static-ffmpeg) ' || true
  if [ -x "$RT/ffmpeg/ffmpeg" ]; then echo "ffmpeg=$RT/ffmpeg/ffmpeg"; else echo "ffmpeg=missing"; fi; } > "$RT/INSTALLED.txt"
say "完成"; cat "$RT/INSTALLED.txt"
echo "runtime 占用：$(du -sh "$RT" | cut -f1)，全部在 $RT"
echo "启动：双击 start.command（或 ./start.command），然后在网页右上角“设置”里填你的 Key"
