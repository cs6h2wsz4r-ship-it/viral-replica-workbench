"""Steps 01–04: upload, Hypit-style breakdown, Treatment + performable segments, main camera image."""
from __future__ import annotations

import copy
import json
import logging
import hashlib
import math
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import gateway, asr, config, evidence, kits, llm, media, motion, prompts, replacement_guard, scene_edits, scene_plan, segment_timing, speech_lines, store, storyboard

STEP_META = {
    "probe": ("01 媒体探测", "ffprobe（= hypit media probe）· 本地免费"),
    "transcribe": ("02 逐词转录", "WhisperX / faster-whisper（= hypit transcribe）· 本地免费"),
    "boundaries": ("03 切点检测与帧网格", "hypit boundaries 同算法 + 带台词帧网格 · 本地免费"),
    "overview": ("04 整片理解 → ANALYSIS.md", "外部计费"),
    "close": ("05 逐段细读 → TIMELINE.md", "外部计费"),
    "pack": ("06 拆解包", "7 卡 + 图层表 + 文档"),
    "storyboard": ("07 原片分镜", "按镜头切点截帧，9 格一张"),
}
CARD_KEYS = ["characters", "scene", "beats", "shots", "productMoments", "dialogue", "overlays", "viralLogic"]
DOC_FILES = {"台词表.转写原句.srt": "台词表（转写原句）", "ANALYSIS.md": "整片理解", "TIMELINE.md": "逐段时间轴", "台词表.srt": "台词表（句级）",
             "逐词时间轴.csv": "逐词时间轴", "transcript.json": "转录（hypit.transcript 兼容）",
             "boundaries.json": "切点候选", "passages.json": "段落与细读结果"}


def _set_step(pid: str, step: str, **fields) -> None:
    def fn(d: dict) -> None:
        steps = d["analysis"].setdefault("steps", {})
        cur = steps.setdefault(step, {"title": STEP_META[step][0], "tool": STEP_META[step][1]})
        cur.update(fields)
    store.update(pid, fn)


# ---------- 01 upload ----------

def save_source(pid: str, filename: str, body_path: Path) -> dict:
    d = store.project_dir(pid)
    ext = Path(filename).suffix.lower() or ".mp4"
    if ext not in {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}:
        raise ValueError("只支持 mp4/mov/m4v/webm/mkv/avi 视频")
    target = d / "source" / f"source{ext}"
    for old in (d / "source").glob("source.*"):
        old.unlink()
    body_path.replace(target)
    info = media.probe(target)
    if not info.get("has_video"):
        target.unlink()
        raise ValueError("文件里没有视频流")

    def fn(data: dict) -> None:
        data["source"] = {"file": store.rel(pid, target), "name": filename, "probe": info,
                          "sha256": store.sha256_file(target)}
        data["analysis"] = {"status": "idle", "steps": {}}
        data["breakdown"] = None
        data["breakdown_versions"] = {}
    store.update(pid, fn)
    store.log(pid, f"已上传参考视频 {filename}（{info['duration']:.1f}s，{info.get('width')}x{info.get('height')}）")
    return info


def save_asset(pid: str, filename: str, body_path: Path, kind: str, label: str, roles: list[str]) -> dict:
    ext = Path(filename).suffix.lower()
    allowed = {"image": {".png", ".jpg", ".jpeg", ".webp"}, "audio": {".wav", ".mp3", ".m4a", ".aac", ".flac"}}
    if kind not in allowed or ext not in allowed[kind]:
        raise ValueError(f"{kind} 素材只支持 {', '.join(sorted(allowed.get(kind, [])))}")
    aid = store.new_id("a")
    target = store.project_dir(pid) / "assets" / f"{aid}{ext}"
    body_path.replace(target)
    asset = {"id": aid, "type": kind, "label": label or Path(filename).stem, "roles": roles or ["reference"],
             "file": store.rel(pid, target), "sha256": store.sha256_file(target), "description": ""}
    if kind == "audio":
        asset["probe"] = _audio_probe(target)
        if asset["probe"]["audio_streams"] != 1 or asset["probe"]["video_streams"]:
            target.unlink()
            raise ValueError("声线样本必须是只含一条音轨的纯音频文件")
        if not 1 <= asset["probe"]["duration"] <= 15:
            target.unlink()
            raise ValueError("声线样本需要 1–15 秒（推荐 5 秒左右的干净人声）")
    store.update(pid, lambda d: d["assets"].append(asset))
    return asset


def _audio_probe(path: Path) -> dict:
    import json
    import subprocess
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    raw = json.loads(proc.stdout or "{}")
    streams = raw.get("streams", [])
    return {"duration": round(float(raw.get("format", {}).get("duration") or 0), 2),
            "audio_streams": sum(1 for s in streams if s.get("codec_type") == "audio"),
            "video_streams": sum(1 for s in streams if s.get("codec_type") == "video")}


# ---------- 02 breakdown (Hypit reference-understanding method) ----------

def _grids(src: Path, start: float, end: float, every: float, outdir: Path, transcript: dict | None,
           prefix: str, columns: int = 4, per_grid: int = 12, width: int = 240) -> list[dict]:
    frames = media.frames_range(src, start, end, every, outdir, width=width, prefix=prefix)
    labels = evidence.word_labels(transcript, [f["t"] for f in frames], every)
    grids = []
    for gi in range(0, len(frames), per_grid):
        chunk = frames[gi:gi + per_grid]
        out = outdir / f"{prefix}-grid-{gi // per_grid + 1:02d}.jpg"
        media.tile([f["file"] for f in chunk], out, columns)
        grids.append({"file": out, "columns": columns,
                      "cells": [{"t": f["t"], "words": labels[gi + k]} for k, f in enumerate(chunk)]})
    return grids


def _grid_parts(grids: list[dict], start_index: int = 1) -> list[dict]:
    parts: list[dict] = []
    for i, g in enumerate(grids, start=start_index):
        parts += llm.image_part(g["file"], caption=prompts.grid_caption(i, g))
    return parts


def _clean_passages(raw: list, duration: float, max_n: int) -> list[dict]:
    ps = []
    for i, p in enumerate(raw or []):
        try:
            a, b = float(p.get("start", 0)), float(p.get("end", 0))
        except (TypeError, ValueError):
            continue
        a, b = max(0.0, min(a, duration)), max(0.0, min(b, duration))
        if b - a >= 0.3:
            ps.append({**p, "start": round(a, 2), "end": round(b, 2)})
    ps.sort(key=lambda x: x["start"])
    if not ps:
        step = 10.0
        ps = [{"start": round(t, 2), "end": round(min(duration, t + step), 2), "phase": "", "summary": "", "question": ""}
              for t in [i * step for i in range(int(math.ceil(duration / step)))]]
    # make contiguous from 0 to the end
    ps[0]["start"] = 0.0
    for a, b in zip(ps, ps[1:]):
        b["start"] = a["end"] = round((a["end"] + b["start"]) / 2, 2) if b["start"] != a["end"] else a["end"]
    ps[-1]["end"] = round(duration, 2)
    while len(ps) > max_n:  # merge the shortest neighbouring pair
        k = min(range(len(ps) - 1), key=lambda i: ps[i + 1]["end"] - ps[i]["start"])
        a, b = ps[k], ps.pop(k + 1)
        a.update(end=b["end"], phase="/".join(x for x in (a.get("phase"), b.get("phase")) if x),
                 summary=f"{a.get('summary', '')} {b.get('summary', '')}".strip(),
                 question=f"{a.get('question', '')} {b.get('question', '')}".strip())
    for i, p in enumerate(ps, start=1):
        p["id"] = f"P{i}"
    return ps


class _AnalysisChanged(ValueError):
    """An in-flight analysis lost ownership of its source or generation."""
    def __init__(self, message, source, started):
        super().__init__(message)
        self.source, self.started = source, started


def run_analysis(pid: str, provider: str, srt_text: str | None = None, skip_asr: bool = False,
                 send_video: bool = False) -> None:
    """Wrapper: a failure always leaves a visible failed state (never a stuck 'running')."""
    try:
        _run_analysis(pid, provider, srt_text, skip_asr, send_video)
    except Exception as exc:
        def fn(x: dict) -> None:
            a = x.setdefault("analysis", {})
            a["status"] = "failed"
            a["error"] = str(exc)
            for st in (a.get("steps") or {}).values():
                if st.get("status") == "running":
                    st["status"] = "failed"
                    st["note"] = str(exc)
        with store._lock(pid):
            current = store.load(pid)
            superseded = isinstance(exc, _AnalysisChanged) and (
                current.get("source") != exc.source or current.get("analysis", {}).get("started") != exc.started)
            if not superseded:
                store.update(pid, fn)
        raise


def recover_stale(pid: str, running_jobs: dict) -> None:
    """After a restart, a status left at 'running' with no live job is shown as interrupted."""
    with store._lock(pid):
        try:
            data = store.load(pid)
        except store.ProjectDeletedError:
            return
        _recover_stale_locked(pid, running_jobs, data)


def _recover_stale_locked(pid: str, running_jobs: dict, data: dict) -> None:
    a = data.get("analysis") or {}
    live = running_jobs.get("analyze", {}).get("state") == "running"
    if a.get("status") == "running" and not live:
        def fn(x: dict) -> None:
            x["analysis"]["status"] = "failed"
            x["analysis"]["error"] = x["analysis"].get("error") or "上次拆解被中断（工作台重启或出错），可以重新开始拆解"
            for st in (x["analysis"].get("steps") or {}).values():
                if st.get("status") == "running":
                    st["status"] = "failed"
        store.update(pid, fn)
    for rid, rec in (data.get("images") or {}).items():
        if rec.get("state") == "running" and running_jobs.get(f"img:{rid}", {}).get("state") != "running":
            store.update(pid, lambda x, rid=rid: x["images"][rid].update(state="failed", error="生图被中断，可点“恢复”继续查询原任务"))


def clear_source(pid: str) -> dict:
    """Remove the reference video and everything derived from it; keeps the user's own assets."""
    import shutil
    d = store.project_dir(pid)
    for sub in ("source", "analysis", "packages", "clips", "final"):
        shutil.rmtree(d / sub, ignore_errors=True)
        (d / sub).mkdir(parents=True, exist_ok=True)
    (d / "analysis" / "frames").mkdir(parents=True, exist_ok=True)

    def fn(x: dict) -> None:
        x.update(source=None, analysis={"status": "idle", "steps": {}}, breakdown=None, breakdown_versions={},
                 breakdown_provider=None, storyboard=None, storyboard_edits=None, script=None, final=None)
        x["runs"] = {}
        x["assets"] = [a for a in x.get("assets", []) if not a.get("label", "").startswith("原片参考帧") and not str(a.get("file") or "").startswith("analysis/storyboard/")]
    data = store.update(pid, fn)
    store.log(pid, "已清除参考视频和它的拆解、脚本、预览记录（素材库保留）")
    return data


