"""Local media helpers built on ffmpeg/ffprobe (no cloud services)."""
from __future__ import annotations

import json
import math
import re
import shutil
import statistics
import subprocess
from pathlib import Path


class MediaError(RuntimeError):
    pass


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise MediaError(f"找不到 {tool}，请重新运行安装脚本（macOS: ./install.sh，Windows: install.bat），它会把 FFmpeg 下载到 runtime/ffmpeg")


def _run(cmd: list[str], timeout: float = 900) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-6:]
        raise MediaError("ffmpeg 失败：" + " | ".join(tail))
    return proc


def probe(path: Path) -> dict:
    """Equivalent of `hypit media probe`: container, streams, duration, fps, size."""
    require_ffmpeg()
    proc = _run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], 60)
    raw = json.loads(proc.stdout or "{}")
    streams = raw.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    fmt = raw.get("format", {})
    fps = None
    if video and video.get("avg_frame_rate") not in (None, "0/0"):
        num, _, den = video["avg_frame_rate"].partition("/")
        try:
            fps = round(float(num) / float(den or 1), 3)
        except (ValueError, ZeroDivisionError):
            fps = None
    duration = float(fmt.get("duration") or (video or {}).get("duration") or 0)
    info = {
        "duration": round(duration, 3),
        "size_bytes": int(fmt.get("size") or path.stat().st_size),
        "format": fmt.get("format_name"),
        "has_video": video is not None,
        "audio_stream_count": len(audio),
    }
    if video:
        # Container duration may include a longer audio tail with no video frame.
        try:
            video_duration = float(video.get("duration"))
        except (TypeError, ValueError):
            video_duration = None
        if video_duration is not None and math.isfinite(video_duration) and video_duration > 0:
            info["video_duration"] = video_duration
        w, h = int(video.get("width") or 0), int(video.get("height") or 0)
        rotate = str((video.get("tags") or {}).get("rotate", ""))
        for side in video.get("side_data_list", []) or []:
            if "rotation" in side:
                rotate = str(side["rotation"])
        if rotate.strip("-") in {"90", "270"}:
            w, h = h, w
        info.update(width=w, height=h, fps=fps, video_codec=video.get("codec_name"))
        info["ratio_hint"] = _ratio_hint(w, h)
    if audio:
        info.update(audio_codec=audio[0].get("codec_name"), sample_rate=audio[0].get("sample_rate"),
                    channels=audio[0].get("channels"))
    return info


def _ratio_hint(w: int, h: int) -> str:
    if not w or not h:
        return "9:16"
    r = w / h
    return min({"9:16": 9 / 16, "16:9": 16 / 9, "1:1": 1.0}.items(), key=lambda kv: abs(kv[1] - r))[0]


def detect_boundaries(path: Path, threshold: float, duration: float, min_shot: float = 0.5) -> list[dict]:
    """Equivalent of `hypit media boundaries`: ffmpeg scene-change detection."""
    require_ffmpeg()
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-an", "-vf",
         f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    if proc.returncode != 0:
        raise MediaError("镜头切分失败：" + (proc.stderr or "")[-300:])
    cuts = sorted({round(float(m), 3) for m in re.findall(r"pts_time:([0-9.]+)", proc.stderr)})
    points = [0.0]
    for t in cuts:
        if t - points[-1] >= min_shot and duration - t >= min_shot:
            points.append(t)
    points.append(round(duration, 3))
    return [{"index": i + 1, "start": points[i], "end": points[i + 1]} for i in range(len(points) - 1)]


def extract_frame(path: Path, t: float, out: Path, width: int = 540) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    seek = ["-ss", f"{t:.3f}"] if t > 0 else []
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *seek, "-i", str(path),
          "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", str(out)], 120)
    if not out.exists():
        raise MediaError(f"取帧失败 @ {t:.2f}s")
    return out


def extract_audio_wav(path: Path, out: Path) -> Path:
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-vn", "-ac", "1",
          "-ar", "16000", str(out)], 900)
    return out


