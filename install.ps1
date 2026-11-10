# 爆款复刻工作台 · 安装（Windows）
# 解压到哪里，就装在哪里：Python、依赖、模型、FFmpeg 全部放进本文件夹的 runtime\。
# 删除整个文件夹即完全卸载。macOS 请运行 install.sh，两边做的事完全一样。
#
#   install.bat                Python 3.12 + faster-whisper + FFmpeg + Whisper 模型
#   install.bat -WhisperX      另外安装 WhisperX（含 PyTorch，约 2GB）
#   install.bat -Mirror        国内网络：Python / pip / 模型都走国内镜像
#   install.bat -Model small   指定 Whisper 模型（默认 medium）
param([switch]$WhisperX, [switch]$Mirror, [string]$Model = "medium")
$ErrorActionPreference = "Continue"   # native tools report through exit codes (checked by Run)
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Root = $PSScriptRoot; $RT = Join-Path $Root "runtime"; $Venv = Join-Path $RT "venv"
$Py = Join-Path $Venv "Scripts\python.exe"; $UV = Join-Path $RT "uv\uv.exe"
foreach ($d in "uv","python","ffmpeg","models\huggingface","models\torch","temp") { New-Item -ItemType Directory -Force -Path (Join-Path $RT $d) | Out-Null }

# ---- every download, cache and temp file stays in runtime\ ----
$env:UV_UNMANAGED_INSTALL = Join-Path $RT "uv"; $env:UV_PYTHON_INSTALL_DIR = Join-Path $RT "python"
$env:UV_PYTHON_BIN_DIR = Join-Path $RT "uv\bin"; $env:UV_CACHE_DIR = Join-Path $RT "temp\uv"
$env:UV_NO_CONFIG = "1"; $env:UV_NO_MODIFY_PATH = "1"
$env:HF_HOME = Join-Path $RT "models\huggingface"; $env:TORCH_HOME = Join-Path $RT "models\torch"
$env:NLTK_DATA = Join-Path $RT "models\nltk"; $env:XDG_CACHE_HOME = Join-Path $RT "temp\cache"
$env:TEMP = Join-Path $RT "temp"; $env:TMP = $env:TEMP
$env:PIP_NO_CACHE_DIR = "1"; $env:HF_HUB_DISABLE_TELEMETRY = "1"; $env:PYTHONUTF8 = "1"
$PipMirror = "https://pypi.tuna.tsinghua.edu.cn/simple"; $HfMirror = "https://hf-mirror.com"
$PyMirror = "https://registry.npmmirror.com/-/binary/python-build-standalone"
if ($Mirror) { $env:UV_DEFAULT_INDEX = $PipMirror; $env:UV_PYTHON_INSTALL_MIRROR = $PyMirror; $env:HF_ENDPOINT = $HfMirror }