def _run_analysis(pid: str, provider: str, srt_text: str | None = None, skip_asr: bool = False,
                  send_video: bool = False) -> None:
    route = gateway.routes.resolve('analysis', pid, provider)
    provider = route['channel']
    data = store.load(pid)
    if not data.get("source"):
        raise ValueError("请先上传参考视频")
    d = store.project_dir(pid)
    src = d / data["source"]["file"]
    adir = d / "analysis"
    cfg = config.load()
    settings = {**config.provider_settings(provider), 'model':route['model']}
    send_video = send_video and provider == "jimmy" and settings["model"].lower().startswith("gemini")
    if cfg.get("HF_ENDPOINT"):
        os.environ["HF_ENDPOINT"] = cfg["HF_ENDPOINT"]
    analysis_started = time.time()
    store.update(pid, lambda x: x.update(analysis={"status": "running", "provider": provider,
                                                   "request_model": settings["model"], "steps": {},
                                                   "started": analysis_started}))

    # 01 probe
    _set_step(pid, "probe", status="running")
    info = media.probe(src)
    duration = info["duration"]
    _set_step(pid, "probe", status="done", output={k: info.get(k) for k in
                                                   ("duration", "width", "height", "fps", "audio_stream_count")})

    # 02 word-level transcript
    transcript = None
    if srt_text:
        transcript = asr.parse_srt(srt_text)
        _set_step(pid, "transcribe", status="done", tool="用户上传 SRT（只有句级时间）",
                  output={"sentences": len(transcript["segments"])})
    elif skip_asr or info.get("audio_stream_count", 0) == 0:
        _set_step(pid, "transcribe", status="skipped", note="没有音轨或已选择跳过转录（无对白作品按动作和画面变化定位）")
    elif not asr.available():
        _set_step(pid, "transcribe", status="skipped",
                  note="本机还没装转录模型：按说明让 Codex 安装 faster-whisper（或 WhisperX），或上传 SRT")
    else:
        model, compute = cfg.get("ASR_MODEL", "medium"), cfg.get("ASR_COMPUTE_TYPE", "int8")
        _set_step(pid, "transcribe", status="running", tool=f"{cfg.get('ASR_ENGINE', 'auto')} · {model} {compute} · 本地免费")
        wav = media.extract_audio_wav(src, adir / "audio16k.wav")
        transcript = asr.transcribe_best(wav, model, compute, cfg.get("ASR_LANGUAGE") or None, cfg.get("ASR_ENGINE", "auto"))
        if transcript.get("fallback_reason"):
            store.log(pid, f"WhisperX 失败，已改用 faster-whisper：{transcript['fallback_reason']}", "warn")
        _set_step(pid, "transcribe", status="done", tool=f"{transcript['engine']} · 本地免费",
                  output={"sentences": len(transcript["segments"]), "words": len(evidence.all_words(transcript))})
    if transcript:
        store.atomic_write_json(adir / "transcript.raw.json", transcript)
        store.atomic_write_json(adir / "transcript.json", evidence.hypit_transcript(transcript))
        (adir / "台词表.srt").write_text(evidence.to_srt(transcript), encoding="utf-8")
        (adir / "逐词时间轴.csv").write_text(evidence.to_word_csv(transcript), encoding="utf-8-sig")

    # 03 boundaries + overview grids
    _set_step(pid, "boundaries", status="running")
    cuts = media.hypit_boundaries(src)
    shots = media.merge_boundaries(cuts, duration)
    store.atomic_write_json(adir / "boundaries.json", {"algorithm": "hypit visualBoundaries rate=12 threshold=0.1",
                                                       "candidates": cuts, "merged_shots": shots})
    frame_dir = adir / "frames"
    for old in frame_dir.glob("*.jpg"):
        old.unlink()
    for s in shots:
        s["frame_at"] = (s["start"] + s["end"]) / 2
        f = media.extract_frame(src, s["frame_at"], frame_dir / f"shot-{s['index']:03d}.jpg")
        s["frame"] = store.rel(pid, f)
    every = 1.0 if duration <= 60 else round(duration / 60, 1)
    ev_dir = adir / "evidence"
    overview_grids = _grids(src, 0, duration, every, ev_dir / "overview", transcript, "ov")
    _set_step(pid, "boundaries", status="done",
              output={"cut_candidates": len(cuts), "shots": len(shots), "overview_every_s": every,
                      "overview_grids": len(overview_grids)})
    store.update(pid, lambda x: x["analysis"].update(
        shots=shots, transcript=(transcript or {}).get("segments", []),
        overview_grids=[{"file": store.rel(pid, g["file"]), "cells": g["cells"]} for g in overview_grids]))

    # 04 whole piece → ANALYSIS.md
    label = gateway.label(provider,settings["model"])
    _set_step(pid, "overview", status="running", tool=f"{label} · 外部计费")
    content: list[dict] = [{"type": "text", "text": prompts.overview_user_text(info, cuts, transcript,
                                                                               [] if send_video else overview_grids)}]
    used_video = False
    if send_video:
        proxy = media.make_proxy(src, adir / "proxy.mp4")
        if proxy.stat().st_size <= 18 * 1024 * 1024:
            used_video = True
            content.append({"type": "text", "text": "下面是整段原视频（低帧率小尺寸，含原声）。"})
            content.append(llm.video_part(proxy))
    if not used_video:
        content += _grid_parts(overview_grids)
    try:
        ov, meta = gateway.reason('analysis', [{"role": "system", "content": prompts.OVERVIEW_SYSTEM},
                                            {"role": "user", "content": content}], temperature=0.2, max_tokens=16000, json=True, project=pid, override=route)
    except llm.LLMError as exc:
        if not used_video:
            raise
        store.log(pid, f"整段视频输入失败（{exc}），自动改用帧网格", "warn")
        used_video = False
        content = [{"type": "text", "text": prompts.overview_user_text(info, cuts, transcript, overview_grids)}]
        content += _grid_parts(overview_grids)
        ov, meta = gateway.reason('analysis', [{"role": "system", "content": prompts.OVERVIEW_SYSTEM},
                                            {"role": "user", "content": content}], temperature=0.2, max_tokens=16000, json=True, project=pid, override=route)
    _warn_language_mismatch(pid, transcript, ov.get("overview") or {})
    passages = _clean_passages(ov.get("passages"), duration, int(cfg.get("MAX_PASSAGES") or 14))
    (adir / "ANALYSIS.md").write_text(ov.get("analysis_md") or "", encoding="utf-8")
    usage = [meta["usage"]]
    _set_step(pid, "overview", status="done",
              output={"model": meta["model"], "input": "整段视频" if used_video else f"{len(overview_grids)} 张帧网格",
                      "passages": len(passages), "systems": len(ov.get("systems") or []), "seconds": meta["seconds"]})

    # 05 close reading per passage → TIMELINE.md
    _set_step(pid, "close", status="running", tool=f"{label} · 外部计费", progress=f"0/{len(passages)}")
    done = {"n": 0}
    progress_lock = threading.Lock()
    cache_dir = adir / "close-cache"
    source_hash = data["source"].get("sha256") or store.sha256_file(src)

    def close_read(p: dict) -> dict:
        length = p["end"] - p["start"]
        step = round(max(0.2, math.ceil(length / 24 / 0.1) * 0.1), 2)
        cache_key = json.dumps([p["start"], p["end"], provider, settings["model"]], ensure_ascii=False)
        cache_path = cache_dir / (hashlib.sha256(cache_key.encode()).hexdigest()[:20] + ".json")
        user_text = prompts.close_user_text(p, ov, transcript, step)
        context_hash = hashlib.sha256((source_hash + prompts.CLOSE_SYSTEM + user_text).encode()).hexdigest()
        try:
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cached = None
            if (isinstance(cached, dict) and cached.get("source_hash") == source_hash
                    and cached.get("context_hash") == context_hash
                    and isinstance(cached.get("result"), dict) and isinstance(cached.get("grids"), list)
                    and all((d / g).is_file() for g in cached["grids"])):
                store.log(pid, f"段落 {p['id']} 命中细读缓存")
                return {**cached, "passage": p, "every": step, "usage": {}, "seconds": 0, "cached": True}
            grid_dir = ev_dir / "close" / cache_path.stem / context_hash[:16]
            grids = _grids(src, p["start"], p["end"], step, grid_dir, transcript, p["id"].lower())
            parts = [{"type": "text", "text": user_text}] + _grid_parts(grids)
            res, m = gateway.reason('analysis', [{"role": "system", "content": prompts.CLOSE_SYSTEM},
                                              {"role": "user", "content": parts}], temperature=0.2, max_tokens=12000, json=True, project=pid, override=route)
            result = {"passage": p, "every": step, "grids": [store.rel(pid, g["file"]) for g in grids],
                      "result": res, "usage": m["usage"], "seconds": m["seconds"], "source_hash": source_hash,
                      "context_hash": context_hash}
            store.atomic_write_json(cache_path, result)
            return result
        except llm.LLMError as exc:
            store.log(pid, f"段落 {p['id']} 细读失败，保留其他段落，可重新拆解重试：{exc}", "warn")
            return {"passage": p, "every": step, "grids": [], "usage": {}, "seconds": 0, "error": str(exc),
                    "result": {"timeline_md": f"## {p['start']:.2f}–{p['end']:.2f}\n\n细读失败，请重新拆解重试。"}}
        finally:
            with progress_lock:
                done["n"] += 1
                _set_step(pid, "close", progress=f"{done['n']}/{len(passages)}")

    with ThreadPoolExecutor(max_workers=int(cfg.get("LLM_PARALLEL") or 3)) as pool:
        reads = list(pool.map(close_read, passages))
    usage += [r["usage"] for r in reads]
    failed = sum(bool(r.get("error")) for r in reads)
    timeline_md = "# TIMELINE\n\n" + "\n\n".join((r["result"].get("timeline_md") or
                                                 f"## {r['passage']['start']:.2f}–{r['passage']['end']:.2f}").strip()
                                                for r in reads)
    (adir / "TIMELINE.md").write_text(timeline_md, encoding="utf-8")
    _set_step(pid, "close", status="partial" if failed else "done", output={"passages": len(reads), "failed": failed,
                                                   "seconds": round(sum(r["seconds"] for r in reads), 1)})

    # 06 pack
    _set_step(pid, "pack", status="running")
    shots_out, overlays, moments = [], [], []
    for r in reads:
        shots_out += r["result"].get("shots") or []
        overlays += r["result"].get("overlays") or []
        moments += r["result"].get("productMoments") or []
    for i, s in enumerate(sorted(shots_out, key=lambda x: float(x.get("start") or 0)), start=1):
        s["id"] = f"K{i}"
    pack = {
        "overview": ov.get("overview") or {},
        "characters": ov.get("characters") or [], "scene": ov.get("scene") or [],
        "systems": ov.get("systems") or [],
        "beats": [{"id": r["passage"]["id"], "start": r["passage"]["start"], "end": r["passage"]["end"],
                   "function": r["passage"].get("phase"), "summary": r["passage"].get("summary"),
                   "performance": r["result"].get("performance"), "sound": r["result"].get("sound")} for r in reads],
        "shots": sorted(shots_out, key=lambda x: float(x.get("start") or 0)),
        "productMoments": sorted(moments, key=lambda x: float(x.get("start") or 0)),
        "dialogue": motion.annotate_speakers(
            [{"start": s["start"], "end": s["end"], "text": s["text"]}
             for s in (transcript or {}).get("segments", [])],
            [line for r in reads for line in (r["result"].get("lineSpeakers") or [])
             if isinstance(line, dict) and isinstance(line.get("start"), (int, float))
             and isinstance(line.get("end"), (int, float))
             and line["start"] < r["passage"]["end"] and line["end"] > r["passage"]["start"]],
            ov.get("characters") or []),
        "overlays": sorted(overlays, key=lambda x: float(x.get("start") or 0)),
        "viralLogic": ov.get("viralLogic") or {},
    }
    pack = normalize_breakdown(pack, duration)
    current = store.load(pid)
    if current.get("source") != data.get("source") or current.get("analysis", {}).get("started") != analysis_started:
        raise _AnalysisChanged("拆解期间原片已变化，请重新拆解", data.get("source"), analysis_started)
    speech_transcript = speech_lines.read_transcript(d)
    if transcript is not None and speech_transcript != transcript:
        raise _AnalysisChanged("拆解期间逐词转写已变化，请重新拆解", data.get("source"), analysis_started)
    speech_fingerprint = speech_lines.source_fingerprint(current, speech_transcript)
    pack = speech_lines.refine(pid, pack, transcript, d)
    pack["speech_lines_state"]["fingerprint"] = speech_lines.source_fingerprint(
        {**current, "breakdown": pack}, speech_transcript)
    store.atomic_write_json(adir / "passages.json", [{k: v for k, v in r.items() if k != "usage"} for r in reads])
    key = provider + ("-video" if used_video else "")
    tok_in = sum((u or {}).get("prompt_tokens") or 0 for u in usage)
    tok_out = sum((u or {}).get("completion_tokens") or 0 for u in usage)

    def fn(x: dict) -> None:
        x.setdefault("breakdown_versions", {})[key] = {
            "provider": provider, "input": "整段视频" if used_video else "帧网格", "pack": pack,
            "model": meta["model"], "usage": {"prompt_tokens": tok_in, "completion_tokens": tok_out},
            "seconds": round(meta["seconds"] + sum(r["seconds"] for r in reads), 1), "at": time.strftime("%H:%M:%S"),
            "analysis_md": ov.get("analysis_md") or "", "timeline_md": timeline_md}
        x["breakdown"] = pack
        x["breakdown_provider"] = key
        x["analysis"].update(status="partial" if failed else "done", model=meta["model"], passages=[{**r["passage"], "every": r["every"], "grids": r["grids"]}
                                                      for r in reads],
                             docs={name: f"analysis/{name}" for name in DOC_FILES if (adir / name).exists()})
    with store._lock(pid):
        current = store.load(pid)
        if speech_lines.source_fingerprint(current, speech_lines.read_transcript(d)) != speech_fingerprint:
            raise _AnalysisChanged("断句期间原片或转写已变化，请重新拆解", data.get("source"), analysis_started)
        speech_lines.write_srt(pack, d)
        store.atomic_write_json(adir / f"breakdown-{key}.json", pack)
        store.update(pid, fn)
    if pack["speech_lines_state"].get("warning"):
        store.log(pid, speech_lines.WARNING, "warn")
    _set_step(pid, "pack", status="done", output={k: len(pack.get(k) or []) for k in CARD_KEYS if k != "viralLogic"})
    _build_analysis_storyboard(pid)
    store.log(pid, f"拆解{'部分完成' if failed else '完成'}（{label}）：{len(reads)} 个段落、{len(pack['shots'])} 个分镜、"
              f"{len(overlays)} 条字幕/花字图层" + (f"；{failed} 个段落失败，请重新拆解重试" if failed else ""),
              "warn" if failed else "info")


def _build_analysis_storyboard(pid):
    # Existing boards can contain manual merges; a new analysis only marks them stale.
    if store.load(pid).get("storyboard"):
        _set_step(pid, "storyboard", status="skipped", note="已有原片分镜；拆解有变化时请手动重新生成")
        return
    _set_step(pid, "storyboard", status="running")
    try:
        board = storyboard.build(pid)["storyboard"]
        _set_step(pid, "storyboard", status="done", output={"cells": len(board["cells"]), "sheets": len(board["sheets"])})
    except Exception as exc:
        from .channel_text import user_message
        _set_step(pid, "storyboard", status="failed", note="原片分镜没生成，请重试：" + user_message(exc))
        store.log(pid, "原片分镜没生成：" + user_message(exc), "warn")


def normalize_breakdown(pack: dict, duration: float) -> dict:
    out = {"overview": pack.get("overview") or {}}
    for key in CARD_KEYS:
        default = {} if key == "viralLogic" else []
        value = pack.get(key, default)
        out[key] = value if isinstance(value, type(default)) else default
    out["overview"].setdefault("duration", duration)
    out["overlays"] = [{**o, "bbox": replacement_guard.clean_bbox(o.get("bbox"))}
                       for o in out["overlays"] if isinstance(o, dict)]
    return out


# ---------- 03 replacements and main camera image ----------

def use_camera_image(pid: str, char_id: str, scene_id: str, asset_id: str) -> dict:
    if not all(isinstance(value, str) and value for value in (char_id, scene_id, asset_id)):
        raise ValueError("请选择有效的人物、场景和主镜头图")
    def select(data):
        character = next((c for c in (data.get("replacements") or {}).get("characters", [])
                          if c.get("source_id") == char_id), None)
        if not character or replacement_guard.offscreen_character(data, char_id):
            raise ValueError("当前人物没有可选用的主镜头图")
        if scene_id not in {s["id"] for s in replacement_guard.scenes(data)}:
            raise ValueError("当前项目没有这个场景")
        asset = next((a for a in data.get("assets", []) if a.get("id") == asset_id), {})
        if asset.get("camera_character") != char_id or asset.get("camera_scene") != scene_id:
            raise ValueError("这张图不是当前人物和场景的主镜头图")
        if not replacement_guard.camera_asset_valid(data, asset_id):
            raise ValueError("这张主镜头图已删除、文件缺失或替换设定已过期，请重新生成")
        character.setdefault("camera_assets", {})[scene_id] = asset_id
        replacement_guard.clean_camera_selections(data)
        data.get("camera_notice", {}).pop(f"{char_id}:{scene_id}", None)
    # A failed safety pass must not leave a secretly changed selection behind a 400.
    with store._lock(pid):
        previous = store.load(pid)
        store.update(pid, select)
        try:
            synced = follow_camera_selection(pid, char_id, scene_id)
        except Exception:
            def restore(data):
                data["replacements"] = previous["replacements"]
                if "camera_notice" in previous:
                    data["camera_notice"] = previous["camera_notice"]
                else:
                    data.pop("camera_notice", None)
            store.update(pid, restore)
            raise
        data = store.load(pid)
    label = next(a.get("label") or asset_id for a in data["assets"] if a["id"] == asset_id)
    data["camera_selection"] = {"character_id": char_id, "scene_id": scene_id, "asset_id": asset_id,
        "synced_tasks": synced, "message": f"已改用“{label}”" + (f"，{'、'.join(synced)} 的图1 已同步" if synced else "")}
    return data


def delete_asset(pid: str, asset_id: str) -> dict:
    affected = []
    scene_affected = []
    def remove(data):
        if scene_plan.enabled(data):
            scene_affected.extend(s["id"] for s in replacement_guard.scenes(data)
                if scene_plan.camera_image_for(data, s["id"]) == asset_id)
        affected.extend((c["source_id"], s["id"]) for c in (data.get("replacements") or {}).get("characters", [])
                        for s in replacement_guard.scenes(data)
                        if replacement_guard.camera_image_for(data, c["source_id"], s["id"]) == asset_id)
        data["assets"] = [a for a in data["assets"] if a["id"] != asset_id]
        for row in ((data.get("replacements") or {}).get("scene_plan") or {}).values():
            for key in ("camera_asset", "frame_asset_id"):
                if row.get(key) == asset_id:
                    row.pop(key, None)
        for task in (data.get("script") or {}).get("tasks", []):
            # Keep image slots and historical image records, including deleted image1.
            if task.get("audio_id") == asset_id:
                task["audio_id"] = None
        if affected:
            replacement_guard.clean_camera_selections(data)
        for cid, sid in affected:
            current = replacement_guard.camera_image_for(data, cid, sid)
            label = next((a.get("label") or current for a in data["assets"] if a["id"] == current), "")
            data.setdefault("camera_notice", {})[f"{cid}:{sid}"] = (
                f"原来在用的图已删除，已改用“{label}”" if current else "原来在用的图已删除，本场景还没有可用的图，请重新生成")
        for sid in scene_affected:
            current = scene_plan.camera_image_for(data, sid)
            if current:
                data["replacements"]["scene_plan"].setdefault(sid, {})["camera_asset"] = current
            label = next((a.get("label") or current for a in data["assets"] if a["id"] == current), "")
            data.setdefault("camera_notice", {})[f"scene:{sid}"] = (
                f"原来在用的图已删除，已改用“{label}”" if current else "原来在用的图已删除，这一场还没有可用的图，请重新生成")
    store.update(pid, remove)
    for cid, sid in affected:
        follow_camera_selection(pid, cid, sid, report_failure=True)
    for sid in scene_affected:
        follow_scene_camera_selection(pid, sid, report_failure=True)
    return store.load(pid)


def edit_scenes(pid: str, action: str, scene_id: str, next_scene_id: str | None = None) -> dict:
    with store._lock(pid):
        def commit(data):
            scene_edits.apply(data, action, scene_id, next_scene_id)
            for runs in data.get("runs", {}).values():
                for run in runs:
                    if run.get("state") == "pending_confirmation":
                        run["state"] = "superseded"
        return store.update(pid, commit)