def _delogo_filter(bbox: list[float], width: int, height: int) -> str:
    x0, y0, x1, y1 = bbox
    # Keep the same one-pixel interpolation border for both clips and stills.
    x, y = max(1, min(width - 4, math.floor(x0 * width))), max(1, min(height - 4, math.floor(y0 * height)))
    w = max(2, min(width - 1, math.ceil(x1 * width)) - x)
    h = max(2, min(height - 1, math.ceil(y1 * height)) - y)
    return f"delogo=x={x}:y={y}:w={w}:h={h}"


def delogo_frame(path: Path, masks: list[dict], out: Path) -> Path:
    """Interpolate subtitle bands locally; no opaque blocks or model calls."""
    from .replacement_guard import clean_bbox
    info = probe(path)
    filters = []
    for mask in masks:
        bbox = clean_bbox(mask.get('bbox'))
        if bbox is None:
            raise MediaError('字幕遮挡区域无效，请重新拆解')
        filters.append(_delogo_filter(bbox, info['width'], info['height']))
    if not filters:
        return path
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(path),
          '-frames:v', '1', '-vf', ','.join(filters), str(out)], 120)
    return out


def cut_clip(path: Path, start: float, duration: float, out: Path, *, mute: bool,
             masks: list[dict] | None = None) -> Path:
    """Cut a reference clip for Mini-video. Muting removes the audio stream entirely."""
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-i", str(path),
           "-t", f"{duration:.3f}", "-map", "0:v:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    if masks:
        from .replacement_guard import clean_bbox
        info = probe(path)
        width, height = info["width"], info["height"]
        filters = []
        for mask in masks:
            bbox = clean_bbox(mask.get("bbox"))
            a, b = max(0, float(mask["start"])), min(duration, float(mask["end"]))
            if bbox is None or not math.isfinite(a) or not math.isfinite(b) or b <= a:
                raise MediaError("字幕遮挡区域或时间无效，请重新拆解")
            filters.append(_delogo_filter(bbox, width, height) + f":enable='between(t,{a:.3f},{b:.3f})'")
        cmd += ["-vf", ",".join(filters)]
    if mute:
        cmd += ["-an"]
    else:
        cmd += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "160k"]
    _run(cmd + [str(out)], 900)
    return out


def validate_reference_clip(path: Path, target_seconds: int) -> dict:
    """Fully decode a director reference and compare its MP4 index with reality."""
    require_ffmpeg()
    result = _run(['ffprobe', '-v', 'error', '-count_frames', '-show_format', '-show_streams',
                   '-of', 'json', str(path)], 900)
    raw = json.loads(result.stdout or '{}')
    streams = raw.get('streams') or []
    video = next((s for s in streams if s.get('codec_type') == 'video'), {})
    try:
        duration = float((raw.get('format') or {}).get('duration') or video.get('duration') or 0)
    except (ValueError, TypeError):
        duration = 0
    if not math.isfinite(duration) or abs(duration - target_seconds) > .05:
        raise MediaError(f'参考视频时长校验失败，需要 {target_seconds} 秒，实际 {duration} 秒')
    if any(s.get('codec_type') == 'audio' for s in streams):
        raise MediaError('参考视频必须无声，请重新生成预览')
    try:
        indexed, decoded = int(video['nb_frames']), int(video['nb_read_frames'])
    except (KeyError, TypeError, ValueError):
        raise MediaError('参考视频缺少索引或解码帧数，请重新生成预览') from None
    if indexed <= 0 or indexed != decoded or result.stderr.strip():
        raise MediaError(f'参考视频帧数校验失败：索引 {indexed} 帧，可解码 {decoded} 帧')
    from fractions import Fraction
    return {'duration': duration, 'fps': float(Fraction(video['avg_frame_rate'])), 'audio_stream_count': 0,
            'indexed_frames': indexed, 'decoded_frames': decoded}


