"""Local transcription with faster-whisper (medium, int8). Optional dependency.

Install on your own computer:  pip3 install faster-whisper
The first run downloads the model (~1.5 GB) into the Hugging Face cache.
"""
from __future__ import annotations

import math
import re
import threading
import wave
from pathlib import Path

from . import align_models

_model = None
_model_key = None
_lock = threading.Lock()


def available() -> bool:
    return bool(engines())


def engines() -> list[str]:
    found = []
    for name, mod in (("whisperx", "whisperx"), ("faster-whisper", "faster_whisper")):
        try:
            __import__(mod)
            found.append(name)
        except Exception:
            pass
    return found


def transcribe_best(wav: Path, model_size: str, compute_type: str, language: str | None, engine: str = "auto", *,
                    local_only: bool = False) -> dict:
    """auto: WhisperX (same as `hypit transcribe`, forced word alignment) when installed, else faster-whisper."""
    options = {"local_only": True} if local_only else {}
    have = engines()
    if engine in ("auto", "whisperx") and "whisperx" in have:
        try:
            return transcribe_whisperx(wav, model_size, compute_type, language, **options)
        except Exception as exc:
            if engine != "auto" or "faster-whisper" not in have:
                raise
            result = transcribe(wav, model_size, compute_type, language, **options)
            result["fallback_reason"] = f"WhisperX 转录失败：{type(exc).__name__}: {exc}"
            return result
    if "faster-whisper" in have:
        return transcribe(wav, model_size, compute_type, language, **options)
    raise RuntimeError("本机没有安装 faster-whisper / whisperx")


def transcribe_whisperx(wav: Path, model_size: str = "medium", compute_type: str = "int8",
                        language: str | None = None, *, local_only: bool = False) -> dict:
    options = {"local_only": True} if local_only else {}
    import nltk
    import whisperx  # type: ignore
    from whisperx.utils import PUNKT_LANGUAGES

    nltk_root = Path(__file__).resolve().parents[1] / "runtime/models/nltk"
    try:
        nltk.data.find("tokenizers/punkt_tab/", paths=[str(nltk_root)])
    except LookupError as exc:
        raise RuntimeError("缺少 NLTK punkt_tab，请重新运行安装脚本的 WhisperX 步骤") from exc
    result = transcribe(wav, model_size, compute_type, language, **options)
    lang = result.get("language") or language
    model_info = align_models.check_align_model(lang)
    if model_info["status"] != "ok":
        raise RuntimeError(align_models.unavailable_reason(model_info))
    # Check the same tokenizer WhisperX will use, before it can try nltk.download.
    punkt_lang = PUNKT_LANGUAGES.get(lang, "english")
    try:
        nltk.data.find(f"tokenizers/punkt_tab/{punkt_lang}/", paths=[str(nltk_root)])
        nltk.data.load(f"tokenizers/punkt_tab/{punkt_lang}.pickle")
    except (LookupError, OSError, ValueError) as exc:
        raise RuntimeError("缺少 NLTK punkt_tab，请重新运行安装脚本的 WhisperX 步骤") from exc
    audio = whisperx.load_audio(str(wav))
    options = {"language_code": lang, "device": "cpu", "model_cache_only": True}
    if model_info["type"] == "torchaudio":
        options["model_dir"] = str(align_models.CHECKPOINTS_DIR)
    try:
        with align_models.block_torch_downloads():
            align_model, meta = whisperx.load_align_model(**options)
    except Exception as exc:
        broken = {**model_info, "status": "broken"}
        raise RuntimeError(f"{align_models.unavailable_reason(broken)}；加载失败：{exc}") from exc
    segs = []
    aligned_count = 0
    for seg in result["segments"]:
        # Align one original sentence at a time so split subsegments cannot migrate
        # into a neighbour (even when source sentence timestamps overlap).
        sentence = {k: seg[k] for k in ("start", "end", "text")}
        aligned = whisperx.align([sentence], align_model, meta, audio, "cpu", return_char_alignments=False)
        parts = aligned.get("segments", [])
        words = [w for part in parts for w in part.get("words", [])]
        valid = words and all(part.get("words") for part in parts) and all(
            isinstance(w.get(k), (int, float)) and math.isfinite(w[k])
            for w in words for k in ("start", "end"))
        if not valid or any(w["end"] < w["start"] for w in words):
            segs.append(seg)
            continue
        words = [{"word": w.get("word", ""), "start": round(w["start"], 3), "end": round(w["end"], 3)}
                 for w in sorted(words, key=lambda w: (w["start"], w["end"]))]
        segs.append({**seg, "start": min(w["start"] for w in words), "end": max(w["end"] for w in words),
                     "words": words})
        aligned_count += 1
    output = {"engine": f"faster-whisper {model_size} {compute_type} + wav2vec2 alignment", "language": lang,
              "timing": "forced_alignment", "segments": segs}
    if aligned_count < len(segs):
        output["aligned_ratio"] = aligned_count / len(segs)
    return output


def transcribe(wav: Path, model_size: str = "medium", compute_type: str = "int8", language: str | None = None,
               *, local_only: bool = False) -> dict:
    global _model, _model_key
    from faster_whisper import WhisperModel  # type: ignore
    import numpy as np

    with wave.open(str(wav), "rb") as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (16000, 1, 2):
            raise ValueError("转录音频必须是 16kHz 单声道 PCM16 WAV")
        audio = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32) / 32768.0

    with _lock:
        key = (model_size, compute_type)
        if _model is None or _model_key != key:
            try:
                _model = WhisperModel(model_size, device="auto", compute_type=compute_type, local_files_only=True)
            except Exception:
                if local_only:
                    raise
                _model = WhisperModel(model_size, device="auto", compute_type=compute_type)
            _model_key = key
        segments, info = _model.transcribe(
            audio, language=(language or "").strip() or None, vad_filter=True, word_timestamps=True)
        detected_language = info.language
        if detected_language == "zh":
            # Detection happens before the lazy segment iterator is consumed. Only
            # decode Chinese with the punctuation prompt once its language is known.
            segments, _ = _model.transcribe(
                audio, language=detected_language, vad_filter=True, word_timestamps=True,
                initial_prompt="以下是普通话的句子，请输出简体中文并加标点。")
        out = []
        for seg in segments:
            words = [{"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word.strip()}
                     for w in (seg.words or [])]
            out.append({"start": round(seg.start, 2), "end": round(seg.end, 2), "text": seg.text.strip(),
                        "words": words})
    return {"engine": f"faster-whisper {model_size} {compute_type}", "language": detected_language,
            "timing": "asr_estimate", "segments": out}


_SRT_TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def parse_srt(text: str) -> dict:
    """Accept an SRT the user already has (e.g. exported from 剪映)."""
    segs = []
    for block in re.split(r"\n\s*\n", text.replace("\r", "")):
        lines = [l for l in block.strip().split("\n") if l.strip()]
        for i, line in enumerate(lines):
            m = _SRT_TIME.search(line)
            if m:
                g = [int(x) for x in m.groups()]
                start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
                end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
                body = " ".join(lines[i + 1:]).strip()
                if body:
                    segs.append({"start": round(start, 2), "end": round(end, 2), "text": body, "words": []})
                break
    if not segs:
        raise ValueError("没识别出 SRT 时间轴")
    return {"engine": "user_srt", "language": None, "timing": "user_supplied", "segments": segs}