function Say($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Run([scriptblock]$b) { & $b; if ($LASTEXITCODE -ne 0) { throw "命令失败（退出码 $LASTEXITCODE）" } }
function Set-EnvLine($k, $v) {
  $f = Join-Path $Root "console.env"; if (-not (Test-Path $f)) { New-Item $f -ItemType File | Out-Null }
  $lines = @(Get-Content $f -Encoding UTF8 | Where-Object { $_ -notmatch "^$k=" }) + "$k=$v"
  [IO.File]::WriteAllLines($f, [string[]]$lines)   # UTF-8 without BOM
}
function Pkg([string[]]$names) {
  & $UV pip install --python $Py @names
  if ($LASTEXITCODE -ne 0) {
    if ($Mirror) { throw "安装失败：$names" }
    Write-Host "官方源失败，改用清华镜像重试"; $env:UV_DEFAULT_INDEX = $PipMirror
    Run { & $UV pip install --python $Py @names }
  }
}

Say "1/5 安装 uv 和 Python 3.12（runtime\uv、runtime\python）"
if (-not (Test-Path $UV)) { Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression }
if (-not (Test-Path $Py)) {
  & $UV python install 3.12
  if ($LASTEXITCODE -ne 0 -and -not $Mirror) { Write-Host "改用国内镜像下载 Python"; $env:UV_PYTHON_INSTALL_MIRROR = $PyMirror; Run { & $UV python install 3.12 } }
  Run { & $UV venv --python 3.12 --python-preference only-managed $Venv }
}
Run { & $Py --version }

Say "2/5 安装 faster-whisper、static-ffmpeg（runtime\venv）"
Pkg @("faster-whisper", "static-ffmpeg", "pillow", "numpy")

Say "3/5 准备 FFmpeg（runtime\ffmpeg）"
$ffDir = Join-Path $RT "ffmpeg"
& $Py -c "import shutil,sys,pathlib,static_ffmpeg.run as r; d=pathlib.Path(sys.argv[1]); [shutil.copy2(s, d/pathlib.Path(s).name) for s in r.get_or_fetch_platform_executables_else_raise()]; print('FFmpeg ->', d)" $ffDir
if (Test-Path (Join-Path $ffDir "ffmpeg.exe")) { & (Join-Path $ffDir "ffmpeg.exe") -hide_banner -version | Select-Object -First 1 }
elseif (Get-Command ffmpeg -ErrorAction SilentlyContinue) { Write-Host "下载失败，先使用本机已有的 FFmpeg：$((Get-Command ffmpeg).Source)" -ForegroundColor Yellow }
else { Write-Host "FFmpeg 下载失败：网络好的时候重新运行 install.bat 即可（其余已装好的部分不会重复下载）" -ForegroundColor Yellow }

Say "4/5 下载 Whisper $Model 模型（runtime\models）"
$fetch = "from faster_whisper import WhisperModel; WhisperModel('$Model', device='cpu', compute_type='int8'); print('模型就绪')"
& $Py -c $fetch
if ($LASTEXITCODE -ne 0) {
  if ($env:HF_ENDPOINT) { throw "模型下载失败" }
  Write-Host "连不上 Hugging Face，改用镜像 $HfMirror"; $env:HF_ENDPOINT = $HfMirror; Run { & $Py -c $fetch }
}
if ($env:HF_ENDPOINT) { Set-EnvLine "HF_ENDPOINT" $env:HF_ENDPOINT }
Set-EnvLine "ASR_MODEL" $Model

if ($WhisperX) {
  Say "5/5 安装 WhisperX 与中文对齐模型（runtime\venv、runtime\models）"
  Pkg @("whisperx")
  & $Py (Join-Path $Root "tools/fetch_punkt_tab.py")
  if ($LASTEXITCODE -ne 0) { Write-Warning "punkt_tab 准备失败，工作台会自动回退到 faster-whisper；可稍后重试 WhisperX 安装步骤。" }
  Run { & $Py -c "import whisperx; whisperx.load_align_model(language_code='zh', device='cpu'); print('中文对齐模型就绪')" }
  Run { & $Py (Join-Path $Root "tools/check_align_models.py") }
} else { Say "5/5 跳过 WhisperX（需要时运行 install.bat -WhisperX）" }

Remove-Item -Recurse -Force (Join-Path $RT "temp") -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path (Join-Path $RT "temp") | Out-Null
$ffNote = if (Test-Path "$ffDir\ffmpeg.exe") { "$ffDir\ffmpeg.exe" } else { "missing" }
$info = @("installed_at=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')", "platform=Windows", "python=$(& $Py --version 2>&1)") +
  (& $UV pip list --python $Py 2>$null | Select-String -Pattern '^(faster-whisper|ctranslate2|whisperx|torch|static-ffmpeg) ') +
  @("ffmpeg=$ffNote")
Set-Content -Path (Join-Path $RT "INSTALLED.txt") -Value $info -Encoding UTF8
Say "完成"; Get-Content (Join-Path $RT "INSTALLED.txt")
$mb = [math]::Round((Get-ChildItem $RT -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)
Write-Host "runtime 占用：$mb MB，全部在 $RT"
Write-Host "启动：双击 start.bat，然后在网页右上角“设置”里填你的 Key"