def stretch_clip(path: Path, source_seconds: float, target_seconds: int, out: Path) -> dict:
    """Stretch an already cut/masked source-clock clip, keeping its exact frame rate."""
    from fractions import Fraction
    require_ffmpeg()
    if not math.isfinite(source_seconds) or source_seconds <= 0 or not 4 <= target_seconds <= 15:
        raise MediaError('参考视频拉伸时间无效')
    raw = json.loads(_run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                           'stream=avg_frame_rate', '-of', 'json', str(path)], 60).stdout)
    try:
        fps = Fraction(raw['streams'][0]['avg_frame_rate'])
        if fps <= 0:
            raise ValueError
    except (KeyError, IndexError, ValueError, ZeroDivisionError):
        raise MediaError('无法读取原片帧率，请重新生成预览') from None
    stretch = target_seconds / source_seconds
    # Two terminal source frames allow the CFR sampler to fill the final frame
    # interval even for a one-frame source; trim only the padded reference tail.
    filters = (f'tpad=stop_mode=clone:stop=2,setpts={stretch:.12f}*(PTS-STARTPTS),'
               f'fps={fps.numerator}/{fps.denominator},trim=duration={target_seconds}')
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(path), '-map', '0:v:0',
          '-vf', filters, '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18',
          '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(out)], 900)
    return validate_reference_clip(out, target_seconds)


# Calibrated against the round-8 incident (eight normalized RGB samples).
# This is an advisory near-copy detector, not identity/fidelity acceptance.
COPY_SIMILARITY_THRESHOLD = 0.94
COPY_SAMPLE_COUNT = 8


def small_frame(path: Path, at: float, width: int = 64, height: int = 64) -> bytes:
    # Seeking to zero can discard the only packet in FFmpeg's JPEG image2 demuxer.
    seek = ["-ss", f"{at:.6f}"] if at > 0 else []
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", *seek,
                           "-i", str(path), "-frames:v", "1", "-vf", f"scale={width}:{height}",
                           "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], capture_output=True, timeout=120)
    if proc.returncode or len(proc.stdout) != width * height * 3:
        raise MediaError(f"相似度检查取帧失败：{at:.3f}s")
    return proc.stdout


# Round-9 real-source calibration at 360px width: clean excess <= 0.0006,
# old-mask residues >= 0.0131. Advisory only; scene texture can resemble text.
CAPTION_RESIDUE_THRESHOLD = .004


