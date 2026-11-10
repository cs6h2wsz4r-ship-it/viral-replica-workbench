"""Real local ASR acceptance check for CODEX_TASKS_3.md; no paid services."""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from console import runtime_env
    runtime_env.apply()
    # The acceptance run must use installed models, never download replacements.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    from console import asr
    with tempfile.TemporaryDirectory(prefix="asr-round3-", dir=ROOT / "runtime/temp") as tmp:
        wav = Path(tmp) / "omni-60-75.wav"
        subprocess.run([str(ROOT / "runtime/ffmpeg/ffmpeg"), "-v", "error", "-ss", "60", "-i",
                        str(Path.home() / "workspace/douyin/omni.mp4"), "-t", "15", "-vn", "-ar", "16000",
                        "-ac", "1", "-c:a", "pcm_s16le", str(wav)], check=True)
        print("开始 auto 转录", flush=True)
        result = asr.transcribe_best(wav, "medium", "int8", "zh", "auto")
        print("开始单独 faster-whisper 转录", flush=True)
        baseline = asr.transcribe(wav, "medium", "int8", "zh")
        print(json.dumps({"auto": result, "baseline": baseline}, ensure_ascii=False, indent=2), flush=True)
        assert "wav2vec2 alignment" in result["engine"]
        assert "fallback_reason" not in result
        assert result["timing"] == "forced_alignment"
        assert len(result["segments"]) >= 2
        assert [s["text"] for s in result["segments"]] == [s["text"] for s in baseline["segments"]]
        assert all(re.search(r"[，。！？；：、]", s["text"]) for s in result["segments"])
        assert all(s["words"] and all(w.get("start") is not None for w in s["words"]) for s in result["segments"])
    assert not wav.exists()
    print("真实转录自检通过；临时 WAV 已删除", flush=True)


if __name__ == "__main__":
    main()
