"""Local word-end protection for scene projects; no inference during read-only GET."""
from __future__ import annotations

import json
import math
from pathlib import Path

from . import asr, media, segment_timing


def transcript_path(directory: Path, run: dict) -> Path:
    folder = run.get('run_dir')
    if not folder:
        # Deterministic per-version cache, never shared between generated videos.
        folder = str(Path(run['result']).with_suffix('')) + '-speech'
    path = (directory / folder / 'motion-check/generated-transcript.json').resolve()
    if not path.is_relative_to(directory.resolve()):
        raise ValueError('生成视频的转录路径无效')
    return path


def bounds(transcript: dict) -> tuple[float | None, float | None]:
    spans = []
    for row in transcript.get('segments') or []:
        # Prefer word alignment; sentence endpoints are still conservative evidence
        # when older local transcripts have no word timestamps.
        for word in row.get('words') or [row]:
            if not str(word.get('word', word.get('text', ''))).strip():
                continue
            a, b = word.get('start'), word.get('end')
            if all(isinstance(v, (int,float)) and math.isfinite(v) for v in (a,b)) and 0 <= a <= b:
                spans.append((a,b))
    return (min(a for a,b in spans), max(b for a,b in spans)) if spans else (None,None)


def summarize(task: dict, transcript: dict, duration: float | None = None) -> dict:
    target = segment_timing.target_seconds(task)
    start, end = bounds(transcript)
    trim_end = max(target, end + .2) if end is not None else target
    if duration is not None:
        trim_end = min(duration, trim_end)
    source_starts = [float(r['start']) - float(task.get('source_start') or 0)
                     for r in task.get('dialogue_lines') or [] if isinstance(r.get('start'), (int,float))]
    source_starts += [float(r['start']) for r in task.get('dialogue_windows') or []
                      if isinstance(r.get('start'), (int,float))] if not source_starts else []
    return {'trim_end': trim_end, 'last_word_end': end,
            'lag_seconds': round(start - min(source_starts), 3) if start is not None and source_starts else None,
            'extra_seconds': max(0, trim_end - target)}


def for_run(task: dict, run: dict, directory: Path, *, transcribe: bool = False) -> dict | None:
    if not task.get('dialogue') and not task.get('dialogue_lines'):
        return None
    path = transcript_path(directory,run)
    transcript = None
    if path.is_file():
        try:
            transcript = json.loads(path.read_text(encoding='utf-8'))
        except (ValueError,OSError):
            pass
    clip = directory / run['result']
    duration = None
    if transcribe:
        probe = media.probe(clip)
        duration = probe.get('duration')
        if not probe.get('audio_stream_count'):
            return summarize(task,{},duration)
        if transcript is None:
            path.parent.mkdir(parents=True,exist_ok=True)
            audio = path.with_name('trim-audio.wav')
            try:
                media.extract_audio_wav(clip,audio)
                transcript = asr.transcribe(audio, 'medium', 'int8', task.get('language'), local_only=True)
                path.write_text(json.dumps(transcript,ensure_ascii=False,indent=2),encoding='utf-8')
            except Exception as exc:
                # Without local word evidence, retaining the full generated take
                # is the only way to guarantee that speech is not cut in half.
                target = segment_timing.target_seconds(task)
                return {'trim_end': duration or target, 'lag_seconds': None,
                        'extra_seconds': max(0,(duration or target)-target),
                        'warning': f'本地台词转录未完成，已保留整段以免剪断台词：{exc}'}
            finally:
                audio.unlink(missing_ok=True)
    if not isinstance(transcript,dict):
        return None
    if duration is None:
        duration = (run.get('speech_timing') or {}).get('video_seconds')
        if duration is None:
            duration = media.probe(clip).get('duration')
    result = summarize(task,transcript,duration)
    result['video_seconds'] = duration
    if not transcribe:
        # Existing motion checks already know sentence-match lags; prefer those
        # over first-word inference for ASR that contains unrelated opening audio.
        lags = [r.get('lag_seconds') for r in (run.get('motion_check') or {}).get('speech_lags') or []
                if isinstance(r.get('lag_seconds'),(int,float))]
        if lags:
            result['lag_seconds'] = max(lags)
    return result