def caption_residue(clip: Path, source: Path, masks: list[dict], overlays: list[dict], outdir: Path,
                    *, source_start: float = 0, sample_times: list[float] | None = None,
                    image_at: float | None = None) -> dict:
    """Compare bright narrow strokes with subtitle-free source frames in the same region.

    Times on masks/samples belong to the processed clip; baseline times belong to
    the whole source, so a caption covering the entire cut can still be checked.
    Optional extra sample times are useful for examining known suspicious frames.
    """
    from .replacement_guard import clean_bbox

    result = {"status": "skipped", "reason": "", "suspected": [], "samples": [], "baseline_times": [],
              "threshold": CAPTION_RESIDUE_THRESHOLD, "scan_padding_y": .06,
              "method": "360px RGB→gray; bright (>160) horizontal strokes (>50 above both ±2px neighbors); "
                        "density minus median of 3 subtitle-free source frames"}
    captions = [o for o in overlays if o.get("kind") in {"caption", "typography", "sticker"}]
    if not captions:
        result["reason"] = "原片没有字幕（拆解 overlay 为空或无文字图层），跳过残留检查"
        return result
    if not masks:
        result["reason"] = "本段没有启用字幕遮挡区间，跳过残留检查"
        return result

    # Never silently use a frame with known captions as the negative control.
    info, source_info = probe(clip), probe(source)
    occupied = []
    try:
        for o in captions:
            a, b = float(o["start"]), float(o["end"])
            if not math.isfinite(a) or not math.isfinite(b) or b <= a:
                raise ValueError("无效字幕时间")
            occupied.append((max(0, a - .15), min(source_info["duration"], b + .15)))
    except (KeyError, TypeError, ValueError):
        result.update(status="unavailable", reason="字幕时间记录不完整，无法确定无字幕基准，需人工检查")
        return result
    gaps, cursor = [], 0.0
    for a, b in sorted(occupied):
        if a - cursor >= .2:
            gaps.append((cursor, a))
        cursor = max(cursor, b)
    if source_info["duration"] - cursor >= .2:
        gaps.append((cursor, source_info["duration"]))
    if not gaps and image_at is None:
        result.update(status="unavailable", reason="原片没有足够长的无字幕基准区间，无法判断残留，需人工检查")
        return result
    baselines = []
    if gaps:
        a, b = max(gaps, key=lambda gap: gap[1] - gap[0])
        baselines = [round(a + (b - a) * f, 6) for f in (.25, .5, .75)]
    else:
        # A still may come from a source with captions throughout. Keep the round-9
        # stroke metric/threshold, conservatively without background subtraction.
        result['baseline_note'] = '原片没有无字幕基准；静态帧按同一亮细笔画密度直接检查，仅作提示'
        result['method'] = result['method'].split('density minus')[0] + 'density (no subtitle-free baseline)'
    result["baseline_times"] = baselines

    width = 360
    height = max(8, round(width * info["height"] / info["width"] / 2) * 2)
    cache = {}

    def gray(path, at):
        key = (str(path), at)
        if key not in cache:
            pixels = small_frame(path, at, width, height)
            cache[key] = [r * .299 + g * .587 + b * .114
                          for r, g, b in zip(pixels[0::3], pixels[1::3], pixels[2::3])]
        return cache[key]

    def density(frame, bbox):
        x0, y0, x1, y1 = bbox
        left, right = int(x0 * width), min(width, max(int(x0 * width) + 5, int(x1 * width)))
        top, bottom = int(y0 * height), min(height, max(int(y0 * height) + 1, int(y1 * height)))
        if right - left < 5 or bottom <= top:
            return 0.0
        strokes = sum(frame[i] > 160 and min(frame[i] - frame[i-2], frame[i] - frame[i+2]) > 50
                      for y in range(top, bottom) for i in range(y * width + left + 2, y * width + right - 2))
        return strokes / ((bottom - top) * (right - left - 4))

    for index, mask in enumerate(masks):
        bbox = clean_bbox(mask.get("bbox"))
        a, b = max(0, float(mask["start"])), min(info["duration"], float(mask["end"]))
        if image_at is not None:
            a, b = 0, 1  # A decoded image has no container duration; inspect once at t=0.
        if bbox is None or not math.isfinite(a) or not math.isfinite(b) or b <= a:
            raise MediaError("字幕残留检查区域或时间无效")
        x0, y0, x1, y1 = bbox
        scan = [x0, max(0, y0 - .06), x1, min(1, y1 + .06)]
        baseline = statistics.median([density(gray(source, at), scan) for at in baselines]) if baselines else 0.0
        times = {round(a + (b - a) * f, 6) for f in (.2, .5, .8)}
        times.update(at for at in sample_times or [] if a < at < b)
        if image_at is not None:
            times = {0.0}
        for at in sorted(times):
            value = density(gray(clip, at), scan)
            score = max(0, value - baseline)
            suspected = score > CAPTION_RESIDUE_THRESHOLD
            sample = {"time": at, "source_time": image_at if image_at is not None else round(source_start + at, 6),
                      "score": round(score, 8), "density": round(value, 8), "baseline": round(baseline, 8),
                      "suspected": suspected, "system": mask.get("system") or mask.get("kind"), "scan_bbox": scan}
            if suspected:
                thumb = extract_frame(clip, at, outdir / f"mask-{index:02d}-{at:.6f}.jpg", width=360)
                sample["thumbnail"] = str(thumb)
                result["suspected"].append(sample)
            result["samples"].append(sample)
    result.update(status="checked", reason="")
    return result


def copy_similarity(result: Path, source: Path, start: float, end: float) -> dict:
    output_duration = probe(result)["duration"]
    source_duration = probe(source)["duration"]
    start, end = max(0, start), min(end, source_duration)
    if end <= start or output_duration <= 0:
        raise MediaError("相似度检查的原片区间或成片时长无效")
    scores = []
    for i in range(COPY_SAMPLE_COUNT):
        relative = (i + .5) / COPY_SAMPLE_COUNT
        a = small_frame(source, start + (end - start) * relative)
        b = small_frame(result, output_duration * relative)
        scores.append(1 - sum(abs(x - y) for x, y in zip(a, b)) / len(a) / 255)
    similarity = sum(scores) / len(scores)
    return {"copy_suspected": similarity >= COPY_SIMILARITY_THRESHOLD,
            "similarity": round(similarity, 6), "threshold": COPY_SIMILARITY_THRESHOLD,
            "method": "8-frame RGB mean absolute difference / 255; similarity = 1 - difference",
            "frame_similarities": [round(s, 6) for s in scores], "source_range": [start, end]}


