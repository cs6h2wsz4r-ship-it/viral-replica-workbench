"""Probe Jimmy's /images/understand with text, one image and a short video, using the key in console.env.
Prints only status codes and short messages (never the key).
usage: python tools/test_jimmy_gemini.py [--chat] [model ...]
"""
import argparse
import base64
import json
import ssl
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import runtime_env  # noqa: E402

runtime_env.apply()

from console import config, llm  # noqa: E402

parser = argparse.ArgumentParser(description="测试吉米图像理解或 Chat Completions")
parser.add_argument("--chat", action="store_true", help="用一张图测试 JIMMY_LLM_MODEL 的 Chat Completions 接口")
parser.add_argument("models", nargs="*")
args = parser.parse_args()

key = config._parse(config.ENV_FILE).get("JIMMY_API_KEY", "")
if not key:
    sys.exit("console.env 里没有 JIMMY_API_KEY")
llm._jimmy_dns_shim()
URL = config.get("JIMMY_LLM_BASE_URL").rstrip("/") + ("/chat/completions" if args.chat else "/images/understand")
models = [config.get("JIMMY_LLM_MODEL")] if args.chat else (args.models or ["gemini-3.5-flash"])


def post(body):
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180, context=llm._ssl_context()) as r:
            raw = r.read().decode()
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read().decode("utf-8", "replace"), e.code
    except Exception as e:  # network
        return f"连接失败 {e}"
    try:
        d = json.loads(raw)
    except ValueError:
        return f"HTTP {status} 非 JSON：{raw[:160]}"
    code, msg = d.get("code"), d.get("msg")
    text = ""
    try:
        text = (d["choices"][0]["message"]["content"] if args.chat else
                d["data"]["candidates"][0]["content"]["parts"][0]["text"])[:60].replace("\n", " ")
    except Exception:
        pass
    return f"HTTP {status} code={code} msg={msg} 回答={text!r}"


tmp = Path(tempfile.mkdtemp())
img = tmp / "f.jpg"
vid = tmp / "v.mp4"
src = next((ROOT / "projects").glob("*/source/source.*"), None)
if src:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "1", "-i", str(src), "-frames:v", "1", "-vf", "scale=320:-2", str(img)])
    if not args.chat:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-t", "4", "-vf", "scale=240:-2,fps=2", "-c:v", "libx264",
                        "-crf", "32", "-an", str(vid)])
if args.chat and not img.exists():
    sys.exit("没有可用的参考图，请先在项目中上传参考视频")
for m in models:
    print(f"== {m}")
    if args.chat:
        print(" 单张图:", post({"model": m, "messages": [{"role": "user", "content":
              [{"type": "text", "text": "用一句话描述这张图"}] + llm.image_part(img)}],
              "temperature": 0.2, "max_tokens": 1000}))
        continue
    print(" 纯文字:", post({"model": m, "contents": [{"role": "user", "parts": [{"text": "用一句话回答：1+1 等于几"}]}]}))
    if img.exists():
        b = base64.b64encode(img.read_bytes()).decode()
        print(" 单张图:", post({"model": m, "contents": [{"role": "user", "parts": [
            {"inline_data": {"mime_type": "image/jpeg", "data": b}}, {"text": "用一句话描述这张图"}]}]}))
    if vid.exists():
        b = base64.b64encode(vid.read_bytes()).decode()
        print(" 4秒视频:", post({"model": m, "contents": [{"role": "user", "parts": [
            {"inline_data": {"mime_type": "video/mp4", "data": b}}, {"text": "用一句话描述这段视频"}]}]}))