def save_replacements(pid: str, replacements: dict) -> dict:
    if not isinstance(replacements, dict):
        raise ValueError("选角和分场设定格式不正确，请刷新后再试")
    replacements = copy.deepcopy(replacements)
    def commit(data):
        # Migrate only on this explicit save, before replacing the old casting.
        # Server startup/GET must never write project state.
        from . import segment_plan, storyboard_edits
        plan=data.get('segments_plan')
        if plan:storyboard_edits.remember_photo_overrides(data,store.project_dir(pid))
        if plan and not str(plan.get('inputs_sig') or '').startswith('source-v1:'):
            directory=store.project_dir(pid)
            if not segment_plan.status(data,directory)['stale']:
                plan['inputs_sig']=segment_plan.inputs_sig(data,directory)
        scene_edits.discard_invalid_merges(data)
        old = data.get("replacements") or {}
        # Hidden historical roles stay byte-for-byte in persisted casting and
        # model inputs. A visible Step03 form cannot delete or overwrite them.
        source_ids={c['id'] for c in (data.get('breakdown') or {}).get('characters',[])}
        old_ghosts=[(i,c) for i,c in enumerate(old.get('characters',[])) if c.get('source_id') not in source_ids and c.get('action')!='add']
        if old_ghosts:
            ghost_ids={c.get('source_id') for _,c in old_ghosts}
            rows=[c for c in replacements.get('characters',[]) if c.get('source_id') not in ghost_ids]
            for index,entry in old_ghosts:rows.insert(min(index,len(rows)),copy.deepcopy(entry))
            replacements['characters']=rows
        # Only the locked action endpoint owns overlays; stale forms cannot undo them.
        if "scene_edits" in old:
            replacements["scene_edits"] = copy.deepcopy(old["scene_edits"])
        else:
            replacements.pop("scene_edits", None)
        if "scene_plan" not in replacements and "scene_plan" in old:
            replacements["scene_plan"] = copy.deepcopy(old["scene_plan"])
        if "scene_plan" in replacements:
            plan = replacements["scene_plan"]
            if isinstance(plan, dict):
                current_plan = old.get("scene_plan") or {}
                current_scenes = {s["id"] for s in scene_edits.original_scenes(data)}
                for sid in set(current_plan) - current_scenes:
                    plan.pop(sid, None)
                visible = {s["id"] for s in replacement_guard.scenes(data)}
                for sid in (set(plan) | set(current_plan)) & current_scenes:
                    if sid not in visible:
                        if sid in current_plan:
                            plan[sid] = copy.deepcopy(current_plan[sid])
                        else:
                            plan.pop(sid, None)
                        continue
                    row = plan.setdefault(sid, {})
                    if not isinstance(row, dict):
                        continue
                    # Use/capture endpoints own these fields. A stale full form
                    # must not undo a frame pick or paid generation completed
                    # while that form was being edited.
                    for key in ("camera_asset", "frame_at", "frame_asset_id", "frame_mode"):
                        if key in (current_plan.get(sid) or {}):
                            row[key] = current_plan[sid][key]
                        else:
                            row.pop(key, None)
            scene_plan.validate_plan({**data, "replacements": replacements}, replacements["scene_plan"])
        old_chars = {c["source_id"]: c for c in old.get("characters", [])}
        old_cameras = _camera_images(data)
        product_changed = old.get("product") != replacements.get("product")
        for c in replacements.get("characters", []):
            if replacement_guard.offscreen_character(data, c.get("source_id")):
                c.update(action="keep", asset_id=None, camera_assets={})
                c.pop("camera_asset_id", None)
            previous = old_chars.get(c.get("source_id"), {})
            # Camera use is saved by its own endpoint, independently of this form.
            if "camera_assets" not in c:
                c["camera_assets"] = {s["id"]: replacement_guard.camera_image_for(data, c.get("source_id"), s["id"])
                                      for s in replacement_guard.scenes(data)}
            changed = any(c.get(k) != previous.get(k) for k in ("asset_id", "description", "action"))
            if (changed or product_changed or old.get("scene") != replacements.get("scene")) and "camera_asset_id" in c and c.get("camera_asset_id") == old_cameras.get(c.get("source_id")):
                c["camera_asset_id"] = None
            if changed or product_changed or old.get("scene") != replacements.get("scene"):
                c["camera_assets"] = {scene["id"]: None for scene in replacement_guard.scenes(data)}
        data["replacements"] = replacements
        replacement_guard.clean_camera_selections(data)
        if old_ghosts:
            # Camera cleanup must not normalize hidden historical records either.
            by_id={entry.get('source_id'):entry for _,entry in old_ghosts}
            data['replacements']['characters']=[copy.deepcopy(by_id[c.get('source_id')]) if c.get('source_id') in by_id else c
                                                for c in data['replacements'].get('characters',[])]
        for runs in data.get("runs", {}).values():
            for run in runs:
                if run.get("state") == "pending_confirmation":
                    run["state"] = "superseded"
    return store.update(pid, commit)

def build_replacements(data: dict) -> dict:
    rep = data.get("replacements") or {}
    assets = {a["id"]: a for a in data.get("assets", [])}
    out = {"人物": [], "商品": [], "场景": rep.get("scene") or "保持原片场景",
           "替换外观规则": "替换项的 action/story/direction 只用对应 @image 引用，不描述原外观；只有用户明确说明可照写。保持项沿用原片事实。图号对应本段 imageIds 顺序，换人视频模式主镜头图必须在图1。"}
    cams = _camera_images(data)
    for item in rep.get("characters", []):
        a = assets.get(item.get("asset_id") or "")
        out["人物"].append({"原片角色": item.get("source_id"), "处理": item.get("action", "keep"),
                          "用户明确说明": item.get("description") or "",
                          "新人物素材": (a or {}).get("label"), "asset_id": item.get("asset_id"),
                          "camera_assets": {s["id"]: replacement_guard.camera_image_for(data, item.get("source_id"), s["id"])
                                            for s in replacement_guard.scenes(data)}})
    prod = rep.get("product") or {}
    if prod:
        a = assets.get(prod.get("asset_id") or "")
        out["商品"].append({"处理": prod.get("action", "keep"), "新商品说明": prod.get("description", ""),
                          "新商品素材": (a or {}).get("label"), "asset_id": prod.get("asset_id")})
    if scene_plan.enabled(data):
        out["分场设定"] = [{"场景": sid, "在用主镜头图": row["camera_asset"],
            "本场人物和商品": [{"原片对象": subject["key"], "处理": "替换" if subject["on"] else "保持原片",
                "asset_id": subject["asset_id"] if subject["on"] else None,
                "用户说明": subject["description"] if subject["on"] else ""} for subject in row["subjects"]]}
            for sid, row in scene_plan.scene_cast(data).items()]
        out["分场规则"] = "每段按所在场景的有效设定写：未勾选的人物和商品保持原片；新照片以本场照片为准；图1使用本场在用主镜头图。"
    return out


def _camera_images(data: dict) -> dict:
    """character id -> accepted main camera image asset id (generated with for_task camera:<id>)."""
    return replacement_guard.camera_images(data)


def default_images(data: dict, task: dict | None = None) -> list[str]:
    if scene_plan.enabled(data):
        sid = replacement_guard.task_scene(data, task) if task else replacement_guard.scenes(data)[0]["id"]
        subjects = [s for s in scene_plan.effective_subjects(data, sid) if s["on"]]
        current = scene_plan.camera_image_for(data, sid)
        ids = ([current] if current else []) + [s["asset_id"] for s in subjects if s.get("asset_id")]
        ids = list(dict.fromkeys(ids))
        if len(ids) > 4:
            raise ValueError(f"场景 {sid} 的视频参考图超过 4 张，请减少本场替换对象；每段最多 4 张参考图")
        valid = {a["id"] for a in data.get("assets", []) if a.get("type") == "image"}
        return [aid for aid in ids if aid in valid]
    rep = data.get("replacements") or {}
    cams = replacement_guard.camera_images(data, replacement_guard.task_scene(data, task) if task else None)
    ids = []
    for c in replacement_guard.replaced_people(data, task):
        if c.get("action") == "replace":
            aid = cams.get(c["source_id"]) or c.get("asset_id")
            if aid:
                ids.append(aid)
            if task and task.get("mode") == "video" and c.get("asset_id"):
                ids.append(c["asset_id"])
    prod = rep.get("product") or {}
    if prod.get("action") == "replace" and prod.get("asset_id"):
        ids.append(prod["asset_id"])
    assets = {a["id"] for a in data.get("assets", []) if a.get("type") == "image"}
    return list(dict.fromkeys(aid for aid in ids if aid in assets))[:4]


def _camera_frame(data: dict, char_id: str, scene: dict) -> tuple[float, str]:
    pack = data.get('breakdown') or {}
    ranges = replacement_guard.scene_ranges(scene) or [(0, float((data.get('source') or {}).get('probe', {}).get('duration') or 1e9))]
    shots = sorted((s for s in pack.get('shots') or [] if char_id in (s.get('characters') or [])), key=lambda s: float(s.get('start') or 0))
    front = re.compile(r'正面|面向镜头|面对镜头|facing the camera|\bfront\b', re.I)
    behind = re.compile(r'背对|背影|from behind|back to camera', re.I)

    def text(row):
        return ' '.join(str(v) for k, v in row.items() if k not in {'characters', 'start', 'end', 'id'})

    def caption_area(at):
        masks, missing = replacement_guard.frame_caption_masks(data, at)
        return (1.0 if missing else 0.0) + sum((m['bbox'][2] - m['bbox'][0]) *
            (m['bbox'][3] - m['bbox'][1]) for m in masks)

    candidates, front_candidates = [], []
    for shot in shots:
        for a, b in ranges:
            start, end = max(a, float(shot['start'])), min(b, float(shot['end']))
            if end <= start or behind.search(text(shot)):
                continue
            candidates.append((start, end, shot))
            if front.search(text(shot)):
                front_candidates.append((start + end) / 2)
                continue
            for moment in pack.get('productMoments') or []:
                if not all(k in moment for k in ('start', 'end')) or behind.search(text(moment)):
                    continue
                x, y = max(start, float(moment['start'])), min(end, float(moment['end']))
                if y > x and front.search(text(moment)):
                    front_candidates.append((x + y) / 2)
    if front_candidates:
        return min(front_candidates, key=lambda at: (caption_area(at), at)), '正面、商品清楚'
    moments = []
    for moment in pack.get('productMoments') or []:
        if not all(k in moment for k in ('start', 'end')) or behind.search(text(moment)):
            continue
        for a, b, _ in candidates:
            x, y = max(a, float(moment['start'])), min(b, float(moment['end']))
            if y > x:
                score = len(re.findall(r'完整|全身|清楚|清晰|full|complete|clear', text(moment), re.I))
                moments.append((score, y - x, x, y))
    if moments:
        _, _, a, b = max(moments, key=lambda row: (row[0], row[1], -caption_area((row[2]+row[3])/2), -row[2]))
        return (a + b) / 2, '商品展示最完整'
    if candidates:
        a, b, _ = min(candidates, key=lambda row: (caption_area(min(row[0]+.5, row[1])), row[0]))
        return min(a + .5, b), '首次合适出镜 +0.5 秒'
    if not shots:
        return (sum(ranges[0]) / 2 if replacement_guard.scene_ranges(scene) else 1.0), '缺少镜头证据，采用场景默认时间'
    raise ValueError('本场景没有可用的正面或商品展示镜头，请用“换一帧”选择截帧时间')



FRAME_REASONS = {
    "front_product": "正面、商品清楚", "product_moment": "商品展示最完整",
    "legacy_first_appearance": "旧规则：人物第一次出现后 0.5 秒", "manual": "手动选择",
    "first_suitable": "首次合适出镜后 0.5 秒", "scene_default": "缺少镜头证据，采用场景默认时间",
    "unknown": "选取方式未记录",
}


def frame_metadata(data: dict, frame: dict) -> dict:
    at = frame.get("frame_at")
    reason = frame.get("frame_reason_code") or frame.get("frame_reason")
    legacy_labels = {v: k for k, v in FRAME_REASONS.items()}
    legacy_labels.update({"用户选择": "manual", "首次合适出镜 +0.5 秒": "first_suitable"})
    reason = legacy_labels.get(reason, reason)
    cid = frame.get("frame_character") or frame.get("camera_character")
    if not cid:
        match = re.search(r"frame-(C\d+)-", str(frame.get("file") or ""))
        cid = match[1] if match else None
    shots = [s for s in (data.get("breakdown") or {}).get("shots") or [] if cid in (s.get("characters") or [])]
    first = min((float(s.get("start") or 0) for s in shots), default=0)
    if not reason:
        reason = "legacy_first_appearance" if at is not None and (abs(float(at) - .5) < .011 or abs(float(at) - first - .5) < .011) else "unknown"
    warning = ""
    if reason == "legacy_first_appearance" and at is not None:
        for shot in shots:
            if float(shot.get("start") or 0) <= float(at) < float(shot.get("end") or 0) and re.search(
                    r"背对|背影|from behind|back to camera", " ".join(str(v) for k, v in shot.items() if k not in {"characters", "start", "end", "id"}), re.I):
                warning = "这一帧是背影，看不到脸，建议点‘生成预览’按新规则重新截帧"
                break
    return {"frame_at": at, "frame_reason": reason, "frame_warning": warning}

def _dialogue_source(data: dict, task: dict, directory: Path) -> list[dict]:
    transcript = motion.read_transcript(directory)
    rows = motion.source_lines(task, transcript)
    if not rows:
        rows = motion.source_lines(task, {'segments': (data.get('breakdown') or {}).get('dialogue') or []})
    if not rows:
        raise ValueError('缺少原片逐句台词和时间，请先完成转录')
    offset = float(task.get('source_start') or 0)
    source = [{'start': round(row['start'] + offset, 3), 'end': round(row['end'] + offset, 3), 'original': row['text']} for row in rows]
    labels = task.get('dialogue_lines') or (data.get('breakdown') or {}).get('dialogue') or []
    return motion.annotate_speakers(source, labels, (data.get('breakdown') or {}).get('characters') or [])


def _dialogue_constraints(data: dict, task: dict, row: dict) -> dict:
    evidence_task = {**task, 'dialogue': row['original'], 'dialogue_choice': None}
    must_keep = not bool(replacement_guard.dialogue_mismatches(data, evidence_task))
    terms = sorted({term for item in replacement_guard.dialogue_conflicts(data, evidence_task)
                    for term in item.get('matched_terms', item.get('terms', []))}) if not must_keep else []
    return {'mustKeep': must_keep, 'conflictTerms': terms}


def _dialogue_error(source: list[dict], index: int, message: str) -> ValueError:
    return ValueError(f"第 {index + 1} 句，原句「{source[index]['original']}」：{message}")


def _validate_dialogue_proposal(data: dict, task: dict, source: list[dict], result: dict) -> list[dict]:
    lines = result.get('lines') if isinstance(result, dict) else None
    if not isinstance(lines, list) or len(lines) != len(source):
        count = len(lines) if isinstance(lines, list) else 0
        index = next((i for i, (original, row) in enumerate(zip(source, lines or []))
                      if not isinstance(row, dict) or row.get('original') != original['original']),
                     min(count, len(source) - 1)) if isinstance(lines, list) else 0
        raise _dialogue_error(source, index, f'改写方案必须保留原片逐句数量和时间，应有 {len(source)} 句，实际 {count} 句；请按原顺序返回全部句子')
    out = []
    language = task.get('language') or (data.get('breakdown') or {}).get('overview', {}).get('language') or 'en'
    if result.get('language') and str(result['language']).lower().split('-')[0] != language.lower().split('-')[0]:
        raise _dialogue_error(source, 0, f"改写方案必须保持原片语言 {language}，模型返回 {result['language']}")
    for index, (original, row) in enumerate(zip(source, lines)):
        if not isinstance(row, dict) or row.get('original') != original['original'] or any(row.get(k) != original[k] for k in ('start', 'end')):
            problems = [f'{key} 应为 {original[key]!r}，实际为 {row.get(key)!r}' for key in ('original', 'start', 'end')
                        if row.get(key) != original[key]] if isinstance(row, dict) else ['该行不是台词对象']
            raise _dialogue_error(source, index, '改写方案改变了原句或时间；' + '；'.join(problems))
        proposed = row.get('proposed')
        if not isinstance(proposed, str) or not proposed.strip():
            raise _dialogue_error(source, index, '改写后的台词不能为空')
        changed = proposed != original['original']
        if changed:
            source_cjk = bool(re.search(r'[\u3400-\u9fff]', original['original']))
            proposed_cjk = bool(re.search(r'[\u3400-\u9fff]', proposed))
            if source_cjk != proposed_cjk:
                raise _dialogue_error(source, index, '改写方案必须保持原片语言，不能翻译原句')
        constraints = _dialogue_constraints(data, task, original)
        restored = constraints['mustKeep'] and (changed or row.get('restored') is True)
        if constraints['mustKeep']:
            proposed = original['original']
        out.append({**original, 'proposed': proposed, 'reason': str(row.get('reason') or ''),
                    **constraints, 'restored': restored, 'changed': proposed != original['original']})
    return out