def extract_audio_sample(path: Path, start: float, end: float, out: Path) -> Path:
    """Exact natural sample, no pitch/tempo change (mirrors chain5_voice_local extract-reference)."""
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
          "-i", str(path), "-vn", "-c:a", "pcm_s16le", str(out)], 300)
    return out


def concat(videos: list[Path], out: Path, width: int, height: int, fps: int = 24, *,
           target_seconds: list[float] | None = None) -> Path:
    """Normalize, optionally trim video/audio together, then join; add silence if needed."""
    if not videos:
        raise MediaError("没有可拼接的片段")
    if target_seconds is not None and (len(target_seconds) != len(videos) or any(
            not math.isfinite(t) or t <= 0 for t in target_seconds)):
        raise MediaError('每段成片时长必须是大于 0 的秒数，并与视频一一对应')
    work = out.parent / "_norm"
    work.mkdir(parents=True, exist_ok=True)
    normalized = []
    for i, v in enumerate(videos):
        info = probe(v)
        n = work / f"{i:02d}.mp4"
        vf = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
              f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(v)]
        if info.get("audio_stream_count", 0) == 0:
            cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-shortest", "-map", "0:v:0", "-map", "1:a:0"]
        else:
            cmd += ["-map", "0:v:0", "-map", "0:a:0"]
        if target_seconds is not None:
            # Output-side -t limits video and audio together, without stretching
            # short generations or modifying the original downloaded video.
            cmd += ['-t', str(min(target_seconds[i], info['duration']))]
        cmd += ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac",
                "-ar", "44100", "-ac", "2", str(n)]
        _run(cmd, 900)
        normalized.append(n)
    listing = work / "list.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in normalized), encoding="utf-8")
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
          "-c", "copy", "-movflags", "+faststart", str(out)], 900)
    shutil.rmtree(work, ignore_errors=True)
    return out


def make_proxy(path: Path, out: Path, long_side: int = 640) -> Path:
    """Small low-fps copy for whole-video understanding (Gemini samples ~1 fps anyway)."""
    vf = f"scale='if(gt(iw,ih),{long_side},-2)':'if(gt(iw,ih),-2,{long_side})',fps=2"
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-vf", vf, "-c:v", "libx264",
          "-preset", "veryfast", "-crf", "32", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "1", "-b:a", "32k",
          "-movflags", "+faststart", str(out)], 1800)
    return out


# ---------------------------------------------------------------------------
# Hypit-equivalent evidence tools (ports of `hypit media boundaries / frames / tile`)

HYPIT_BOUNDARY_RATE = 12
HYPIT_BOUNDARY_THRESHOLD = 0.1


def hypit_boundaries(path: Path, rate: int = HYPIT_BOUNDARY_RATE,
                     threshold: float = HYPIT_BOUNDARY_THRESHOLD) -> list[dict]:
    """Same algorithm as @hypit/media-local visualBoundaries: sample at `rate` fps, downscale to
    32x32 RGB, mean absolute adjacent-frame difference / 255 >= threshold -> candidate.
    Scores are mechanical evidence, not editorial shots."""
    require_ffmpeg()
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-vf",
                           f"fps={rate},scale=32:32,format=rgb24", "-f", "rawvideo", "-"],
                          capture_output=True, timeout=1800)
    if proc.returncode != 0:
        raise MediaError("切点检测失败：" + proc.stderr.decode("utf-8", "replace")[-300:])
    data = proc.stdout
    stride = 32 * 32 * 3
    n = len(data) // stride
    try:  # numpy is present whenever faster-whisper is installed
        import numpy as np
        arr = np.frombuffer(data[: n * stride], dtype=np.uint8).reshape(n, stride).astype(np.int16)
        scores = (np.abs(np.diff(arr, axis=0)).sum(axis=1) / stride / 255).tolist() if n > 1 else []
    except ImportError:
        frames = [data[i * stride:(i + 1) * stride] for i in range(n)]
        scores = [sum(abs(x - y) for x, y in zip(frames[i - 1], frames[i])) / stride / 255 for i in range(1, n)]
    return [{"at": round((i + 1) / rate, 3), "score": round(sc, 4)} for i, sc in enumerate(scores) if sc >= threshold]


