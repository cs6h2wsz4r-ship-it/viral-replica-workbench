"""Checked sentence boundaries over immutable source words; run only on explicit work."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import threading
import unicodedata
from pathlib import Path

from . import evidence, gateway, llm, store

SPEECH_SYSTEM = '''你是逐词转写的台词断句员。按语义完整性与自然换气重新划分自然句，优先参考真实停顿 gap，
不要沿用转写块边界，也不要仅为凑时长切断句子。输入 words 带原词、从零开始的 index、start/end 与前词间隔 gap，
dialogue 提供原句及说话人。只能改变句界、标点和大小写；不得增加、删除、替换、合并或调整任何词的顺序。
保持原语言。每句用首尾词的闭区间序号 first/last；从第 0 词到最后一词按序连续完整覆盖，不得漏词或重叠。
只输出 JSON：{"lines":[{"first":0,"last":3,"text":"原词加自然标点组成的句子"}]}。不要输出时间、解释或额外字段。'''
WARNING = '台词自动断句没成功，暂用转写的原句。'
_TERMINAL = {'done', 'fallback', 'skipped'}
_HYPHENS = r'[-‐‑‒–—﹘﹣－]'
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def normalize_text(text: str) -> list[str]:
    """Compare words, tolerating apostrophe and hyphen style/absence.

    Word boundaries remain significant: ``people are`` cannot become ``peopleare``.
    Han characters are atomic so ASR token spacing is immaterial for Chinese.
    """
    text = unicodedata.normalize('NFKC', str(text or '')).casefold()
    text = re.sub("['’‘ʼ＇]", '', text)
    text = re.sub(_HYPHENS, '', text)
    return re.findall(r'[\u3400-\u9fff]|[^\W_\u3400-\u9fff]+', text)


def _matching_ends(actual: list[str], word_rows: list[dict], positions: set[int]) -> set[int]:
    """Share spelling tolerance between exact validation and sentence search."""
    for word in word_rows:
        original = word['word']
        variants = [normalize_text(original)]
        # Only a hyphen in an original word authorizes a spaced spelling. This
        # cannot merge two ordinary source words into one candidate token.
        if re.search(_HYPHENS, original):
            variants.append(normalize_text(re.sub(_HYPHENS, ' ', original)))
        positions = {pos + len(option) for pos in positions for option in variants
                     if actual[pos:pos + len(option)] == option}
        if not positions:
            break
    return positions


def _same_words(text: str, word_rows: list[dict]) -> bool:
    actual = normalize_text(text)
    return len(actual) in _matching_ends(actual, word_rows, {0})


def contains_words(text: str, word_rows: list[dict]) -> bool:
    """Find all the words contiguously; never skip intervening picture prose."""
    actual = normalize_text(text)
    return bool(word_rows and _matching_ends(actual, word_rows, set(range(len(actual)))))


def read_transcript(directory: Path) -> dict:
    path = Path(directory) / 'analysis/transcript.raw.json'
    if not path.is_file():
        return {}
    raw = b''
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError('invalid transcript object')
        return value
    except (OSError, ValueError):
        return {'segments': [], '_speech_read_error': '逐词转写文件无法读取或格式无效',
                '_speech_source_digest': hashlib.sha256(raw).hexdigest()}


def _word_rows(transcript: dict) -> list[dict]:
    out = []
    if not isinstance(transcript, dict) or not isinstance(transcript.get('segments', []), list):
        raise ValueError('逐词转写格式无效')
    for segment in transcript.get('segments') or []:
        if not isinstance(segment, dict):
            raise ValueError('逐词转写行格式无效')
        raw = segment.get('words') or []
        if not isinstance(raw, list):
            raise ValueError('逐词转写列表格式无效')
        # Sentence-only SRT must never invent word timings or omit its speech.
        if segment.get('text') and not raw:
            return []
        for word in raw:
            try:
                text = word['word']
                start, end = float(word['start']), float(word['end'])
            except (KeyError, TypeError, ValueError):
                raise ValueError('逐词转写缺少原词或有效时间') from None
            if (not isinstance(text, str) or not text.strip() or not math.isfinite(start)
                    or not math.isfinite(end) or start < 0 or end < start
                    or (out and start < out[-1]['start'])):
                raise ValueError('逐词转写的原词或时间顺序无效')
            out.append({'index': len(out), 'word': text, 'start': start, 'end': end,
                        'gap': round(start - out[-1]['end'], 6) if out else 0.0})
    return out


def words(data: dict, directory: Path | None = None) -> list[dict]:
    """Read source words without writing or requesting a model."""
    if directory is not None:
        transcript = read_transcript(Path(directory))
    elif 'segments' in data:
        transcript = data
    else:
        embedded = (data.get('analysis') or {}).get('transcript') or []
        transcript = embedded if isinstance(embedded, dict) else {'segments': embedded}
    return _word_rows(transcript)


def _speaker(start: float, end: float, dialogue: list[dict]) -> str:
    overlaps: dict[str, float] = {}
    for row in dialogue:
        try:
            a, b = float(row['start']), float(row['end'])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(a) or not math.isfinite(b):
            continue
        overlap = max(0.0, min(end, b) - max(start, a))
        speaker = row.get('speaker') or 'unknown'
        if overlap > 0:
            overlaps[speaker] = overlaps.get(speaker, 0.0) + overlap
    maximum = max(overlaps.values(), default=0)
    winners = [speaker for speaker, overlap in overlaps.items() if abs(overlap - maximum) < 1e-6]
    return winners[0] if len(winners) == 1 else 'unknown'


def validate(value: dict, word_rows: list[dict], dialogue: list[dict]) -> list[dict]:
    """Return source-clock sentences only when every original word is preserved."""
    entries = value.get('lines') if isinstance(value, dict) else None
    if not isinstance(entries, list) or not entries or not word_rows:
        raise ValueError('断句结果缺少完整的 lines 列表')
    result, expected = [], 0
    for row in entries:
        if not isinstance(row, dict):
            raise ValueError('断句行格式无效')
        first, last, text = row.get('first'), row.get('last'), row.get('text')
        if type(first) is not int or type(last) is not int or first != expected or not first <= last < len(word_rows):
            raise ValueError('词序号必须首尾相接、按序完整覆盖，不能遗漏或重叠')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('断句文本不能为空')
        if not _same_words(text, word_rows[first:last + 1]):
            raise ValueError('断句文本与原词不一致，只允许调整标点和大小写')
        start, end = word_rows[first]['start'], word_rows[last]['end']
        result.append({'start': start, 'end': end, 'text': text.strip(), 'words': [first, last],
                       'speaker': _speaker(start, end, dialogue),
                       'pause_after': round(max(0.0, word_rows[last + 1]['start'] - end), 6)
                       if last + 1 < len(word_rows) else 0.0})
        expected = last + 1
    if expected != len(word_rows):
        raise ValueError('断句结果未覆盖全部原词')
    return result


def lines(data: dict) -> list[dict]:
    """Read existing sentences, falling back to original dialogue without mutation."""
    pack = data.get('breakdown') or {}
    return copy.deepcopy(pack.get('speech_lines') or pack.get('dialogue') or [])


def source_fingerprint(data: dict, transcript: dict) -> str:
    """Capture only upstream evidence; logs, usage and downstream plans are irrelevant."""
    value = {'source': data.get('source'), 'analysis_started': (data.get('analysis') or {}).get('started'),
             'dialogue': (data.get('breakdown') or {}).get('dialogue') or [], 'transcript': transcript}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _fallback(dialogue: list[dict], word_rows: list[dict]) -> list[dict]:
    rows = copy.deepcopy(dialogue)
    if not word_rows:
        return rows
    for index, row in enumerate(rows):
        try:
            start, end = float(row['start']), float(row['end'])
        except (KeyError, TypeError, ValueError):
            continue
        owned = [w['index'] for w in word_rows if start <= (w['start'] + w['end']) / 2 < end]
        if owned:
            row['words'] = [owned[0], owned[-1]]
        row.setdefault('speaker', 'unknown')
        row['pause_after'] = round(max(0.0, float(rows[index + 1]['start']) - end), 6) if index + 1 < len(rows) else 0.0
    return rows


def _request_failure(exc: Exception) -> str:
    # Do not persist arbitrary provider exception messages or echoed requests.
    kind = getattr(exc, 'kind', '')
    return {'auth': '模型渠道鉴权未通过', 'quota': '模型渠道额度不足',
            'too_large': '模型请求超出渠道限制', 'transient': '模型渠道暂时不可用',
            'empty': '模型没有返回断句结果'}.get(kind, '模型请求未完成或返回格式无效')


def refine(pid: str, pack: dict, transcript: dict | None, directory: Path | None = None) -> dict:
    """Compute a pack for an explicit analysis. Caller commits it and SRT together."""
    project = store.load(pid)
    result = copy.deepcopy(pack)
    transcript = transcript or {}
    dialogue = result.get('dialogue') or []
    state = {'fingerprint': source_fingerprint({**project, 'breakdown': result}, transcript)}
    reason = transcript.get('_speech_read_error') or ''
    try:
        word_rows = _word_rows(transcript)
    except ValueError as exc:
        word_rows, reason = [], str(exc)
    if not word_rows or not dialogue:
        state.update(status='fallback' if dialogue else 'skipped', reason=reason or ('没有逐词时间' if dialogue else '没有台词'))
        if dialogue:
            state['warning'] = WARNING
        result.update(speech_lines=_fallback(dialogue, word_rows), speech_lines_state=state)
        return result

    messages = [{'role': 'system', 'content': SPEECH_SYSTEM},
                {'role': 'user', 'content': json.dumps({'words': word_rows, 'dialogue': dialogue}, ensure_ascii=False)}]
    # Own both JSON parsing and semantic validation so the total budget is two
    # text requests, without nesting gateway JSON repair/check retry loops.
    for attempt in range(2):
        if attempt:
            messages.append({'role': 'user', 'content': '上次断句未通过校验：' + reason + '。请重新输出完整 JSON，逐词核对覆盖与原文。'})
        try:
            response = gateway.reason('speech_lines', messages, json=False, temperature=0, max_tokens=16000, project=pid)
        except Exception as exc:
            reason = _request_failure(exc)
            continue
        try:
            content = response.get('content') if isinstance(response, dict) else None
            if not isinstance(content, str):
                raise llm.JSONResponseError('模型没有返回文本')
            value = llm.extract_json(content)
        except llm.JSONResponseError:
            reason = '模型没有返回完整有效的 JSON'
            continue
        try:
            refined = validate(value, word_rows, dialogue)
        except ValueError as exc:
            reason = str(exc)
            continue
        result.update(speech_lines=refined, speech_lines_state={**state, 'status': 'done'})
        return result
    result.update(speech_lines=_fallback(dialogue, word_rows),
                  speech_lines_state={**state, 'status': 'fallback', 'reason': reason, 'warning': WARNING})
    return result


def write_srt(pack: dict, directory: Path) -> None:
    """Write artifacts only after the caller has verified the current source."""
    adir = Path(directory) / 'analysis'
    adir.mkdir(parents=True, exist_ok=True)
    (adir / '台词表.转写原句.srt').write_text(evidence.to_srt({'segments': pack.get('dialogue') or []}), encoding='utf-8')
    (adir / '台词表.srt').write_text(evidence.to_srt({'segments': pack.get('speech_lines') or pack.get('dialogue') or []}), encoding='utf-8')


def ensure(pid: str) -> dict:
    """Explicit old-project migration; serialize requests without holding store's lock."""
    with _locks_guard:
        lock = _locks.setdefault(pid, threading.Lock())
    with lock:
        directory = store.project_dir(pid)
        data = store.load(pid)
        if not isinstance(data.get('breakdown'), dict):
            return data
        transcript = read_transcript(directory)
        fingerprint = source_fingerprint(data, transcript)
        state = data['breakdown'].get('speech_lines_state') or {}
        if state.get('status') in _TERMINAL and state.get('fingerprint') == fingerprint:
            return data
        pack = refine(pid, data['breakdown'], transcript, directory)
        with store._lock(pid):
            current = store.load(pid)
            if source_fingerprint(current, read_transcript(directory)) != fingerprint:
                return current
            def commit(latest):
                latest['breakdown']['speech_lines'] = pack['speech_lines']
                latest['breakdown']['speech_lines_state'] = pack['speech_lines_state']
                docs = latest.setdefault('analysis', {}).setdefault('docs', {})
                docs.update({'台词表.srt': 'analysis/台词表.srt', '台词表.转写原句.srt': 'analysis/台词表.转写原句.srt'})
            write_srt(pack, directory)
            saved = store.update(pid, commit)
        if pack['speech_lines_state'].get('warning'):
            store.log(pid, WARNING, 'warn')
            saved = store.load(pid)
        return saved