def dialogue_proposal(pid: str, task_id: str, instruction: str = '', *, provider: str | None = None,
                      previous_proposal: dict | None = None) -> dict:
    data = store.load(pid)
    script = data.get('script') or {}
    task = next((t for t in script.get('tasks', []) if t.get('id') == task_id), None)
    if task is None:
        raise ValueError('分段不存在')
    source = _dialogue_source(data, task, store.project_dir(pid))
    constrained = [{**row, **_dialogue_constraints(data, task, row)} for row in source]
    previous = task.get('dialogue_proposal') if previous_proposal is None else previous_proposal
    if previous is not None:
        rows = previous.get('lines') if isinstance(previous, dict) else None
        # Past/manual text is diagnostic, not fresh model output. Ignore stale source
        # identities; allow empty or conflicting drafts so the model can repair them.
        if not isinstance(rows, list) or len(rows) != len(source) or any(
                not isinstance(row, dict) or not isinstance(row.get('proposed'), str) or
                any(row.get(k) != original[k] for k in ('start', 'end', 'original'))
                for original, row in zip(source, rows)):
            previous = None
        else:
            # Carry text feedback, never obsolete length budgets from saved proposals.
            previous = {**{k: previous[k] for k in ('model', 'provider', 'instruction') if k in previous},
                        'lines': [{**original, 'proposed': row['proposed'], 'reason': str(row.get('reason') or ''),
                                   'changed': row['proposed'] != original['original'],
                                   **_dialogue_constraints(data, task, original)}
                                  for original, row in zip(source, rows)]}
    payload = {'lines': constrained, 'language': task.get('language'), 'replacements': data.get('replacements'),
               'assets': [{k: a.get(k) for k in ('id', 'label', 'roles', 'description')} for a in data.get('assets', []) if not a.get('internal')],
               'conflicts': replacement_guard.conflicts(data, task),
               'dialogue_mismatches': replacement_guard.dialogue_mismatches(data, {**task, 'dialogue_choice': None}),
               'instruction': instruction, 'previous_proposal': previous}
    selected = gateway.routes.resolve('dialogue_proposal', data, provider)
    selected_provider, selected_model = selected['channel'], selected['model']
    result, meta = gateway.reason_checked("dialogue_proposal", [
        {'role': 'system', 'content': prompts.DIALOGUE_PROPOSAL_SYSTEM},
        {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
        analysis=data.get('analysis') or {}, temperature=.2, max_tokens=6000, project=pid, override=selected)
    proposal = {'lines': _validate_dialogue_proposal(data, task, source, result), 'model': meta['model'],
                'provider': meta.get('provider') or selected_provider, 'instruction': instruction}
    def commit(current):
        if current.get('script') != script or current.get('replacements') != data.get('replacements'):
            raise ValueError('生成方案期间脚本或替换设定已变化，请重新生成')
        next(t for t in current['script']['tasks'] if t['id'] == task_id)['dialogue_proposal'] = proposal
    store.update(pid, commit)
    return proposal


def adopt_dialogue_proposal(pid: str, task_id: str, lines: list[dict] | None = None) -> dict:
    data = store.load(pid)
    script = data.get('script') or {}
    task = next((t for t in script.get('tasks', []) if t.get('id') == task_id), None)
    if task is None or not task.get('dialogue_proposal'):
        raise ValueError('请先点“改写冲突台词”生成方案')
    proposed = lines if lines is not None else task['dialogue_proposal']['lines']
    source = _dialogue_source(data, task, store.project_dir(pid))
    checked = _validate_dialogue_proposal(data, task, source, {'lines': proposed})
    task['dialogue_proposal']['lines'] = checked
    task['dialogue'] = '\n'.join(line['proposed'] for line in checked)
    task['dialogue_lines'] = [{**{k: row[k] for k in ('start','end','speaker','onScreen','evidence') if k in row}, 'text': row['proposed']} for row in checked]
    task['dialogue_choice'] = 'adopt'
    motion.apply_dialogue_anchors(task, checked)
    script['dialogue_mode'] = 'rewrite'
    return save_script(pid, script)


def keep_original_dialogue(pid: str, task_id: str) -> dict:
    def commit(data):
        task = next((t for t in (data.get('script') or {}).get('tasks', []) if t.get('id') == task_id), None)
        if task is None:
            raise ValueError('分段不存在')
        source = _dialogue_source(data, task, store.project_dir(pid))
        task['dialogue'] = '\n'.join(row['original'] for row in source)
        task['dialogue_lines'] = [{**{k: row[k] for k in ('start','end','speaker','onScreen','evidence') if k in row}, 'text': row['original']} for row in source]
        motion.restore_dialogue_anchors(task)
        task['dialogue_choice'] = 'keep'
        _attach_motion_evidence(data, data['script'], store.project_dir(pid))
        _refresh_target_timing(task)
        errors = motion.performance_errors(task) + motion.anchor_errors(task)
        if errors:
            raise ValueError('\n'.join(errors))
    return store.update(pid, commit)


def _clean_camera_frame(pid: str, data: dict, frame_asset: dict, at: float) -> tuple[dict, str]:
    masks, missing = replacement_guard.frame_caption_masks(data, at)
    warning = '字幕位置记录不完整，无法完整去除，请检查参考帧' if missing else ''
    if not masks:
        return frame_asset, warning
    d = store.project_dir(pid)
    original = d / frame_asset['file']
    key = hashlib.sha256(json.dumps({'source': store.sha256_file(original),
        'rectangles': [m['bbox'] for m in masks], 'method': 'delogo-v1'}, sort_keys=True).encode()).hexdigest()
    # The project lock also prevents two simultaneous drafts from creating duplicates.
    with store._lock(pid):
        cached = next((a for a in store.load(pid).get('assets', [])
            if a.get('derived_from') == frame_asset['id'] and a.get('caption_clean_key') == key
            and (d / str(a.get('file') or '')).is_file()
            and store.sha256_file(d / a['file']) == a.get('sha256')), None)
        if cached:
            return cached, warning
        clean_path = media.delogo_frame(original, masks, d / 'assets' / f"clean-{frame_asset['id']}-{key[:12]}.png")
        try:
            residue = media.caption_residue(clean_path, d / data['source']['file'], masks,
                (data.get('breakdown') or {}).get('overlays') or [],
                d / 'analysis' / f'caption-frame-{key[:12]}', image_at=at)
        except (media.MediaError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
            residue = {'status': 'unavailable', 'reason': f'字幕残留检查未完成，请人工检查：{exc}', 'suspected': []}
        overlap = replacement_guard.frame_caption_overlap(data, at, masks)
        asset = {'id': store.new_id('a'), 'type': 'image', 'roles': ['scene'], 'internal': True,
                 'label': f'去字幕参考帧 {at:.2f}s', 'derived_from': frame_asset['id'],
                 'file': store.rel(pid, clean_path), 'sha256': store.sha256_file(clean_path),
                 'camera_scene': frame_asset.get('camera_scene'), 'frame_at': at,
                 'frame_reason': frame_asset.get('frame_reason'), 'masks': masks, 'caption_clean_key': key,
                 'caption_residue': residue, 'caption_overlap_warning':
                     '参考帧的字幕区域与人物/商品重叠，已模糊处理，生成后请检查这一块' if overlap else ''}
        store.update(pid, lambda x: x['assets'].append(asset))
        return asset, warning


def _known_camera_frame_time(data: dict, directory: Path, char_id: str, scene_id: str, at: float) -> bool:
    """Keep the exact time of a visible analysis/cached frame instead of rounding it."""
    analysis = data.get("analysis") or {}
    frames = analysis.get("frames") or []
    candidates = []
    for row in frames or analysis.get("shots") or []:
        try:
            time_at = row.get("frame_at")
            if time_at is None:
                time_at = next((row[k] for k in ("at", "time", "start") if row.get(k) is not None), None) if frames else (float(row["start"]) + float(row["end"])) / 2
            candidates.append((float(time_at), row.get("file") or row.get("frame") or row.get("path")))
        except (KeyError, TypeError, ValueError):
            continue
    candidates += [(a.get("frame_at"), a.get("file")) for a in data.get("assets", [])
                   if not a.get("derived_from") and a.get("camera_scene") == scene_id
                   and (a.get("frame_character") == char_id or Path(str(a.get("file") or "")).name.startswith(f"frame-{char_id}-"))]
    return any(time_at == at and file and (directory / file).is_file() for time_at, file in candidates)


def draft_camera_image(pid: str, provider: str, char_id: str, at: float | None = None, scene_id: str | None = None,
                       *, _data: dict | None = None, _frame: dict | None = None,
                       _anchor: dict | None = None) -> dict:
    """Hypit image direction: write person/shot/setting for the main A-roll camera image."""
    route = gateway.routes.resolve('camera_prompt', pid, provider)
    provider = route['channel']
    data = _data if _data is not None else store.load(pid)
    d = store.project_dir(pid)
    pack = data.get("breakdown") or {}
    char = next((c for c in pack.get("characters", []) if c.get("id") == char_id), {"id": char_id})
    rep = next((c for c in (data.get("replacements") or {}).get("characters", []) if c.get("source_id") == char_id), {})
    if replacement_guard.offscreen_character(data, char_id):
        raise ValueError("画外音角色无需主镜头图")
    assets = {a["id"]: a for a in data.get("assets", [])}
    portrait = assets.get(rep.get("asset_id") or "")
    if not portrait:
        raise ValueError("请先在第 03 步选择新人物照片，再生成预览")
    product = (data.get("replacements") or {}).get("product") or {}
    product_asset = assets.get(product.get("asset_id") or "") if product.get("action") == "replace" else None
    if product.get("action") == "replace" and not product_asset:
        raise ValueError("商品替换缺少参考图，请先在第 03 步选择商品图")
    scene_id = scene_id or replacement_guard.first_scene(data, char_id)
    scene = next((s for s in replacement_guard.scenes(data) if s["id"] == scene_id), None)
    if scene is None:
        raise ValueError(f"场景 {scene_id} 不存在")
    ranges = replacement_guard.scene_ranges(scene)
    frame_reason = "手动选择"
    if at is None:
        at, frame_reason = _camera_frame(data, char_id, scene)
    frame_reason_code = next((code for code, label in FRAME_REASONS.items() if label == frame_reason), "first_suitable")
    requested_at = float(at)
    at = round(requested_at, 2)
    # Retain the existing range checks and automatic/manual rounding contract,
    # but never move a known thumbnail to a nearby instant or reuse that frame.
    if frame_reason_code == "manual" and (_known_camera_frame_time(data, d, char_id, scene_id, requested_at)
            or _frame and _frame.get('camera_scene') == scene_id and _frame.get('frame_at') == requested_at):
        at = requested_at
    if not math.isfinite(at) or (ranges and not any(a <= at <= b for a, b in ranges)):
        raise ValueError(f"截帧时间不在场景 {scene_id}（{scene.get('name', '')}）的时间范围内")
    anchor_id = None
    ordered = sorted(replacement_guard.scenes(data), key=lambda s: s["id"] != replacement_guard.first_scene(data, char_id))
    for other in ordered:
        if other["id"] != scene_id:
            aid = replacement_guard.camera_image_for(data, char_id, other["id"])
            if aid in assets:
                anchor_id = aid
                break
    if _anchor is not None:
        anchor_id = _anchor.get("asset_id")
    with store._lock(pid):
        frame_asset = _frame or next((a for a in store.load(pid).get("assets", [])
                            if (a.get("frame_character") == char_id or (not a.get("frame_character")
                                and Path(str(a.get("file") or "")).name.startswith(f"frame-{char_id}-")))
                            and a.get("camera_scene") == scene_id
                            and float(a.get("frame_at", -100)) == at
                            and (d / str(a.get("file") or "")).is_file()), None)
        if frame_asset:
            metadata = frame_metadata(data, frame_asset)
            frame_asset.update(metadata)
            store.update(pid, lambda x: next(a for a in x["assets"] if a["id"] == frame_asset["id"]).update(metadata))
            if frame_reason_code != "manual":
                frame_reason_code = metadata["frame_reason"]
                frame_reason = FRAME_REASONS.get(frame_reason_code, FRAME_REASONS["unknown"])
            if not frame_asset.get("internal") or frame_asset.get("frame_character") != char_id:
                frame_asset.update(internal=True, frame_character=char_id)
                store.update(pid, lambda x: next(a for a in x["assets"] if a["id"] == frame_asset["id"]).update(
                    internal=True, frame_character=char_id))
            frame = d / frame_asset["file"]
            at = frame_asset["frame_at"]
        else:
            frame_time = f"{at:.3f}" if at != round(at, 2) else f"{at:.2f}"
            frame_name = f"frame-{char_id}-{frame_time}.jpg" if len(replacement_guard.scenes(data)) == 1 else f"frame-{char_id}-{scene_id}-{frame_time}.jpg"
            frame = media.extract_frame(d / data["source"]["file"], at, d / "assets" / frame_name, width=720)
            frame_asset = {"id": store.new_id("a"), "type": "image", "label": f"原片参考帧 {char.get('name', char_id)} {at:.2f}s",
                           "roles": ["scene"], "internal": True, "frame_character": char_id,
                           "camera_scene": scene_id, "frame_at": at, "frame_reason": frame_reason_code,
                           "file": store.rel(pid, frame), "sha256": store.sha256_file(frame),
                           "description": "原片机位/场景/构图参考（不继承人物外观）"}
            store.update(pid, lambda x: x["assets"].append(frame_asset))
        model_frame, caption_warning = _clean_camera_frame(pid, data, frame_asset, at)
    parts: list[dict] = [{"type": "text", "text": json.dumps({
        "原片角色": char, "替换设定": rep, "新人物素材说明": (portrait or {}).get("description"),
        "整片理解": pack.get("overview"), "场景卡": scene, "参考帧秒数": at,
        "参考帧所在分镜": [s for s in pack.get('shots', []) if scene_plan._interval(s)[0] <= at < scene_plan._interval(s)[1]]}, ensure_ascii=False)}]
    refs = []
    if portrait:
        parts += llm.image_part(d / portrait["file"], caption="reference image 1：新人物照片（身份依据）")
        refs.append(portrait["id"])
    if product_asset:
        parts += llm.image_part(d / product_asset["file"], caption="reference image 2：服装/商品真实外观，穿戴或手持状态必须落实")
        refs.append(product_asset["id"])
    if anchor_id:
        anchor_role = '身份锚点，只保持脸、发型和商品细节，衣服按参考帧；不沿用背景和机位' if scene_plan.enabled(data) else '身份锚点，只保持脸、发型、服装和商品细节；不沿用背景和机位'
        parts += llm.image_part(d / assets[anchor_id]["file"], caption=f"reference image {len(refs) + 1}：{anchor_role}")
        refs.append(anchor_id)
    parts += llm.image_part(d / model_frame['file'], caption=f"reference image {len(refs) + 1}：原片参考帧（机位、场景、构图依据，不继承人物外观）")
    refs.append(model_frame["id"])
    roles = ("图1确定人物身份，图2确定服装/商品的真实外观，图3为原片参考帧（只取机位、场景和构图）。"
             if product_asset else "图1确定人物身份，图2为原片参考帧（只取机位、场景和构图）。")
    if anchor_id:
        roles = "图1确定人物身份，" + ("图2确定服装/商品的真实外观，" if product_asset else "")
        roles += f"图{len(refs) - 1}是身份锚点，只保持同一个人的脸、发型、服装和商品细节一致，不沿用它的背景和机位；图{len(refs)}为本场景原片参考帧，背景和机位只看本场景参考帧。"
    roles += "生成一个实际机位的一帧完整画面，不要多角度、分格、拼贴；替换人物不得继承原片人物外观或冲突的旧商品；画面里没勾选的人保持参考帧里的样子。"
    roles += "参考帧里的字幕、贴纸、水印一律不要画出来。"
    roles += "\n" + prompts.CAMERA_SHOT_FIDELITY
    if product_asset:
        roles += "\n" + prompts.CAMERA_PRODUCT_REFERENCE + "\n" + prompts.CAMERA_PRODUCT_USE
    if scene_plan.enabled(data):
        roles = roles.replace('脸、发型、服装和商品细节一致', '脸、发型和商品细节一致，衣服按参考帧')
        roles += '\n' + prompts.CAMERA_SCENE_CLOTHING
        roles += f"\n图1 只取{char.get('name') or char_id}的脸和身份，衣服按参考帧。人物补充说明：{rep.get('description') or '无换装要求'}"
    parts.append({"type": "text", "text": roles + "用户商品说明：" + (product.get("description") or "")})
    system = prompts.SCENE_PERSON_CAMERA_IMAGE_SYSTEM if scene_plan.enabled(data) else prompts.CAMERA_IMAGE_SYSTEM
    res, _ = gateway.reason('camera_prompt', [{"role": "system", "content": system},
                                      {"role": "user", "content": parts}], temperature=0.5, max_tokens=4000, json=True, project=pid, override=route)
    prompt = roles + "\n\n" + kits.phone_ugc_image_prompt(res.get("person", ""), res.get("shot", ""), res.get("setting", "")) + "\n" + prompts.CAMERA_IMAGE_NO_TEXT
    return {"prompt": prompt, "image_ids": refs, "note": res.get("note", ""), "for_task": f"camera:{char_id}:{scene_id}",
            'clean_frame_asset_id': model_frame['id'] if model_frame.get('derived_from') else None,
            'caption_warning': caption_warning,
            'caption_overlap_warning': model_frame.get('caption_overlap_warning', ''),
            'caption_residue': model_frame.get('caption_residue'),
            "scene_id": scene_id, "frame_asset_id": frame_asset["id"], "frame_at": at, "frame_reason": frame_reason, "frame_reason_code": frame_reason_code,
            "frame_warning": frame_metadata(data, {**frame_asset, "frame_reason": frame_reason_code})["frame_warning"], "anchor_asset_id": anchor_id,
            "camera_binding": replacement_guard.camera_binding(data, char_id, d, scene_id=scene_id),
            "ratio": (data["source"]["probe"] or {}).get("ratio_hint", "9:16")}


def capture_scene_frame(pid: str, scene_id: str, at: float | None = None) -> dict:
    """Capture and publish a scene frame immediately, using local media tools only."""
    with store._lock(pid):
        data = store.load(pid)
        mode = "auto" if at is None else "manual"
        choice = scene_plan.frame_choice(data, scene_id, at)
        directory = store.project_dir(pid)
        at = choice["at"]
        # Keep exact known frame buttons paired with their original capture time;
        # arbitrary manual/automatic instants and persisted inputs use hundredths.
        known = any(scene_plan._thumbnail_at(s) == at for s in (data.get('analysis') or {}).get('shots') or [])
        known = known or any(a.get('scene_frame') and a.get('camera_scene') == scene_id
                            and a.get('frame_at') == at for a in data.get('assets', []))
        if not known:
            rounded = scene_plan.rounded_time(at, scene_plan._ranges(data, scene_id))
            choice = {**scene_plan.frame_choice(data, scene_id, rounded), 'reason':choice['reason']}
            at = choice['at']
        source = (directory / str((data.get("source") or {}).get("file") or "")).resolve()
        if not source.is_relative_to(directory.resolve()) or not source.is_file():
            raise ValueError("找不到原片，请先上传原片再选帧")
        frame = next((a for a in data.get("assets", []) if a.get("scene_frame")
            and a.get("camera_scene") == scene_id and a.get("frame_at") == at
            and (directory / str(a.get("file") or "")).is_file()
            and store.sha256_file(directory / a["file"]) == a.get("sha256")), None)
        if frame is None:
            aid = store.new_id("a")
            output = media.extract_frame(source, at, directory / "assets" / f"scene-frame-{aid}.jpg", width=720)
            frame = {"id": aid, "type": "image", "label": f"原片参考帧 {scene_id} {at:.2f} 秒",
                "roles": ["scene"], "internal": True, "scene_frame": True, "camera_scene": scene_id,
                "frame_at": at, "frame_reason": "manual" if mode == "manual" else "scene_auto",
                "file": store.rel(pid, output), "sha256": store.sha256_file(output),
                "frame_characters": choice["characters"], "frame_present_text": choice["present_text"],
                "scene_frame_reason": choice["reason"]}
            store.update(pid, lambda current: current["assets"].append(frame))
        model_frame, warning = _clean_camera_frame(pid, data, frame, at)
        def select(current):
            row = current.setdefault("replacements", {}).setdefault("scene_plan", {}).setdefault(scene_id, {})
            row.update(frame_at=round(at,2), frame_asset_id=frame["id"], frame_mode=mode)
        store.update(pid, select)
        return {**choice, "asset_id": frame["id"], "file": frame["file"],
                "image_subject_keys":[s['key'] for s in scene_plan.effective_subjects(data,scene_id) if s['on']],
                "frame_at": at, "frame_asset_id": frame["id"], "frame_mode": mode, "caption_warning": warning,
                "clean_frame_asset_id": model_frame["id"] if model_frame.get("derived_from") else None,
                "caption_overlap_warning": model_frame.get("caption_overlap_warning", ""),
                "caption_residue": model_frame.get("caption_residue")}


SCENE_CAMERA_IMAGE_SYSTEM = """你为整场复刻准备一张主镜头图，输出英文的 person、shot、setting 三段。
严格按用户给出的图号表说明每个勾选人物对应哪张照片，商品对应哪张图，最后一张是原片参考帧。
person 逐人写清图号与原片角色的对应关系；没勾选的人保持原片身份、外观和衣服。只勾商品时，所有人物都保持原样。
shot 沿用原片机位、构图、动作状态和每个人的站位，不把两个人合成一个人，不增加或删除人物。
setting 沿用参考帧场景；只有用户明确填写新场景时才改变。商品以商品图及用户说明为准。
只生成一个机位的一帧完整手机视频画面，不要多角度、分格或拼贴。不要画参考帧里的字幕、贴纸或水印。
不要写固定拍摄质感，不编造照片或说明里没有的外观。只输出 JSON：
{"person":"","shot":"","setting":"","note":"给用户的简短中文说明"}。""" + "\n" + prompts.CAMERA_SHOT_FIDELITY + "\n" + prompts.CAMERA_PRODUCT_USE + '\n' + prompts.CAMERA_SCENE_CLOTHING


def draft_scene_camera_image(pid: str, provider: str, scene_id: str, at: float | None = None) -> dict:
    from . import jimmy_bridge
    data = store.load(pid)
    scene = next((s for s in replacement_guard.scenes(data) if s["id"] == scene_id), None)
    if scene is None:
        raise ValueError("当前项目没有这个场景，请刷新分场表")
    selected_at = scene_plan.scene_cast(data, store.project_dir(pid))[scene_id]["frame"]["at"] if scene_plan.frame_mode(data, scene_id) == "manual" else None
    frame_info = capture_scene_frame(pid, scene_id, at if at is not None else selected_at)
    data = store.load(pid)
    directory = store.project_dir(pid)
    assets = {a["id"]: a for a in data.get("assets", [])}
    raw_frame = assets[frame_info["asset_id"]]
    model_frame = assets[frame_info.get("clean_frame_asset_id") or raw_frame["id"]]
    references = jimmy_bridge.scene_image_references(data, scene_id, at=frame_info['at'])
    subjects = references["subjects"]
    people = [s for s in subjects if s["kind"] == "character"]
    refs = references["image_ids"] + [model_frame["id"]]
    image_route = gateway.routes.resolve("camera_image", pid)
    limit = jimmy_bridge.max_image_references(image_route["channel"], image_route.get("model"))
    if len(refs) > limit:
        raise ValueError(f"本场要送去生图的图片有 {len(refs)} 张，当前渠道最多 {limit} 张，请减少本场替换对象")
    binding = scene_plan.binding(data, scene_id, directory)
    if len(people) == 1:
        # Keep every legacy prompt word, including the cross-scene identity anchor.
        effective = copy.deepcopy(data)
        character = next((c for c in effective["replacements"].get("characters", [])
                          if c.get("source_id") == people[0]["key"]), None)
        if character is None:
            character = {"source_id": people[0]["key"]}
            effective["replacements"].setdefault("characters", []).append(character)
        character.update(action="replace", asset_id=people[0]["asset_id"], description=people[0]["description"])
        product = next((s for s in subjects if s["kind"] == "product"), None)
        effective["replacements"]["product"] = ({"action": "replace", "asset_id": product["asset_id"],
            "description": product["description"]} if product else {"action": "keep"})
        draft = draft_camera_image(pid, provider, people[0]["key"], at=frame_info["at"], scene_id=scene_id,
            _data=effective, _frame=raw_frame, _anchor={"asset_id": references["anchor_asset_id"]})
    else:
        route = gateway.routes.resolve("camera_prompt", pid, provider)
        roles = []
        for i, subject in enumerate(subjects, 1):
            roles.append(f"图{i} 只取{subject['name']}的脸和身份，衣服按参考帧；补充说明：{subject.get('description') or '无换装要求'}" if subject["kind"] == "character"
                         else f"图{i} 是商品的真实外观")
        roles.append(f"图{len(refs)} 是原片参考帧（只取机位、场景、构图和人物站位）")
        roles = "，".join(roles) + "。每个人在画面里的位置按参考帧对应；画面里没勾选的人保持参考帧里的样子。"
        roles += "没有勾选的商品保持原片；生成一个实际机位的一帧完整画面，不要多角度、分格、拼贴。参考帧里的字幕、贴纸、水印一律不要画出来。"
        roles += "\n" + prompts.CAMERA_SHOT_FIDELITY
        roles += '\n' + prompts.CAMERA_SCENE_CLOTHING
        if any(s['kind'] == 'product' for s in subjects):
            roles += "\n" + prompts.CAMERA_PRODUCT_REFERENCE + "\n" + prompts.CAMERA_PRODUCT_USE
        parts = [{"type": "text", "text": json.dumps({"场景卡": scene, "本场替换对象": subjects,
            "原片人物": (data.get("breakdown") or {}).get("characters") or [],
            "参考帧秒数": frame_info['at'],
            "参考帧所在分镜": [s for s in (data.get('breakdown') or {}).get('shots', [])
                              if scene_plan._interval(s)[0] <= frame_info['at'] < scene_plan._interval(s)[1]],
            "场景说明": (data.get("replacements") or {}).get("scene") or "保持原片场景"}, ensure_ascii=False)}]
        for i, aid in enumerate(refs, 1):
            label = (f"{subjects[i - 1]['name']}的新形象" if subjects[i - 1]["kind"] == "character" else "商品真实外观") if i <= len(subjects) else "原片参考帧（机位、构图、人物站位）"
            parts += llm.image_part(directory / assets[aid]["file"], caption=f"reference image {i}：{label}")
        parts.append({"type": "text", "text": roles})
        result, _ = gateway.reason("camera_prompt", [{"role": "system", "content": SCENE_CAMERA_IMAGE_SYSTEM},
            {"role": "user", "content": parts}], temperature=0.5, max_tokens=4000, json=True, project=pid, override=route)
        draft = {"prompt": roles + "\n\n" + kits.phone_ugc_image_prompt(result.get("person", ""),
            result.get("shot", ""), result.get("setting", "")) + "\n" + prompts.CAMERA_IMAGE_NO_TEXT,
            "image_ids": refs, "note": result.get("note", ""), "anchor_asset_id": None}
    if scene_plan.binding(store.load(pid), scene_id, directory) != binding:
        raise ValueError("这一场的设定刚刚改过，请按最新设定重新生成预览")
    return {**draft, "for_task": f"scene-camera:{scene_id}", "camera_binding": binding,
        "scene_camera_binding": binding, "scene_id": scene_id,
        "frame_at": frame_info["at"], "frame_asset_id": raw_frame["id"],
        "frame_reason": frame_info["reason"], "frame_reason_code": "scene_auto" if frame_info["frame_mode"] == "auto" else "manual",
        "frame_warning": "", "clean_frame_asset_id": frame_info["clean_frame_asset_id"],
        "caption_warning": frame_info["caption_warning"], "caption_overlap_warning": frame_info["caption_overlap_warning"],
        "caption_residue": frame_info["caption_residue"], "characters": frame_info["characters"],
        "present_text": frame_info["present_text"],
        "ratio": (data.get("source") or {}).get("probe", {}).get("ratio_hint", "9:16")}


def use_scene_camera_image(pid: str, scene_id: str, asset_id: str) -> dict:
    with store._lock(pid):
        previous = store.load(pid)
        if scene_id not in {s["id"] for s in replacement_guard.scenes(previous)}:
            raise ValueError("当前项目没有这个场景")
        if not scene_plan.camera_valid(previous, asset_id, scene_id, store.project_dir(pid)):
            raise ValueError("这张图不是本场可用的主镜头图，可能已删除或设定已变，请重新生成")
        def select(current):
            current.setdefault("replacements", {}).setdefault("scene_plan", {}).setdefault(scene_id, {})["camera_asset"] = asset_id
            current.get("camera_notice", {}).pop(f"scene:{scene_id}", None)
        store.update(pid, select)
        try:
            synced = follow_scene_camera_selection(pid, scene_id)
        except Exception:
            store.update(pid, lambda current: current.update(replacements=previous.get("replacements", {}),
                camera_notice=previous.get("camera_notice", {})))
            raise
        result = store.load(pid)
        result["camera_selection"] = {"scene_id": scene_id, "asset_id": asset_id, "synced_tasks": synced,
            "message": "已改用这张主镜头图" + (f"，{'、'.join(synced)} 的图1 已同步" if synced else "")}
        return result


# ---------- 04 Treatment + performable segments ----------

SAFETY_SYSTEM = """你是替换映射安全校对员。输入包含原片 appearance/outfit、productMoments/scene、用户替换映射、每段实际参考图编号以及 action/story/direction。
只把与替换映射冲突的原人物外观、原服装、原商品描述换成对应的 @image 引用（the person from @imageN / the outfit/product from @imageN）。不得新增外观猜测。
用户在替换说明里明确要求的外观可以保留；保持项按原片事实保留。除此以外每一个字都保持不变，尤其时间、动作、台词、声音、机位、场景。不要改写全文、不要润色，不改其它字段。
输出 JSON: {"tasks":[{"id":"原段ID","action":"改后全文","story":"改后全文","direction":"改后全文","changes":[{"field":"action|story|direction","before":"原句或准确子串","after":"替换后"}]}]}。
每条改动须给出准确 before/after；无改动输出空 changes。编号必须与本段 references 一致。缺少对应参考图时不要编造编号，保持原文等待用户补图。"""


def _safety_key(data: dict, task: dict) -> str:
    value = [replacement_guard.source_mappings(data, task), task.get("image_ids"),
             {k: task.get(k) or "" for k in replacement_guard.TEXT_FIELDS}]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def apply_replacement_safety(data: dict, script: dict, previous: dict | None = None) -> None:
    """Checked model fallback, mapped text only. Unchanged saves reuse audit evidence."""
    previous_tasks = {t["id"]: t for t in (previous or {}).get("tasks", [])}
    pending = []
    for task in script.get("tasks", []):
        old = previous_tasks.get(task["id"], {})
        mappings = replacement_guard.source_mappings(data, task)
        if not mappings:
            task.update(replacement_safety=[], replacement_conflicts=[])
            task.pop("replacement_safety_check", None)
            continue
        if old.get("replacement_safety_key") == _safety_key(data, task):
            task["replacement_safety"] = old.get("replacement_safety") or []
            task["replacement_safety_check"] = old.get("replacement_safety_check") or {}
            task["replacement_safety_key"] = old["replacement_safety_key"]
            task["replacement_conflicts"] = replacement_guard.conflicts(data, task)
            continue
        pending.append((task, mappings))
    if not pending:
        return
    provider = script.get("provider")
    model = script.get("request_model") or script.get("model")
    if not provider or not model:
        raise ValueError("旧脚本没有记录生成模型，请重新生成新脚本后再保存修改")
    payload = {"scene": replacement_guard.scenes(data) if scene_edits.fingerprint_edits(data) else (data.get("breakdown") or {}).get("scene", []), "tasks": [
        {"id": t["id"], "mapping": mappings,
         "references": {f"@image{i}": aid for i, aid in enumerate(t.get("image_ids") or [], 1)},
         **{k: t.get(k) or "" for k in replacement_guard.TEXT_FIELDS}} for t, mappings in pending]}
    result, meta = gateway.reason_checked("check", [{"role": "system", "content": SAFETY_SYSTEM},
                                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                              temperature=0, max_tokens=16000, analysis=data.get("analysis"), project=data)
    rows = result.get("tasks")
    if not isinstance(rows, list) or {r.get("id") for r in rows} != {t["id"] for t, _ in pending} or len(rows) != len(pending):
        raise ValueError("映射安全改写缺少段落或包含重复段落，请重新保存/生成脚本")
    # Validate the complete response before applying any of it.
    updates = []
    for task, _ in pending:
        row = next(r for r in rows if r["id"] == task["id"])
        changes = row.get("changes")
        if not isinstance(changes, list):
            raise ValueError("映射安全改写缺少逐条改动清单")
        texts = {k: task.get(k) or "" for k in replacement_guard.TEXT_FIELDS}
        for change in changes:
            field, before, after = change.get("field"), change.get("before"), change.get("after")
            if field not in texts or not isinstance(before, str) or not before or not isinstance(after, str) or before not in texts[field]:
                raise ValueError("映射安全改写清单与原文不一致，请重试")
            texts[field] = texts[field].replace(before, after, 1)
        if any(not isinstance(row.get(k), str) or row[k] != texts[k] for k in texts):
            # The explicit list is the authority. Model full-text drift cannot
            # alter source actions, timing or unrelated prose.
            message = f"{task['id']} 映射安全改写丢弃了清单外的改动，仅采用逐条匹配的修改"
            if data.get('id'):
                store.log(data['id'], message, "warn")
            else:
                logging.getLogger(__name__).warning(message)
        updates.append((task, texts, changes))
    for task, texts, changes in updates:
        task.update(texts)
        for error in task.get("reference_mapping_errors") or []:
            error["text"] = task.get(error["field"]) or ""
        task["replacement_safety"] = (task.get("replacement_safety") or []) + [
            {**change, "provider": meta["provider"], "model": meta["model"]} for change in changes]
        task["replacement_safety_check"] = {k: meta[k] for k in ("provider", "model", "attempts")}
        task["replacement_conflicts"] = replacement_guard.conflicts(data, task)
        task["replacement_safety_key"] = _safety_key(data, task)


def rebase_task_references(task: dict, previous: dict, *, order_before: list[str] | None = None) -> None:
    """Preserve asset identity when the editor changes order without rewriting the text."""
    old, new = previous.get("image_ids") or [], task.get("image_ids") or []
    if order_before is not None:
        if (not isinstance(order_before, list) or not all(isinstance(a, str) for a in order_before)
                or len(set(order_before)) != len(order_before) or len(order_before) != len(new)
                or set(order_before) != set(new)):
            raise ValueError("参考图排序必须是本段已选图片的无重复排列")
        old = order_before
    task["replacement_safety"] = list(previous.get("replacement_safety") or [])
    task["replacement_safety_check"] = previous.get("replacement_safety_check") or {}
    task["reference_mapping_errors"] = [dict(e) for e in previous.get("reference_mapping_errors") or []
                                         if task.get(e["field"]) == e["text"]]
    if old == new:
        return
    for field in replacement_guard.TEXT_FIELDS:
        text = task.get(field) or ""
        if order_before is None and text != (previous.get(field) or ""):
            continue  # ordinary edits already use the new order; explicit repairs use the draft order

        def replace(match):
            index = int(match[1]) - 1
            if index >= len(old) or old[index] not in new:
                task["reference_mapping_errors"].append({"field": field, "text": text,
                    "message": f"{field} 的 {match[0]} 对应参考图已移除，请修改该段文字中的引用或重新生成脚本。"})
                return match[0]
            return "@image" + str(new.index(old[index]) + 1)

        updated = re.sub(r"@image(\d+)\b", replace, text)
        for error in task["reference_mapping_errors"]:
            if error["field"] == field:
                error["text"] = updated
        if text != updated:
            task[field] = updated
            task["replacement_safety"].append({"field": field, "before": text, "after": updated})


def _clip_script_dialogue_lines(lines: list[dict], start: float, end: float, *, allow_sentences: bool = False) -> list[dict]:
    clipped = []
    for row in lines:
        if float(row.get('end') or 0) <= start or float(row.get('start') or 0) >= end:
            continue
        text = evidence.dialogue_between({'segments': [row]}, start, end, midpoint_ownership=allow_sentences)
        if allow_sentences and not row.get('words') and start <= float(row['start']) and float(row['end']) <= end:
            text = row.get('text') or ''  # A whole timed sentence needs no word-level crop.
        elif allow_sentences and not row.get('words') and row.get('text'):
            raise ValueError('这一句台词跨过了分段边界，但转录没有逐词时间；请先在第 02 步完成本地逐词转录，或调整分段边界')
        if text:
            clipped.append({**{k: row[k] for k in ('speaker', 'onScreen', 'evidence') if k in row},
                            'start': max(start, float(row['start'])), 'end': min(end, float(row['end'])), 'text': text})
    return clipped


def _script_dialogue_text(lines: list[dict]) -> str:
    joined = ''
    for row in lines:
        text = row['text']
        if joined and text and not (re.search(r'[㐀-鿿豈-﫿]', joined[-1:] + text[:1]) or joined[-1:] in '，。！？；：、'):
            joined += ' '
        joined += text
    return joined


def _attach_motion_evidence(data: dict, script: dict, directory: Path, *, refresh_dialogue: set[str] | None = None) -> None:
    """Derived evidence is always rebuilt on the server, never trusted from editor JSON."""
    transcript = motion.read_transcript(directory)
    for task in script.get("tasks", []):
        task["source_motion_timeline"] = motion.source_timeline(data, task)
        scoped_keep = task.get('target_seconds') is not None and script.get('dialogue_mode') == 'keep' and task.get('kind') == 'speaker'
        scoped_transcript = transcript
        authored = task.get('dialogue_lines') or []
        keep_authored = bool(authored) and str(task.get('dialogue') or '').split() == _script_dialogue_text(authored).split()
        if scoped_keep and keep_authored and task.get('id') not in (refresh_dialogue or set()):
            scoped_transcript = {'segments': authored}
        elif scoped_keep and transcript.get('segments'):
            a, b = task['source_start'], task['source_end']
            attributed = motion.annotate_speakers(transcript['segments'], task.get('dialogue_lines') or [],
                                                  (data.get('breakdown') or {}).get('characters') or [])
            task['dialogue_lines'] = _clip_script_dialogue_lines(attributed, a, b, allow_sentences=task.get('mode') == 'video')
            task['dialogue'] = _script_dialogue_text(task['dialogue_lines'])
            scoped_transcript = {'segments': task['dialogue_lines']}
        windows, warnings = motion.dialogue_windows({**data, "script": script}, task, transcript=scoped_transcript)
        task["dialogue_windows"], task["motion_warnings"] = windows, warnings
        if not scoped_keep and task.get("mode") == "video" and task.get("kind") == "speaker" and script.get("dialogue_mode") == "keep" and windows and (transcript.get("segments") or task.get("dialogue")):
            task["dialogue"] = " ".join(row["text"] for row in windows)
    script["dialogue"] = [{"segment": t["id"], "text": t["dialogue"]} for t in script.get("tasks", []) if t.get("dialogue")]


def _refresh_target_timing(task: dict) -> None:
    lang = task.get('language') if task.get('language') in evidence.PACE_RATE else 'zh'
    pace = task.get('pace') if task.get('pace') in {'slow', 'normal', 'fast'} else 'normal'
    task['estimate'] = round(evidence.estimate_seconds(task['dialogue'], lang, pace, float(task.get('padding') or 0)), 2) \
        if task.get('kind') == 'speaker' and task.get('dialogue') else None
    task['target_seconds'] = segment_timing.target_seconds({k: v for k, v in task.items() if k != 'target_seconds'})


def save_script(pid: str, script: dict, *, reference_orders: dict | None = None,
                camera_reference_aliases: dict | None = None) -> dict:
    if not isinstance(script, dict):
        raise ValueError("script 格式错误")
    if reference_orders is not None and not isinstance(reference_orders, dict):
        raise ValueError("参考图排序格式错误")
    reference_orders = reference_orders or {}
    if set(reference_orders) - {t["id"] for t in script.get("tasks", [])}:
        raise ValueError("参考图排序包含不存在的段落")
    data = store.load(pid)
    previous = data.get("script") or {}
    # A manual edit cannot renew provenance or change the model used by the safety pass.
    for key in ("provider", "model", "request_model", "replacements_fingerprint"):
        script[key] = previous.get(key)
    for i, task in enumerate(script.get("tasks", []), 1):
        old = next((t for t in previous.get("tasks", []) if t["id"] == task["id"]), {})
        task["performance_cleanup"] = old.get("performance_cleanup") or []
        task["performance_cleanup_check"] = old.get("performance_cleanup_check") or {}
        errors = motion.performance_errors(task)
        if errors:
            raise ValueError("\n".join(errors))
        alias = (camera_reference_aliases or {}).get(task["id"])
        if alias:
            old_id, new_id = alias
            valid = {replacement_guard.camera_image_for(data, c["source_id"], replacement_guard.task_scene(data, task))
                     for c in replacement_guard.replaced_people(data, task)} - {None}
            if scene_plan.enabled(data):
                valid = {scene_plan.camera_image_for(data, replacement_guard.task_scene(data, task))} - {None}
            if (not old.get("image_ids") or old["image_ids"][0] != old_id or new_id not in valid
                    or not task.get("image_ids") or task["image_ids"][0] != new_id):
                raise ValueError("主镜头修复映射已变化，请刷新后重试")
            old = {**old, "image_ids": [new_id] + old["image_ids"][1:],
                   "reference_mapping_errors": [e for e in old.get("reference_mapping_errors") or []
                       if not ("@image1" in e.get("message", "") and "已移除" in e.get("message", ""))]}
        rebase_task_references(task, old, order_before=reference_orders.get(task["id"]))
        task["duration"] = int(task.get("duration") or 8)
        if not 4 <= task["duration"] <= 15:
            raise ValueError(f"{task['id']} 时长需为 4–15 秒整数")
        if task.get("ratio") not in {"9:16", "16:9", "1:1"}:
            raise ValueError(f"{task['id']} 比例只能是 9:16 / 16:9 / 1:1")
        if len(task.get("image_ids") or []) > 4:
            raise ValueError(f"{task['id']} 最多 4 张参考图")
        task["order"] = i
        task.setdefault("blank_captions", False)
        if replacement_guard.check(data, task)["force_mute"]:
            task["mute_clip"] = True
    attribution_changed = any(old.get("dialogue_lines") != task.get("dialogue_lines")
        for task in script.get("tasks", []) for old in previous.get("tasks", []) if old.get("id") == task.get("id"))
    speaker_only = attribution_changed and len(script.get("tasks", [])) == len(previous.get("tasks", [])) and all(
        any(old.get("id") == task.get("id") and _safety_key(data, old) == _safety_key(data, task)
            and all(old.get(k) == task.get(k) for k in ("dialogue", "source_start", "source_end", "kind", "mode"))
            for old in previous.get("tasks", [])) for task in script.get("tasks", []))
    refresh_dialogue = {task['id'] for task in script.get('tasks', []) for old in
                        [next((t for t in previous.get('tasks', []) if t['id'] == task['id']), {})]
                        if any(task.get(k) != old.get(k) for k in ('source_start', 'source_end'))}
    _attach_motion_evidence(data, script, store.project_dir(pid), refresh_dialogue=refresh_dialogue)
    for task in script.get('tasks', []):
        old = next((t for t in previous.get('tasks', []) if t['id'] == task['id']), {})
        if any(task.get(k) != old.get(k) for k in ('source_start', 'source_end', 'kind', 'dialogue', 'pace', 'padding', 'language')):
            _refresh_target_timing(task)
    if not speaker_only:
        apply_replacement_safety(data, script, previous)
    if camera_reference_aliases:
        # A deliberate camera repair is checked against current replacements by
        # the same safety pass before renewing its mapping provenance.
        script["replacements_fingerprint"] = replacement_guard.fingerprint(data, store.project_dir(pid))
    errors = [error for task in script.get("tasks", []) for error in motion.performance_errors(task)]
    if errors:
        raise ValueError("\n".join(errors))

    def commit(current):
        if current.get("script") != previous or current.get("replacements") != data.get("replacements"):
            raise ValueError("刚才有另一次保存先完成了，请刷新到最新内容，检查后再操作")
        current["script"] = script
        for runs in current.get("runs", {}).values():
            for run in runs:
                if run.get("state") == "pending_confirmation":
                    run["state"] = "superseded"
    return store.update(pid, commit)

def _replace_camera_reference(task: dict, current: str) -> tuple[str, str]:
    old_id = task["image_ids"][0]
    task["image_ids"][0] = current
    # Historical drafts sometimes used the internal scene frame as image3 but
    # shipped only camera + product. Repair only explicit scene-context phrases.
    scene_reference = re.compile(r"((?:scene|background|location|setting|lot|room|场景|背景|机位)\s+(?:from\s+)?)@image(\d+)\b", re.I)
    for field in replacement_guard.TEXT_FIELDS:
        task[field] = scene_reference.sub(lambda match: match[1] + "@image1"
            if int(match[2]) > len(task["image_ids"]) else match[0], task.get(field) or "")
    return old_id, current


def follow_camera_selection(pid: str, char_id: str, scene_id: str, *, report_failure: bool = False) -> list[str]:
    # Batch completions for separate scenes still share a script and provenance.
    with store._lock(pid):
        if scene_plan.enabled(store.load(pid)):
            return follow_scene_camera_selection(pid, scene_id, report_failure=report_failure)
        return _follow_camera_selection_locked(pid, char_id, scene_id, report_failure=report_failure)


def follow_scene_camera_selection(pid: str, scene_id: str, *, report_failure: bool = False) -> list[str]:
    """Same-scene camera swaps preserve reference numbers and all task text."""
    with store._lock(pid):
        data = store.load(pid)
        directory = store.project_dir(pid)
        current = scene_plan.camera_image_for(data, scene_id, directory)
        if not current:
            return []
        script = copy.deepcopy(data.get('script') or {})
        assets = {a['id']: a for a in data.get('assets', [])}
        synced = []
        try:
            for task in script.get('tasks', []):
                ids = task.get('image_ids') or []
                def same_scene_camera(aid):
                    sid, cid, new = scene_plan._asset_scene(data, assets.get(aid) or {'id': aid})
                    return sid == scene_id and (new or cid is not None)
                replaced = [current if aid != current and same_scene_camera(aid) else aid for aid in ids]
                if replaced != ids:
                    task['image_ids'] = replaced
                    synced.append(task['id'])
            was_current = replacement_guard.script_current(data, directory)
            def commit(row):
                if row.get('script') != data.get('script') or row.get('replacements') != data.get('replacements'):
                    raise ValueError('刚才有另一次保存先完成了，请刷新后重试')
                if synced:
                    # Renew the format only with verified old provenance. A
                    # camera swap must not conceal a real casting change.
                    if was_current:
                        script['replacements_fingerprint'] = replacement_guard.fingerprint(row, directory)
                    row['script'] = script
                    for tid in synced:
                        for run in row.get('runs', {}).get(tid, []):
                            if run.get('state') == 'pending_confirmation':
                                run['state'] = 'superseded'
                notices = row.get('camera_notice', {})
                if '同步失败' in notices.get(f'scene:{scene_id}', ''):
                    notices.pop(f'scene:{scene_id}', None)
            store.update(pid, commit)
        except Exception as exc:
            if not report_failure:
                raise
            message = f'{scene_id} 在用图已更新，但图1 同步失败：{exc}。请重试同步'
            store.update(pid, lambda row: row.setdefault('camera_notice', {}).__setitem__(f'scene:{scene_id}', message))
            store.log(pid, message, 'warn')
            return []
        return synced


def _follow_camera_selection_locked(pid: str, char_id: str, scene_id: str, *, report_failure: bool) -> list[str]:
    data = store.load(pid)
    script = data.get("script") or {}
    current = replacement_guard.camera_image_for(data, char_id, scene_id)
    if not current:
        return []
    aliases = {}
    for task in script.get("tasks", []):
        images = task.get("image_ids") or []
        if (not images or images[0] == current or replacement_guard.task_scene(data, task) != scene_id
                or char_id not in {c["source_id"] for c in replacement_guard.replaced_people(data, task)}
                or not replacement_guard.camera_reference_matches(data, images[0], char_id, scene_id)):
            continue
        aliases[task["id"]] = _replace_camera_reference(task, current)
    if not aliases:
        return []
    try:
        save_script(pid, script, camera_reference_aliases=aliases)
    except Exception as exc:
        if not report_failure:
            raise
        # Generation/deletion already completed; never relabel a paid image as failed.
        message = f"{scene_id} 在用图已更新，但图1 同步失败：{exc}。请在第 04 步（对照和修改）里这一段的新分镜中修复后重新生成预览"
        def notice(current):
            notices = current.setdefault("camera_notice", {})
            key = f"{char_id}:{scene_id}"
            notices[key] = (notices[key] + "；" if notices.get(key) else "") + message
        store.update(pid, notice)
        store.log(pid, message, "warn")
        return []
    label = next(a.get("label") or current for a in data["assets"] if a["id"] == current)
    store.log(pid, f"{scene_id} 在用的主镜头图换成“{label}”，{'、'.join(aliases)} 的图1 已同步")
    return list(aliases)


def repair_camera_reference(pid: str, task_id: str, char_id: str, scene_id: str) -> dict:
    data = store.load(pid)
    script = data.get("script") or {}
    task = next((t for t in script.get("tasks", []) if t.get("id") == task_id), None)
    if task is None or not task.get("image_ids"):
        raise ValueError("分段或图1不存在")
    if scene_plan.enabled(data):
        if scene_id != replacement_guard.task_scene(data, task):
            raise ValueError('场景与当前分段不一致')
        if not scene_plan.camera_image_for(data, scene_id, store.project_dir(pid)):
            raise ValueError('本场景没有有效主镜头图，请先到第 03 步准备')
        follow_scene_camera_selection(pid, scene_id)
        return store.load(pid)
    if scene_id != replacement_guard.task_scene(data, task) or char_id not in {
            c["source_id"] for c in replacement_guard.replaced_people(data, task)}:
        raise ValueError("人物或场景与当前分段不一致")
    current = replacement_guard.camera_image_for(data, char_id, scene_id)
    if not current:
        raise ValueError("本场景没有有效主镜头图，请先到第 03 步准备")
    return save_script(pid, script, camera_reference_aliases={task_id: _replace_camera_reference(task, current)})


def _warn_language_mismatch(pid: str, transcript: dict | None, overview: dict) -> None:
    detected = (transcript or {}).get("language")
    described = overview.get("language")
    aliases = {"中文": "zh", "汉语": "zh", "普通话": "zh", "chinese": "zh", "mandarin": "zh",
               "英文": "en", "英语": "en", "english": "en", "日语": "ja", "japanese": "ja",
               "西班牙语": "es", "spanish": "es"}

    def normalize(value):
        value = str(value).strip().lower()
        return aliases.get(value, value.replace("_", "-").split("-")[0])

    if detected and described and normalize(detected) != normalize(described):
        store.log(pid, f"转录检测语言（{detected}）与整片理解语言（{described}）不一致，请检查转录与语言设置。", "warn")


def _split_script_segment(seg: dict, points: list[float]) -> list[dict]:
    """Split source coverage without repeating rewritten speech across requests."""
    text = seg.get("dialogue") or ""
    tokens = list(re.finditer(r"[㐀-鿿豈-﫿]|[^㐀-鿿豈-﫿\s]+\s*", text))
    length = seg["sourceEnd"] - seg["sourceStart"]
    offsets = [0]
    for point in points[1:-1]:
        index = int(len(tokens) * (point - points[0]) / length + 0.5)
        offsets.append(tokens[index].start() if index < len(tokens) else len(text))
    offsets.append(len(text))
    out = []
    for i, (a, b) in enumerate(zip(points, points[1:])):
        out.append({**seg, "sourceStart": a, "sourceEnd": b,
                    "dialogue": text[offsets[i]:offsets[i + 1]].strip(),
                    "sceneChange": seg.get("sceneChange", False) if i == 0 else False})
    return out


def _merge_script_segments(group: list[dict], cuts: set[float]) -> dict:
    merged = {**group[0], "sourceEnd": max(s["sourceEnd"] for s in group)}
    if len(group) == 1:
        return merged

    def direction(seg, key):
        text = seg.get(key) or ""
        if seg["imageIds"] == merged["imageIds"]:
            return text

        def rebase(match):
            old = int(match[2]) - 1
            if not 0 <= old < len(seg["imageIds"]):
                return match[0]
            return match[1] + str(merged["imageIds"].index(seg["imageIds"][old]) + 1)

        # Keep the English direction intact, correcting only reference numbers
        # when the same image set was supplied in a different authored order.
        return re.sub(r"(@image|\bimage\s+)(\d+)\b", rebase, text, flags=re.IGNORECASE)

    for key in ("action", "story", "direction"):
        merged[key] = "\n".join(direction(s, key) for s in group if s.get(key))
    for key in ("dialogue", "footageReason"):
        merged[key] = "\n".join(s[key] for s in group if s.get(key))
    merged["overlays"] = [o for s in group for o in s.get("overlays") or []]
    if merged.get("useSourceFootage"):
        spoken = next((s for s in group if s.get("kind") == "speaker" and (s.get("dialogue") or s.get("_has_speech"))), None)
        merged["kind"] = (spoken or max(group, key=lambda s: s["sourceEnd"] - s["sourceStart"]))["kind"]
    if not merged.get("sceneChange") and merged["sourceStart"] not in cuts:
        merged["splitReason"] = "连续镜头、参考图一致，合并剧情节拍"
    return merged


def _complete_scene_coverage(data: dict, segments: list[dict], source_lines: list[dict]) -> list[dict]:
    """Clip to retained scenes and repair every gap before refs, speech and duration."""
    scenes = replacement_guard.scenes(data)
    spans = [(scene, a, b) for scene in scenes for a, b in replacement_guard.scene_ranges(scene)]
    if not spans:
        return segments

    def clip(seg, a, b):
        points = sorted({seg['sourceStart'], a, b, seg['sourceEnd']})
        return next(p for p in _split_script_segment(seg, points) if p['sourceStart'] == a and p['sourceEnd'] == b)

    # Image generation keeps the old short-cross-scene behavior: only clip it
    # at holes in retained footage, not at every adjacent scene boundary.
    union = []
    for a, b in sorted((a, b) for _, a, b in spans):
        if union and a <= union[-1][1]:
            union[-1][1] = max(union[-1][1], b)
        else:
            union.append([a, b])
    images = [clip(seg, max(a, seg['sourceStart']), min(b, seg['sourceEnd']))
              for seg in segments if not seg['useSourceFootage'] for a, b in union
              if seg['sourceEnd'] > a and seg['sourceStart'] < b]
    out = []
    for scene, start, end in spans:
        parts = []
        candidates = [clip(seg, max(start, seg['sourceStart']), min(end, seg['sourceEnd']))
                      for seg in segments if seg['useSourceFootage'] and seg['sourceEnd'] > start and seg['sourceStart'] < end]
        candidates += [seg for seg in images if seg['sourceEnd'] > start and seg['sourceStart'] < end]
        for seg in sorted(candidates, key=lambda s: s["sourceStart"]):
            part = seg
            a, b = seg['sourceStart'], seg['sourceEnd']
            # Overlapping model rows must not duplicate footage in the final edit.
            if parts and a < parts[-1]["sourceEnd"]:
                a = parts[-1]["sourceEnd"]
                if a >= b:
                    continue
                part = _split_script_segment(part, [part["sourceStart"], a, b])[-1]
            if part['useSourceFootage']:
                part["scene_id"] = scene["id"]
            parts.append(part)
        if not parts:
            dialogue = [row for row in source_lines if float(row.get("end") or 0) > start and float(row.get("start") or 0) < end]
            shots = [row for row in (data.get("breakdown") or {}).get("shots") or []
                     if float(row.get("end") or 0) > start and float(row.get("start") or 0) < end]
            spoken = any(row.get("text") or row.get("dialogue") for row in dialogue) or any(
                row.get("dialogue") or row.get("speech") for row in shots)
            parts = [{"sourceStart": start, "sourceEnd": end, "kind": "speaker" if spoken else "broll",
                      "scene_id": scene["id"], "useSourceFootage": True, "imageIds": [],
                      "title": scene.get("name") or scene["id"],
                      "splitReason": "补齐：模型漏掉了这一场"}]
        parts[0]["sourceStart"] = min(start, parts[0]['sourceStart'])
        for left, right in zip(parts, parts[1:]):
            if left["sourceEnd"] < right["sourceStart"]:
                left["sourceEnd"] = right["sourceStart"]
        parts[-1]["sourceEnd"] = max(end, parts[-1]['sourceEnd'])
        # In keep mode model speech is empty: preserve speaker priority using
        # timed evidence, without copying transcript text before final splitting.
        for part in parts:
            if part["kind"] == "speaker":
                part["_has_speech"] = bool(part.get("dialogue")) or any(
                    row.get("text") and float(row.get("end") or 0) > part["sourceStart"] and
                    float(row.get("start") or 0) < part["sourceEnd"] for row in source_lines)
        out.extend(parts)
    # A short image segment may cover multiple adjacent scenes. Emit it once.
    unique = {id(part): part for part in out}
    covered = []
    for part in sorted(unique.values(), key=lambda s: s['sourceStart']):
        if covered and part['sourceStart'] < covered[-1]['sourceEnd']:
            a, b = covered[-1]['sourceEnd'], part['sourceEnd']
            if b <= a:
                continue
            part = clip(part, a, b)
        covered.append(part)
    return covered


def _script_segments(raw: list[dict], boundaries: dict, imgs: list[str], *, data: dict | None = None,
                     source_lines: list[dict] | None = None, dialogue_mode: str | None = None,
                     language: str = 'zh') -> list[dict]:
    """Enforce cuts and reference continuity before deriving dialogue or duration."""
    shots = sorted(boundaries.get("merged_shots") or [], key=lambda s: s["start"])
    cuts = {float(s["start"]) for s in shots[1:]}
    normalized = []
    for original in raw:
        seg = dict(original)
        try:
            a, b = float(seg.get("sourceStart") or 0), float(seg.get("sourceEnd") or 0)
        except (TypeError, ValueError):
            raise ValueError("脚本段落的原片起止时间必须是数字")
        if not math.isfinite(a) or not math.isfinite(b) or a < 0 or b <= a:
            raise ValueError("脚本段落的原片区间必须有效且结束时间大于开始时间")
        kind = seg.get("kind") if seg.get("kind") in {"speaker", "broll", "motion", "camera"} else "speaker"
        refs = seg.get("imageIds", seg.get("image_ids"))
        if refs is None:
            refs = list(imgs)
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
            raise ValueError("脚本参考图 imageIds 必须是素材编号列表")
        seg.update(sourceStart=a, sourceEnd=b, kind=kind, imageIds=list(dict.fromkeys(refs)),
                   useSourceFootage=bool(seg.get("useSourceFootage")) or seg.get("mode") == "video" or kind in {"motion", "camera"})
        if seg.get("sceneChange") is True and not seg["useSourceFootage"]:
            cuts.add(a)
        normalized.append(seg)

    if data is not None:
        normalized = _scene_segments(data, _complete_scene_coverage(data, normalized, source_lines or []))

    pieces = []
    for seg in sorted(normalized, key=lambda s: s["sourceStart"]):
        a, b = seg["sourceStart"], seg["sourceEnd"]
        points = [a] + sorted(c for c in cuts if a < c < b and not seg["useSourceFootage"]) + [b]
        split = _split_script_segment(seg, points) if len(points) > 2 else [seg]
        for part in split:
            if part["sourceStart"] in cuts and not part["useSourceFootage"]:
                part["splitReason"] = part.get("splitReason") if part.get("sceneChange") else "真实镜头切换或场景变化"
        pieces.extend(split)

    def request_seconds(group):
        merged = _merge_script_segments(group, set())
        span = merged['sourceEnd'] - merged['sourceStart']
        if not merged['useSourceFootage'] or merged['kind'] != 'speaker':
            return span
        text = (_script_dialogue_text(_clip_script_dialogue_lines(source_lines or [], merged['sourceStart'], merged['sourceEnd'], allow_sentences=True))
                if dialogue_mode == 'keep' else merged.get('dialogue') or '')
        pace = merged.get('pace') if merged.get('pace') in {'slow', 'normal', 'fast'} else 'normal'
        lang = language if language in evidence.PACE_RATE else 'zh'
        try:
            padding = float(merged.get('padding') or 0)
        except (ValueError, TypeError):
            padding = 0
        estimate = round(evidence.estimate_seconds(text, lang, pace, padding), 2) if text else 0
        return estimate if estimate > span + 1 else span

    # Choose the fewest legal requests, then the fewest sub-4-second pieces.
    # Greedily merging the shortest pair can trap a continuous shot in 3 pieces
    # where 2 would suffice (e.g. source lengths 6, 5, 5, 6).
    best = {len(pieces): (0, 0, 0.0)}
    following = {}
    edited_scenes = data is not None and bool(scene_edits.fingerprint_edits(data))
    for i in range(len(pieces) - 1, -1, -1):
        left = pieces[i]
        a, b = left["sourceStart"], left["sourceEnd"]
        for j in range(i, len(pieces)):
            right = pieces[j]
            if j > i and data is not None and right['sourceStart'] > b:
                break  # Never merge across a hole, even within a recurring scene.
            b = max(b, right["sourceEnd"])
            video = left["useSourceFootage"]
            same = ((video or left["kind"] == right["kind"]) and video == right["useSourceFootage"]
                    and (not (video or edited_scenes) or left.get("scene_id") == right.get("scene_id"))
                    and set(left["imageIds"]) == set(right["imageIds"]))
            if j > i and not same:
                break
            # Integer requests allow a 15.167 s source to remain one 15 s
            # request, rather than manufacturing a sub-second trailing clip.
            seconds = request_seconds(pieces[i:j+1]) if video else b-a
            too_long = seconds > 15 if video else math.floor(b - a + 0.5) > 15
            if j > i and (not same or (not video and any(a < c < b for c in cuts)) or too_long):
                break
            count = math.ceil(seconds / 15) if too_long else 1
            tail = best[j + 1]
            cost = (count + tail[0], int(b - a < 4) + tail[1], (b - a) ** 2 / count + tail[2])
            if i not in best or cost < best[i]:
                best[i], following[i] = cost, j + 1
    groups, i = [], 0
    while i < len(pieces):
        j = following[i]
        groups.append(_merge_script_segments(pieces[i:j], cuts))
        i = j

    out = []
    pending = list(groups)
    while pending:
        seg = pending.pop(0)
        a, b = seg["sourceStart"], seg["sourceEnd"]
        seconds = request_seconds([seg]) if seg['useSourceFootage'] else b-a
        if (seconds > 15 if seg["useSourceFootage"] else math.floor(b - a + 0.5) > 15):
            if b-a < 1e-6:
                raise ValueError('这一处台词估算超过 15 秒，请缩短台词或调整说话节奏')
            count = math.ceil(seconds / 15)
            points = [a + (b - a) * i / count for i in range(count)] + [b]
            parts = _split_script_segment(seg, points)
            for part in parts:
                part["splitReason"] = "生成长度上限：单段请求不超过 15 秒"
            pending[:0] = parts
        else:
            if seg["useSourceFootage"] and seg.get("splitReason") not in {
                    "补齐：模型漏掉了这一场", "生成长度上限：单段请求不超过 15 秒"}:
                seg["splitReason"] = "同一场，镜头切换由参考视频保留"
            else:
                seg.setdefault("splitReason", "连续镜头，无需切分")
            out.append(seg)
    for left, right in zip(out, out[1:]):
        if set(left["imageIds"]) != set(right["imageIds"]) and not (
                right["useSourceFootage"] and left.get("scene_id") != right.get("scene_id")):
            right["splitReason"] = "参考图变化：人物、商品或场景使用不同参考图"
    return _scene_segments(data, out) if data is not None else out



def _retained_script_segments(data: dict, segments: list[dict]) -> list[dict]:
    """Intersect source coverage with retained scenes, splitting holes and speech."""
    if not scene_edits.fingerprint_edits(data):
        return segments
    kept = [(a,b) for scene in replacement_guard.scenes(data) for a,b in replacement_guard.scene_ranges(scene)]
    out = []
    for segment in segments:
        a,b = segment["sourceStart"],segment["sourceEnd"]
        points = sorted({a,b} | {p for span in kept for p in span if a < p < b})
        for part in _split_script_segment(segment,points):
            x,y = part["sourceStart"],part["sourceEnd"]
            if y > x and any(start <= x+1e-9 and y <= end+1e-9 for start,end in kept):
                out.append(part)
    return out


def _scene_segments(data: dict, segments: list[dict]) -> list[dict]:
    """Use exact scene cuts for video; retain image mode's four-second rule."""
    out = []
    for seg in _retained_script_segments(data, segments):
        a, b = seg["sourceStart"], seg["sourceEnd"]
        overlaps = [r for r in replacement_guard.scene_overlaps(data, a, b) if r["overlap"] >= 1]
        points = sorted({a, b} | {p for scene in replacement_guard.scenes(data)
                                 for interval in replacement_guard.scene_ranges(scene) for p in interval if a < p < b})
        video = bool(seg.get("useSourceFootage")) or seg.get("mode") == "video" or seg.get("kind") in {"motion", "camera"}
        split = (video and len(points) > 2) or (len(overlaps) > 1 and all(y - x >= 4 for x, y in zip(points, points[1:])))
        parts = _split_script_segment(seg, points) if split else [dict(seg)]
        for part in parts:
            task = {"source_start": part["sourceStart"], "source_end": part["sourceEnd"],
                    "mode": "video" if part.get("mode") == "video" or part.get("useSourceFootage") or part.get("kind") in {"motion", "camera"} else "image"}
            sid = replacement_guard.task_scene(data, task)
            part["scene_id"] = sid
            refs = default_images(data, task)
            # Preserve authored supplemental refs while replacing the main camera slot.
            old = part.get("imageIds") or []
            camera_ids = {a["id"] for a in data.get("assets", []) if a.get("camera_character") or
                          scene_plan.enabled(data) and (a.get("scene_camera_scene") or a.get("scene_camera_binding"))}
            camera_ids.update(replacement_guard.camera_image_for(data, c["source_id"], s["id"])
                              for c in replacement_guard.replaced_people(data) for s in replacement_guard.scenes(data))
            if scene_plan.enabled(data):
                camera_ids.update(r.get("asset_id") or r.get("id") for r in (data.get("images") or {}).values()
                                  if (r.get("for_task") or "").startswith(("camera:", "scene-camera:")))
            portraits = {c.get("asset_id") for c in replacement_guard.replaced_people(data)}
            if scene_plan.enabled(data):
                portraits.update(s.get("asset_id") for scene in replacement_guard.scenes(data)
                                 for s in scene_plan.effective_subjects(data, scene["id"]))
                portraits.update(c.get("asset_id") for c in (data.get("replacements") or {}).get("characters", []))
                portraits.add(((data.get("replacements") or {}).get("product") or {}).get("asset_id"))
            if len(replacement_guard.scenes(data)) == 1 and task["mode"] != "video" and not scene_plan.enabled(data):
                refs = list(old) if old else refs
            else:
                refs += [aid for aid in old if aid not in camera_ids | portraits and aid not in refs]
            part["scene_references"] = []
            if split:
                part["splitReason"] = "场景变化：不同场景使用不同的主镜头图"
            elif len(overlaps) > 1:
                extras = []
                for other in overlaps[1:]:
                    if scene_plan.enabled(data):
                        aid = scene_plan.camera_image_for(data, other["scene_id"])
                        if aid and aid not in refs and aid not in extras:
                            extras.append(aid)
                            part["scene_references"].append({"scene_id": other["scene_id"], "asset_id": aid,
                                "start": round(other["start"] - a, 3)})
                        continue
                    for c in replacement_guard.replaced_people(data, task):
                        aid = replacement_guard.camera_image_for(data, c["source_id"], other["scene_id"])
                        if aid and aid not in refs and aid not in extras:
                            extras.append(aid)
                            part["scene_references"].append({"scene_id": other["scene_id"], "asset_id": aid,
                                                             "start": round(other["start"] - a, 3)})
                refs = refs[:1] + extras + refs[1:]
            reference_ids = list(dict.fromkeys(aid for aid in refs if any(a["id"] == aid for a in data.get("assets", []))))
            if scene_plan.enabled(data) and len(reference_ids) > 4:
                raise ValueError(f"场景 {sid} 的这一段需要 {len(reference_ids)} 张参考图，视频每段最多 4 张，请减少本场替换对象或参考图")
            part["imageIds"] = reference_ids if scene_plan.enabled(data) else reference_ids[:4]
            remapped = list(old)
            for c in replacement_guard.replaced_people(data, task):
                known = {c.get("asset_id")} | {replacement_guard.camera_image_for(data, c["source_id"], scene["id"])
                                                for scene in replacement_guard.scenes(data)}
                known.update(asset["id"] for asset in data.get("assets", []) if asset.get("camera_character") == c["source_id"])
                index = next((i for i, aid in enumerate(old) if aid in known), None)
                if index is not None:
                    remapped[index] = replacement_guard.camera_image_for(data, c["source_id"], sid) or c.get("asset_id")

            def rebase(match):
                index = int(match[2]) - 1
                if 0 <= index < len(remapped) and remapped[index] in part["imageIds"]:
                    return match[1] + str(part["imageIds"].index(remapped[index]) + 1)
                if scene_plan.enabled(data):
                    if 0 <= index < len(old):
                        aid = old[index]
                        rows = scene_plan.effective_subjects(data, sid)
                        for character in (data.get("replacements") or {}).get("characters", []):
                            cid = character.get("source_id")
                            row = next((s for s in rows if s["key"] == cid), {})
                            if not row.get("on") and aid in {character.get("asset_id"), row.get("asset_id")}:
                                return f"the original {cid} in the source scene"
                        product = next((s for s in rows if s["key"] == "product"), {})
                        if not product.get("on") and aid in {product.get("asset_id"),
                                ((data.get("replacements") or {}).get("product") or {}).get("asset_id")}:
                            return "the original product in the source scene"
                    raise ValueError(f"这一段的 {match[0]} 引用了本场已移除的参考图，请重新生成脚本或修正引用")
                return match[0]

            for field in replacement_guard.TEXT_FIELDS:
                if part.get(field):
                    part[field] = re.sub(r"(@image|\bimage\s+)(\d+)\b", rebase, part[field], flags=re.I)
            out.append(part)
    return out

def run_script(pid: str, provider: str, dialogue_mode: str, instruction: str, target_range: list[float] | None,
               *, review_dialogue: bool = False) -> None:
    route = gateway.routes.resolve('script', pid, provider)
    provider = route['channel']
    data = store.load(pid)
    if not data.get("breakdown"):
        raise ValueError("请先完成拆解")
    d = store.project_dir(pid)
    ver = (data.get("breakdown_versions") or {}).get(data.get("breakdown_provider") or "", {})
    transcript = None
    raw = d / "analysis" / "transcript.raw.json"
    if raw.exists():
        transcript = json.loads(raw.read_text(encoding="utf-8"))
    boundaries_file = d / "analysis" / "boundaries.json"
    boundaries = json.loads(boundaries_file.read_text(encoding="utf-8")) if boundaries_file.exists() else {}
    requested_dialogue_mode = dialogue_mode
    propose_rewrite = dialogue_mode == "rewrite" and bool(review_dialogue or replacement_guard.replaced_people(data) or
        ((data.get("replacements") or {}).get("product") or {}).get("action") == "replace")
    if propose_rewrite:
        dialogue_mode = "keep"
    replacements = build_replacements(data)
    replacements_fingerprint = replacement_guard.fingerprint(data, d)
    replacements["可用参考图"] = [{"id": a["id"], "label": a.get("label", ""), "roles": a.get("roles", [])}
                                 for a in data.get("assets", []) if a.get("type") == "image"]
    script_pack = data["breakdown"]
    if scene_edits.fingerprint_edits(data):
        script_pack = {**script_pack, "scene":replacement_guard.scenes(data)}
        dropped = [s for s in scene_edits.effective_scenes(data, include_dropped=True) if s.get("dropped")]
        if dropped:
            replacements["不复刻的原片区间"] = "以下原片时间段不复刻，成片里不出现：" + "、".join(
                f"{a:.2f}–{b:.2f} 秒（{s['id']} {s.get('name') or ''}）" for s in dropped for a,b in scene_edits.ranges(s))
        replacements["分段规则"] = "只按保留的场景分段；合并后的场景只算一场，不再按合并前的场景边界切段。"
    messages = [{"role": "system", "content": prompts.SCRIPT_SYSTEM},
                {"role": "user", "content": prompts.script_user_text(
                    script_pack, ver.get("analysis_md", ""), ver.get("timeline_md", ""), transcript,
                    replacements, dialogue_mode, instruction, target_range, boundaries)}]
    result, meta = gateway.reason('script', messages, temperature=0.5, max_tokens=16000, json=True, project=pid, override=route)
    ratio = (data["source"] or {}).get("probe", {}).get("ratio_hint", "9:16")
    lang = ((transcript or {}).get("language") or data["breakdown"].get("overview", {}).get("language") or "zh")[:2]
    lang = lang if lang in evidence.PACE_RATE else "zh"
    imgs = default_images(data)
    source_lines = (transcript or {}).get("segments") or data["breakdown"].get("dialogue") or []
    known = [r for r in data["breakdown"].get("dialogue") or [] if r.get("speaker") not in (None, "", "unknown")]
    attributed = motion.annotate_speakers(source_lines, result.get("lineSpeakers") or [], data["breakdown"].get("characters") or [])
    known_rows = motion.annotate_speakers(source_lines, known, data["breakdown"].get("characters") or [])
    attributed = [k if k["speaker"] != "unknown" else r for r, k in zip(attributed, known_rows)]
    tasks = []
    raw_segments = result.get("segments") or []
    for i, seg in enumerate(_script_segments(raw_segments, boundaries, imgs, data=data, source_lines=source_lines,
                                           dialogue_mode=dialogue_mode, language=lang), start=1):
        kind = seg.get("kind") if seg.get("kind") in {"speaker", "broll", "motion", "camera"} else "speaker"
        try:
            src_a, src_b = float(seg.get("sourceStart") or 0), float(seg.get("sourceEnd") or 0)
        except (TypeError, ValueError):
            src_a = src_b = 0.0
        pace = seg.get("pace") if seg.get("pace") in {"slow", "normal", "fast"} else "normal"
        try:
            padding = float(seg.get("padding") or 0)
        except (TypeError, ValueError):
            padding = 0.0
        if dialogue_mode == "keep" and kind == "speaker":
            seg["dialogue"] = _script_dialogue_text(_clip_script_dialogue_lines(attributed, src_a, src_b,
                                                                           allow_sentences=seg['useSourceFootage']))
        est = round(evidence.estimate_seconds(seg.get("dialogue") or "", lang, pace, padding), 2) \
            if kind == "speaker" and seg.get("dialogue") else None
        seconds = est if est is not None and est > src_b - src_a + 1 else src_b - src_a
        duration = min(15, max(4, math.floor(seconds + 0.5)))
        use_src = bool(seg.get("useSourceFootage")) or seg.get("mode") == "video" or kind in {"motion", "camera"}
        tasks.append({
            "id": f"seg-{i:02d}", "order": i, "title": seg.get("title") or f"第{i}段", "kind": kind,
            "source_start": src_a, "source_end": src_b, "duration": duration, "target_seconds": seconds, "ratio": ratio,
            "mode": "video" if use_src else "image", "footage_reason": seg.get("footageReason", ""),
            "split_reason": seg.get("splitReason", ""), "scene_id": seg["scene_id"],
            "scene_references": seg.get("scene_references") or [],
            "dialogue": seg.get("dialogue", ""),
            "dialogue_lines": (_clip_script_dialogue_lines(attributed, src_a, src_b, allow_sentences=use_src) if dialogue_mode == 'keep' else
                               [{k: row[k] for k in ("start", "end", "text", "speaker", "onScreen", "evidence")}
                                for row in attributed if row["end"] > src_a and row["start"] < src_b]) if kind == "speaker" else [],
            "action": seg.get("action", ""), "story": seg.get("story", ""),
            "direction": seg.get("direction", ""), "recipe": seg.get("recipe") or {}, "pace": pace,
            "padding": padding, "estimate": est, "language": lang, "overlays": seg.get("overlays") or [],
            "prompt": "", "start_state": "", "end_state": "",
            "image_ids": seg["imageIds"], "identity_reference_version": 1,
            "mute_clip": True, "blank_captions": False, "audio_id": None, "voice_approved": False,
        })
    (d / "analysis" / "TREATMENT.md").write_text(result.get("treatment_md") or "", encoding="utf-8")
    script = {"provider": provider, "model": meta["model"], "request_model": meta.get("requested_model", meta["model"]),
              "replacements_fingerprint": replacements_fingerprint, "dialogue_mode": dialogue_mode,
              "instruction": instruction, "requested_dialogue_mode": requested_dialogue_mode,
              "summary": result.get("summary", ""),
              "treatment_md": result.get("treatment_md", ""), "tasks": tasks, "at": time.strftime("%H:%M:%S"),
              "dialogue": [{"segment": t["id"], "text": t["dialogue"]} for t in tasks if t["dialogue"]]}
    _attach_motion_evidence(data, script, d)
    apply_replacement_safety(data, script)
    motion.cleanup_performance(data, script)
    def save_generated_script(current):
        current.update(script=script)
        current["motion_warning_epoch"] = store.new_id("script")
        current.pop("motion_warning_dismissals", None)
    store.update(pid, save_generated_script)
    if propose_rewrite:
        for task in tasks:
            if task.get("kind") == "speaker" and task.get("dialogue"):
                dialogue_proposal(pid, task["id"], instruction)
    store.log(pid, f"Treatment 与 {len(tasks)} 个可表演段落已生成（{gateway.label(provider)}）")