def merge_boundaries(cands: list[dict], duration: float, min_gap: float = 0.35) -> list[dict]:
    """Collapse runs of adjacent candidates (a dissolve or fast motion) into one cut at the peak."""
    groups: list[list[dict]] = []
    for c in cands:
        if groups and c["at"] - groups[-1][-1]["at"] <= min_gap:
            groups[-1].append(c)
        else:
            groups.append([c])
    cuts = [max(g, key=lambda x: x["score"]) for g in groups]
    points = [0.0] + [c["at"] for c in cuts if 0.2 < c["at"] < duration - 0.2] + [round(duration, 3)]
    return [{"index": i + 1, "start": points[i], "end": points[i + 1],
             "cut_score": cuts[i - 1]["score"] if 0 < i <= len(cuts) else None}
            for i in range(len(points) - 1)]


def frames_range(path: Path, start: float, end: float, every: float, outdir: Path, width: int = 320,
                 prefix: str = "f") -> list[dict]:
    """`hypit media frames --start --end --every`: one continuous decode, sampled every `every` s."""
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob(f"{prefix}-*.jpg"):
        old.unlink()
    dur = max(0.05, end - start)
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-i", str(path),
          "-t", f"{dur:.3f}", "-vf", f"fps=1/{every:.4f}:round=down,scale={width}:-2", "-q:v", "4",
          str(outdir / f"{prefix}-%04d.jpg")], 900)
    files = sorted(outdir.glob(f"{prefix}-*.jpg"))
    return [{"t": round(start + i * every, 3), "file": f} for i, f in enumerate(files)]


def tile(frames: list[Path], out: Path, columns: int) -> Path:
    """`hypit media tile`: arrange frames in reading order (time labels are supplied as text)."""
    import tempfile
    rows = max(1, -(-len(frames) // columns))
    with tempfile.TemporaryDirectory() as tmp:
        for i, f in enumerate(frames):
            shutil.copyfile(f, Path(tmp) / f"{i:04d}.jpg")  # copies, not symlinks: works on Windows
        _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-framerate", "1", "-i",
              str(Path(tmp) / "%04d.jpg"), "-vf", f"tile={columns}x{rows}:padding=4:color=white",
              "-frames:v", "1", "-q:v", "3", str(out)], 300)
    return out


# Advisory timing tolerances; 10 fps gives 0.1 s motion resolution. Correlation is
# deliberately conservative: camera/lighting/caption changes also create energy.
MOTION_LAG_THRESHOLD = .5
MOTION_MIN_CORRELATION = .45
MOTION_BOUNDARY_WARNING = '画面变化曲线在 ±2 秒内没有找到可靠的对应位置（原片和成片的字幕、小窗等画面元素不同时常见），这一项不作判断'


def _motion_energy(path: Path, start: float, duration: float):
    """Decode once, retaining only a tiny 10 fps grayscale stream in memory."""
    import numpy as np
    proc = subprocess.run([
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', f'{start:.6f}', '-i', str(path),
        '-t', f'{duration:.6f}', '-an', '-vf', 'fps=10,scale=64:64', '-pix_fmt', 'gray',
        '-f', 'rawvideo', '-'], capture_output=True, timeout=180)
    if proc.returncode or not proc.stdout or len(proc.stdout) % 4096:
        raise MediaError('动作检查灰度帧解码失败')
    frames = np.frombuffer(proc.stdout, dtype=np.uint8).reshape(-1, 4096).astype(np.float32)
    if len(frames) < 11:
        raise MediaError('动作检查至少需要 1 秒有效画面')
    return np.abs(np.diff(frames, axis=0)).mean(axis=1)


def _motion_correlation(source_energy, generated_energy):
    import numpy as np
    source, generated = np.asarray(source_energy), np.asarray(generated_energy)
    if min(len(source), len(generated)) < 10 or min(source.std(), generated.std()) < 1e-6:
        raise MediaError('运动能量不足，不能可靠估计偏移')
    source = (source - source.mean()) / source.std()
    generated = (generated - generated.mean()) / generated.std()
    scores = []
    for lag in range(-20, 21):
        si, gi = max(0, -lag), max(0, lag)
        n = min(len(source) - si, len(generated) - gi)
        if n < max(10, min(len(source), len(generated)) // 2):
            continue
        a, b = source[si:si + n], generated[gi:gi + n]
        if min(a.std(), b.std()) < 1e-6:
            continue
        scores.append((float(np.corrcoef(a, b)[0, 1]), lag))
    if not scores:
        raise MediaError('有效运动重叠区间不足')
    coefficient, lag = max(scores, key=lambda row: (row[0], -abs(row[1])))
    return round(lag / 10, 3), round(coefficient, 4)


def _speech_lags(source: dict, generated: dict, start: float, end: float, *, dialogue_lines: list[dict] | None = None):
    """Match ordered sentence anchors, independent of ASR sentence splitting.

    Word timestamps come only from real ASR/alignment. When words are absent,
    compare whole segment starts; never fabricate evenly spaced word times.
    """
    from difflib import SequenceMatcher

    def tokens(text):
        # ASR may emit a Han sentence as characters, words, or one whole span.
        # Normalize all of them to character tokens while retaining Latin words.
        # Every character still uses its actual containing ASR word timestamp;
        # splitting text never invents finer-grained times.
        text = re.sub(r"([\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
                      r"\U00020000-\U0003134f\u3040-\u30ff\uac00-\ud7af])", r" \1 ", str(text).casefold())
        return re.findall(r"\w+", text)

    anchors = []
    for segment in generated.get('segments', []):
        words = segment.get('words') or []
        if words:
            for word in words:
                for token in tokens(word.get('word', '')):
                    if isinstance(word.get('start'), (int, float)) and math.isfinite(word['start']):
                        anchors.append((token, float(word['start']), True))
        else:
            for i, token in enumerate(tokens(segment.get('text', ''))):
                anchors.append((token, float(segment['start']), i == 0))
    rows, warnings, cursor = [], [], 0
    # Authored lines keep absolute source times but may intentionally change text.
    for segment in dialogue_lines or source.get('segments', []):
        a, b = float(segment['start']), float(segment['end'])
        if not (a < end and b > start):
            continue
        text = segment.get('text', '')
        expected = tokens(text)
        if a < start:
            words = [w for w in segment.get('words', []) if float(w.get('start', -1)) >= start
                     and float(w.get('start', end)) < end]
            if not words:
                warnings.append(f'跨区间台词缺少可用词时间，无法核对：{text}')
                continue
            a = float(words[0]['start'])
            expected = tokens(' '.join(w.get('word', '') for w in words))
        prefix = expected[:5]
        candidates = []
        for index in range(cursor, len(anchors)):
            if not anchors[index][2]:
                continue
            actual = [w[0] for w in anchors[index:index + len(prefix)]]
            score = SequenceMatcher(None, prefix, actual).ratio()
            if prefix and actual and prefix[0] == actual[0] and score >= .75:
                candidates.append((score, -index, index))
        if not candidates:
            warnings.append(f'成片未可靠匹配台词：{text}')
            continue
        score, _, index = max(candidates)
        generated_start = anchors[index][1]
        cursor = index + max(1, len(prefix))
        rows.append({'text': text, 'source_start': round(a, 3), 'expected_start': round(a - start, 3),
                     'generated_start': round(generated_start, 3),
                     'lag_seconds': round(generated_start - (a - start), 3), 'match_score': round(score, 3)})
    return rows, warnings


def motion_alignment(result: Path, source: Path, start: float, end: float,
                     source_transcript: dict | Path, outdir: Path, *, dialogue_lines: list[dict] | None = None) -> dict:
    """Advisory local timing check. Negative lag means generated video is early.

    No remote calls/downloads; failures remain explicit inconclusive warnings.
    The caller runs this slow computation as a background job, not inside GET.
    """
    from . import asr
    report = {'status': 'inconclusive', 'motion_lag_seconds': None, 'motion_correlation': None,
              'speech_lags': [], 'warnings': [], 'source_start': start, 'source_end': end,
              'thresholds': {'max_lag_seconds': MOTION_LAG_THRESHOLD, 'min_correlation': MOTION_MIN_CORRELATION},
              'method': '10fps 64x64 gray adjacent MAD; normalized correlation ±2s; ordered ASR sentence anchors',
              'lag_sign': 'negative = generated early', 'sample_fps': 10,
              'motion_search_seconds': 2, 'motion_status': 'inconclusive',
              'speech_reference': 'dialogue_lines' if dialogue_lines else 'source_transcript',
              'speech_reference_lines': dialogue_lines or []}
    invalid, misaligned = False, False
    if not all(math.isfinite(v) for v in (start, end)) or start < 0 or end <= start:
        report['warnings'].append('原片时间区间无效，未执行对齐检查')
        return report
    try:
        energies = [_motion_energy(source, start, end - start), _motion_energy(result, 0, end - start)]
        lag, correlation = _motion_correlation(*energies)
        report.update(motion_lag_seconds=lag, motion_correlation=correlation)
        report['motion_energy'] = {'source': [round(float(v), 4) for v in energies[0]],
                                   'generated': [round(float(v), 4) for v in energies[1]]}
        if abs(lag) >= report['motion_search_seconds'] - 1 / report['sample_fps']:
            invalid = True
            report['warnings'].append(MOTION_BOUNDARY_WARNING)
        else:
            report['motion_status'] = 'aligned'
            if abs(lag) > MOTION_LAG_THRESHOLD:
                misaligned = True
                report['motion_status'] = 'misaligned'
                report['warnings'].append(f'成片运动节奏比原片{"提前" if lag < 0 else "延后"}约 {abs(lag):.2f} 秒')
            if correlation < MOTION_MIN_CORRELATION:
                misaligned = True
                report['motion_status'] = 'misaligned'
                report['warnings'].append(f'动作节奏相关系数偏低（{correlation:.3f} < {MOTION_MIN_CORRELATION}），偏移估计可信度较低')
    except Exception as exc:
        invalid = True
        report['warnings'].append(f'动作检查未完成：{type(exc).__name__}: {exc}')
    try:
        transcript = (json.loads(Path(source_transcript).read_text(encoding='utf-8'))
                      if not isinstance(source_transcript, dict) else source_transcript)
        report['source_timing'] = transcript.get('timing', 'unknown')
        outdir.mkdir(parents=True, exist_ok=True)
        audio = outdir / 'generated-audio.wav'
        extract_audio_wav(result, audio)
        generated = asr.transcribe_best(audio, 'medium', 'int8', transcript.get('language'), local_only=True)
        (outdir / 'generated-transcript.json').write_text(json.dumps(generated, ensure_ascii=False, indent=2), encoding='utf-8')
        report['generated_timing'] = generated.get('timing', 'unknown')
        if generated.get('fallback_reason'):
            report['warnings'].append(generated['fallback_reason'])
        rows, missing = _speech_lags(transcript, generated, start, end, dialogue_lines=dialogue_lines)
        report['speech_lags'] = rows
        report['warnings'].extend(missing)
        if missing:
            invalid = True
        if not (dialogue_lines or transcript.get('segments')):
            invalid = True
            report['warnings'].append('原片没有可信台词时间，语音对齐未判定')
        for row in rows:
            lag = row['lag_seconds']
            if abs(lag) > MOTION_LAG_THRESHOLD:
                misaligned = True
                report['warnings'].append(f'“{row["text"]}” {"提前" if lag < 0 else "延后"} {abs(lag):.2f} 秒')
    except Exception as exc:
        invalid = True
        report['warnings'].append(f'台词检查未完成（仅使用本地模型）：{type(exc).__name__}: {exc}')
    report['status'] = 'misaligned' if misaligned else ('inconclusive' if invalid else 'aligned')
    return report
