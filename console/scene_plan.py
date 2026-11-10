"""Read-only scene casting, reference-frame selection and camera bindings.

The legacy character camera helpers deliberately remain separate: deriving the
new editor view must never migrate a saved project or change an old video job.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path

from . import scene_edits, store


def _guard():
    from . import replacement_guard
    return replacement_guard


def enabled(data: dict) -> bool:
    return (bool(scene_edits.fingerprint_edits(data))
            or 'scene_plan' in (data.get('replacements') or {})
            or any(str(r.get('for_task') or '').startswith('scene-camera:')
                   for r in (data.get('images') or {}).values())
            or any(a.get('scene_camera_binding') or a.get('scene_camera_scene')
                   or str(a.get('for_task') or '').startswith('scene-camera:')
                   for a in data.get('assets', [])))


def _scene(data: dict, sid: str) -> dict:
    scene = next((s for s in _guard().scenes(data) if s.get('id') == sid), None)
    if scene is None:
        raise ValueError('当前项目没有这个场景，请刷新后重试')
    return scene


def _ranges(data: dict, sid: str) -> list[tuple[float, float]]:
    ranges = _guard().scene_ranges(_scene(data, sid))
    if ranges:
        return ranges
    duration = (data.get('source') or {}).get('probe', {}).get('duration')
    if not duration:
        duration = max((_interval(s)[1] for s in (data.get('breakdown') or {}).get('shots', [])), default=1)
    return [(0, max(float(duration or 1), .001))]


def _plan(data: dict, sid: str) -> dict:
    return scene_edits.plan(data, sid)


def _interval(row: dict) -> tuple[float, float]:
    try:
        a, b = float(row.get('start') or 0), float(row.get('end') or 0)
        return (a, b) if math.isfinite(a) and math.isfinite(b) else (0, 0)
    except (TypeError, ValueError):
        return 0, 0


def _overlaps(row: dict, ranges: list[tuple[float, float]]) -> bool:
    a, b = _interval(row)
    return any(b > x and a < y for x, y in ranges)


def _has_product(value) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, (list, tuple)):
        return any(_has_product(v) for v in value)
    if isinstance(value, dict):
        return any(_has_product(v) for v in value.values())
    text = str(value).strip()
    if not text or re.search(r'^(?:无|没有|未见|未出现|未出镜|尚未|暂未|不含|暂无|none\b|null\b|no\s+product\b|not\s+(?:visible|present)\b)', text, re.I):
        return False
    if re.search(r'(?:商品|产品).*(?:尚未|暂未|没有|未见|未出现|未出镜)|(?:尚未|暂未)出现', text):
        return False
    return True


def present_keys(data: dict, sid: str) -> set[str]:
    ranges = _ranges(data, sid)
    pack = data.get('breakdown') or {}
    shots = [s for s in pack.get('shots', []) if _overlaps(s, ranges)]
    keys = {cid for s in shots for cid in s.get('characters') or []
            if not _guard().offscreen_character(data, cid)}
    if any(_overlaps(m, ranges) for m in pack.get('productMoments') or []) or any(_has_product(s.get('product')) for s in shots):
        keys.add('product')
    return keys


def _safe_file(file, directory: Path | None) -> str | None:
    if not isinstance(file, str) or not file or Path(file).is_absolute() or '..' in Path(file).parts:
        return None
    if directory is not None:
        path = (directory / file).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file():
            return None
    return file


def _thumbnail_at(shot: dict) -> float | None:
    at = shot.get('frame_at', sum(_interval(shot)) / 2)
    return float(at) if isinstance(at, (int, float)) and math.isfinite(at) else None


def original_thumbnail(data: dict, key: str, directory: Path | None = None,
                       sid: str | None = None) -> dict:
    """Only a frame's actual capture time can prove that it represents an appearance."""
    pack = data.get('breakdown') or {}
    shots = sorted((s for s in pack.get('shots', [])
                    if key in (s.get('characters') or []) or key == 'product' and _has_product(s.get('product'))),
                   key=lambda s: _interval(s)[0])
    def clip(intervals):
        if sid is not None:
            intervals = [(max(a,x), min(b,y)) for a,b in intervals for x,y in _ranges(data,sid)
                         if b > x and a < y]
        return sorted(set((a,b) for a,b in intervals if b > a))
    intervals = clip([_interval(s) for s in shots])
    if key == 'product':
        # Product moments take priority; descriptions are only a fallback.
        moments = clip([_interval(m) for m in pack.get('productMoments') or []])
        intervals = moments + [pair for pair in intervals if pair not in moments]
    analysis = sorted((data.get('analysis') or {}).get('shots') or [], key=lambda s: _interval(s)[0])
    if not intervals:
        return {}
    start, end = intervals[0]
    target = (start + end) / 2
    candidates = []
    for index, shot in enumerate(analysis, 1):
        at = _thumbnail_at(shot)
        file = _safe_file(shot.get('frame') or shot.get('file'), directory)
        if not file and directory is not None:
            file = _safe_file(f"analysis/frames/shot-{int(shot.get('index') or index):03d}.jpg", directory)
        if at is not None and start <= at < end and file and (key != 'product' or abs(at-target) <= .3):
            candidates.append({'file':file, 'at':at})
    for shot in shots:
        at = _thumbnail_at(shot)
        file = _safe_file(shot.get('frame') or shot.get('file'), directory)
        if at is not None and start <= at < end and file and (key != 'product' or abs(at-target) <= .3):
            candidates.append({'file':file, 'at':at})
    for asset in data.get('assets', []):
        if (asset.get('generated') or asset.get('derived_from') or asset.get('camera_character')
                or asset.get('scene_camera_binding') or asset.get('scene_camera_scene')
                or 'target_state' in (asset.get('roles') or [])):
            continue
        at, file = asset.get('frame_at'), _safe_file(asset.get('file'), directory)
        if isinstance(at, (int,float)) and start <= at < end and file and (key != 'product' or abs(at-target) <= .3):
            candidates.append({'file':file, 'at':at})
    if candidates:
        return min(candidates, key=lambda row:abs(row['at']-target) if key == 'product' else row['at'])
    return thumbnail_request(data, target, [(start,end)])


def original_file(data: dict, key: str, directory: Path | None = None,
                  sid: str | None = None) -> str | None:
    return original_thumbnail(data, key, directory, sid).get('file')


def rounded_time(at: float, ranges: list[tuple[float,float]]) -> float:
    """Prefer hundredths without rounding across a shot/scene boundary."""
    for candidate in (round(at,2), math.floor(at*100)/100, math.ceil(at*100)/100):
        if any(a <= candidate < b for a,b in ranges):
            return candidate
    return at  # A sub-centisecond interval has no representable hundredth.


def thumbnail_request(data: dict, at: float, ranges=None) -> dict:
    at = rounded_time(at,ranges) if ranges else round(at,2)
    value = f'{at:.2f}' if at == round(at,2) else f'{at:.6f}'
    return {'url':f"/api/projects/{data['id']}/thumb?at={value}", 'at':at} if data.get('id') else {}


def cached_thumbnail(pid: str, at) -> Path:
    """On-demand, source-versioned local cache. Never registers assets or saves JSON."""
    from . import media
    try:
        at = float(at)
    except (TypeError, ValueError):
        raise ValueError('请输入有效的缩略图秒数') from None
    with store._lock(pid):
        data = store.load(pid)
        directory = store.project_dir(pid).resolve()
        duration = float((data.get('source') or {}).get('probe', {}).get('duration') or 0)
        if not math.isfinite(at) or at < 0 or duration and at >= duration:
            raise ValueError('缩略图时间不在原片内')
        source_file = _safe_file((data.get('source') or {}).get('file'), directory)
        if not source_file:
            raise ValueError('找不到原片，请先上传原片')
        source = directory / source_file
        stat = source.stat()
        version = hashlib.sha256(f'{source_file}:{stat.st_mtime_ns}:{stat.st_size}'.encode()).hexdigest()[:16]
        cache = (directory / 'analysis/thumbs').resolve()
        if not cache.is_relative_to(directory):
            raise ValueError('缩略图缓存位置无效，请重新上传原片')
        output = cache / f'{version}-{at:.6f}.jpg'
        if output.is_symlink():
            raise ValueError('缩略图缓存位置无效')
        if output.is_file() and output.stat().st_size:
            return output
        cache.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='thumb-', suffix='.jpg', dir=cache)
        os.close(fd)
        temporary = Path(name)
        try:
            media.extract_frame(source, at, temporary, width=360)
            if not temporary.is_file() or not temporary.stat().st_size:
                raise media.MediaError(f'取帧失败 @ {at:.2f}s，请换一个时间')
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
        return output


def original_description(data: dict, key: str, sid: str | None = None, at: float | None = None) -> str:
    pack = data.get('breakdown') or {}
    if key != 'product':
        char = next((c for c in pack.get('characters', []) if c.get('id') == key), {})
        return '，'.join(str(char[k]).strip() for k in ('appearance','outfit') if char.get(k))
    ranges = _ranges(data,sid) if sid else None
    for row in pack.get('productMoments') or []:
        if (ranges is None or _overlaps(row,ranges)) and (at is None or _interval(row)[0] <= at < _interval(row)[1]) and row.get('product'):
            return str(row['product'])
    for row in pack.get('shots') or []:
        if (ranges is None or _overlaps(row,ranges)) and (at is None or _interval(row)[0] <= at < _interval(row)[1]) and _has_product(row.get('product')):
            return str(row['product'])
    return ''


def effective_subjects(data: dict, sid: str) -> list[dict]:
    rep = data.get('replacements') or {}
    pack = data.get('breakdown') or {}
    plan = _plan(data, sid)
    present = present_keys(data, sid)
    added = set(plan.get('added') or [])
    known = {c['id']: c for c in pack.get('characters', []) if c.get('id')}
    casting = {c.get('source_id'): c for c in rep.get('characters', [])}
    order = list(dict.fromkeys([c.get('source_id') for c in rep.get('characters', [])] + list(known)))
    order = [cid for cid in order if cid in known and not _guard().offscreen_character(data, cid)]
    order.append('product')
    rows = []
    for key in order:
        if key not in present and key not in added:
            continue
        global_row = (rep.get('product') or {}) if key == 'product' else casting.get(key, {})
        override = (plan.get('subjects') or {}).get(key) or {}
        aid = override.get('asset_id') if 'asset_id' in override else global_row.get('asset_id')
        default_on = bool(global_row.get('action') == 'replace' and aid)
        if key in added and key not in present:
            default_on = True
        rows.append({'key':key, 'source_id':key, 'name':'商品' if key == 'product' else known[key].get('name') or key,
                     'kind':'product' if key == 'product' else 'character',
                     'on':override.get('on', default_on), 'asset_id':aid,
                     'description':override.get('description', global_row.get('description') or ''),
                     'override':'asset_id' in override or 'description' in override,
                     'added':key in added and key not in present,
                     'original_file':original_file(data, key, sid=sid)})
    return rows


def _legacy_binding(data: dict, sid: str, directory: Path | None = None, *, _old_at=None, _normalize=False) -> str:
    subjects = []
    assets = {a['id']:a for a in data.get('assets', []) if a.get('id')}
    for subject in effective_subjects(data, sid):
        row = {k:subject[k] for k in ('key','on')}
        if subject['on']:
            row['description'] = subject['description']
            row['asset_id'] = subject['asset_id']
            asset = assets.get(subject['asset_id']) or {}
            actual = asset.get('sha256')
            if directory is not None:
                file = _safe_file(asset.get('file'), directory)
                actual = store.sha256_file(directory / file) if file else 'missing'
            row['asset'] = {k:asset.get(k) for k in ('sha256','description','roles')}
            row['actual_sha256'] = actual
        subjects.append(row)
    plan = _plan(data, sid)
    payload = {'subjects':subjects, 'scene':_scene(data, sid),
               'scene_description':(data.get('replacements') or {}).get('scene') or '',
               'frame_at':(frame_choice(data, sid)['at'] if plan.get('frame_at') is not None
                           and frame_mode(data, sid) == 'auto' else plan.get('frame_at'))}
    if _old_at is not None:
        payload['frame_at'] = _old_at
    if _old_at is None or _normalize:
        if isinstance(payload['frame_at'], (int, float)):
            payload['frame_at'] = round(payload['frame_at'], 2)
        payload['scene'] = {**payload['scene']}
        if 'timeRanges' in payload['scene']:
            payload['scene']['timeRanges'] = [[round(t, 2) for t in pair] for pair in payload['scene']['timeRanges']]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def visual_content(data: dict, sid: str, directory: Path | None = None) -> dict:
    """Only picture/script inputs; thumbnail paths and editor labels are excluded."""
    assets = {a['id']: a for a in data.get('assets', []) if a.get('id')}
    subjects = []
    for subject in effective_subjects(data, sid):
        if not subject['on']:
            continue
        asset = assets.get(subject['asset_id']) or {}
        file = _safe_file(asset.get('file'), directory)
        actual = (store.sha256_file(directory / file) if file else 'missing') if directory is not None else asset.get('sha256')
        subjects.append({'key': subject['key'], 'asset_id': subject['asset_id'],
                         'description': subject['description'], 'asset_description': asset.get('description') or '',
                         'sha256': actual})
    scene = _scene(data, sid)
    casting = {c.get('source_id'): c for c in (data.get('replacements') or {}).get('characters', [])}
    overrides = _plan(data, sid).get('subjects') or {}
    removed = [row['key'] for row in effective_subjects(data, sid)
               if row['kind'] == 'character' and not row['on']
               and casting.get(row['key'], {}).get('action') == 'remove'
               and 'on' not in (overrides.get(row['key']) or {})]
    return {'subjects': sorted(subjects, key=lambda row: row['key']), 'removed_subjects': sorted(removed),
            'scene': {k: scene.get(k) for k in ('description', 'lighting', 'merged_from') if scene.get(k)},
            'time_ranges': [[round(a, 2), round(b, 2)] for a, b in _ranges(data, sid)],
            'scene_description': (data.get('replacements') or {}).get('scene') or ''}


def camera_frame(data: dict, asset: dict) -> dict:
    """A generated image owns its capture instant, independently of the editor."""
    record = next((r for r in (data.get('images') or {}).values()
                   if asset.get('id') and asset['id'] in {r.get('id'), r.get('asset_id')}), {})
    return {k: asset.get(k, record.get(k)) for k in ('frame_asset_id', 'frame_at')}


def binding(data: dict, sid: str, directory: Path | None = None, *, frame: dict | None = None) -> str:
    payload = visual_content(data, sid, directory)
    if frame is None:
        plan = _plan(data, sid)
        frame = {'frame_asset_id': plan.get('frame_asset_id'), 'frame_at':
                 frame_choice(data, sid)['at'] if plan.get('frame_at') is not None
                 and frame_mode(data, sid) == 'auto' else plan.get('frame_at')}
    at, aid = frame.get('frame_at'), frame.get('frame_asset_id')
    asset = next((a for a in data.get('assets', []) if a.get('id') == aid), {})
    file = _safe_file(asset.get('file'), directory)
    actual = ((store.sha256_file(directory / file) if file else 'missing') if directory is not None
              else asset.get('sha256')) if aid else None
    payload['frame'] = {'asset_id': aid, 'at': round(at, 2) if isinstance(at, (int, float)) else at,
                        'sha256': actual}
    return 'scene-v2:' + hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                                  separators=(',', ':')).encode()).hexdigest()


def _asset_scene(data: dict, asset: dict) -> tuple[str | None, str | None, bool]:
    sid = asset.get('scene_camera_scene') or asset.get('camera_scene')
    cid = asset.get('camera_character')
    new = bool(asset.get('scene_camera_binding') or asset.get('scene_camera_scene'))
    if str(asset.get('for_task') or '').startswith('scene-camera:'):
        sid, new = asset['for_task'].split(':', 1)[1], True
    for record in (data.get('images') or {}).values():
        if asset.get('id') not in {record.get('asset_id'),record.get('id')}:
            continue
        kind = str(record.get('for_task') or '')
        if kind.startswith('scene-camera:'):
            sid, new = kind.split(':', 1)[1], True
        elif kind.startswith('camera:'):
            parts = kind.split(':')
            cid = cid or parts[1]
            sid = sid or (parts[2] if len(parts) > 2 else _guard().first_scene(data, cid))
    return sid, cid, new


def camera_valid(data: dict, aid: str | None, sid: str, directory: Path | None = None) -> bool:
    if sid not in {s['id'] for s in _guard().scenes(data)}:
        return False
    if directory is None and data.get('id'):
        candidate = store.project_dir(data['id'])
        if candidate.is_dir():
            directory = candidate
    asset = next((a for a in data.get('assets', []) if a.get('id') == aid), None)
    if not asset or asset.get('type') != 'image' or directory is not None and not _safe_file(asset.get('file'), directory):
        return False
    asset_sid, cid, new = _asset_scene(data, asset)
    if asset_sid != sid or aid in scene_edits.archived_images(data):
        return False
    if new:
        saved = asset.get('scene_camera_binding') or asset.get('camera_binding')
        own_frame = camera_frame(data, asset)
        if not any(v is not None for v in own_frame.values()):
            own_frame = None  # Pre-frame fixtures / records retain their old fallback.
        if saved == binding(data, sid, directory, frame=own_frame):
            return True
        if str(saved).startswith('scene-v2:') or visual_content(data, sid, directory)['removed_subjects']:
            return False
        # Read-only compatibility: reconstruct the historical digest with THIS
        # image's own instant, including pre-normalization floating point values.
        frame = own_frame or _plan(data, sid)
        at = frame.get('frame_at')
        if at is None and frame.get('frame_asset_id'):
            at = next((a.get('frame_at') for a in data.get('assets', [])
                       if a.get('id') == frame['frame_asset_id']), None)
        return any(saved == _legacy_binding(data, sid, directory, _old_at=at, _normalize=normalize)
                   for normalize in (False, True))
    if _scene(data, sid).get('merged_from'):
        return False
    if cid not in present_keys(data, sid):
        return False
    # A legacy image knows global casting only; per-scene photo changes cannot
    # be silently treated as if that old image already contained the new photo.
    subjects = {r['key']:r for r in effective_subjects(data, sid)}
    rep = data.get('replacements') or {}
    global_char = next((c for c in rep.get('characters', []) if c.get('source_id') == cid), {})
    subject = subjects.get(cid) or {}
    if not subject.get('on') or subject.get('asset_id') != global_char.get('asset_id') or subject.get('description') != (global_char.get('description') or ''):
        return False
    product = subjects.get('product')
    global_product = rep.get('product') or {}
    if product and (product['on'] != bool(global_product.get('action') == 'replace' and global_product.get('asset_id'))
                    or product['on'] and (product['asset_id'] != global_product.get('asset_id')
                    or product['description'] != (global_product.get('description') or ''))):
        return False
    return _guard().camera_asset_valid(data, aid, directory)


def _cameras(data: dict, sid: str, directory: Path | None = None) -> tuple[list[str], dict[str, list[str]]]:
    fresh, legacy = [], {}
    assets = {a['id']:a for a in data.get('assets', []) if a.get('id')}
    for record in reversed(list((data.get('images') or {}).values())):
        if record.get('state') != 'completed':
            continue
        aid = record.get('asset_id') or record.get('id')
        asset = assets.get(aid) or {}
        asset_sid, cid, new = _asset_scene(data, {**asset, 'id':aid})
        if asset_sid != sid or not camera_valid(data, aid, sid, directory):
            continue
        if new:
            fresh.append(aid)
        else:
            legacy.setdefault(cid, []).append(aid)
    # Some old projects saved only a selected asset, without an image record.
    for character in (data.get('replacements') or {}).get('characters', []):
        cid = character.get('source_id')
        selected = (character.get('camera_assets') or {}).get(sid)
        if not selected and sid == _guard().first_scene(data, cid):
            selected = character.get('camera_asset_id')
        if selected and camera_valid(data, selected, sid, directory):
            versions = legacy.setdefault(cid, [])
            if selected in versions:
                versions.remove(selected)
            versions.insert(0, selected)
    return list(dict.fromkeys(fresh)), legacy


def camera_image_for(data: dict, sid: str, directory: Path | None = None) -> str | None:
    _scene(data, sid)
    selected = _plan(data, sid).get('camera_asset')
    if selected and camera_valid(data, selected, sid, directory):
        return selected
    fresh, legacy = _cameras(data, sid, directory)
    if fresh:
        return fresh[0]
    if len(legacy) == 1:
        return next(iter(legacy.values()))[0]
    return None


def _visible_at(data: dict, at: float) -> tuple[list[str], bool]:
    pack = data.get('breakdown') or {}
    shots = [s for s in pack.get('shots', []) if _interval(s)[0] <= at < _interval(s)[1]]
    visible = {cid for s in shots for cid in s.get('characters') or [] if not _guard().offscreen_character(data,cid)}
    characters = [c['id'] for c in pack.get('characters', []) if c.get('id') in visible]
    product = any(_has_product(s.get('product')) for s in shots) or any(_interval(m)[0] <= at < _interval(m)[1] for m in pack.get('productMoments') or [])
    return characters, product


def _frame_info(data: dict, at: float) -> dict:
    characters, product = _visible_at(data, at)
    names = {c['id']:c.get('name') or c['id'] for c in (data.get('breakdown') or {}).get('characters', []) if c.get('id')}
    def label(key, name):
        description = original_description(data,key,at=at).split('，')[0]
        return f'{name}（{description}）' if description else name
    items = [label(cid,names[cid]) for cid in characters]
    if product:
        items.append(label('product','商品'))
    return {'at':at, 'characters':characters, 'product':product,
            'present_text':'画面里：' + ('、'.join(items) if items else '没有识别到人物或商品')}


def frame_choices(data: dict, sid: str, directory: Path | None = None) -> list[dict]:
    ranges = _ranges(data, sid)
    pack = data.get('breakdown') or {}
    boundaries = {p for pair in ranges for p in pair}
    for row in (pack.get('shots') or []) + (pack.get('productMoments') or []):
        boundaries.update(t for t in _interval(row) if any(a < t < b for a,b in ranges))
    points = sorted(boundaries)
    choices = []
    for start, end in zip(points,points[1:]):
        at = (start + end) / 2
        if not any(a <= at < b for a,b in ranges):
            continue
        file = None
        thumbnails = list(enumerate((data.get('analysis') or {}).get('shots') or [], 1))
        thumbnails += list(enumerate(pack.get('shots') or [], 1))
        for index, shot in thumbnails:
            if _interval(shot)[0] <= at < _interval(shot)[1]:
                known_at = shot.get('frame_at')
                if known_at is None:
                    # Older analysis frames were captured at their shot midpoint.
                    known_at = sum(_interval(shot)) / 2
                if not isinstance(known_at, (int, float)) or not start <= known_at < end:
                    continue
                candidate_file = _safe_file(shot.get('frame') or shot.get('file'), directory)
                if not candidate_file and directory is not None:
                    candidate_file = _safe_file(f"analysis/frames/shot-{int(shot.get('index') or index):03d}.jpg", directory)
                if not candidate_file:
                    continue
                file, at = candidate_file, known_at
                break
        row = {**_frame_info(data, at), 'reason':'已有镜头', 'file':file}
        if not file:
            row.update(thumbnail_request(data,at,[(start,end)]))
        choices.append(row)
    return choices


def frame_choice(data: dict, sid: str, at: float | None = None) -> dict:
    ranges = _ranges(data, sid)
    if at is not None:
        try:
            at = float(at)
        except (ValueError, TypeError):
            raise ValueError('请输入有效的截帧秒数') from None
        if not math.isfinite(at) or not any(a <= at < b for a,b in ranges):
            span = '、'.join(f'{a:.2f}–{b:.2f}' for a,b in ranges)
            raise ValueError(f'截帧时间不在场景 {sid} 内，请输入 {span} 秒之间的时间')
        return {**_frame_info(data, at), 'reason':'手动选择'}
    selected = {r['key'] for r in effective_subjects(data, sid) if r['on']}
    pack = data.get('breakdown') or {}
    boundaries = {p for pair in ranges for p in pair}
    for row in (pack.get('shots') or []) + (pack.get('productMoments') or []):
        boundaries.update(t for t in _interval(row) if any(a < t < b for a,b in ranges))
    points, spans = sorted(boundaries), []
    for start,end in zip(points, points[1:]):
        middle = (start + end) / 2
        if not any(a <= middle < b for a,b in ranges):
            continue
        people, product = _visible_at(data, middle)
        covered = selected & (set(people) | ({'product'} if product else set()))
        # Consecutive breakdown shots can describe one uninterrupted period of
        # everyone being present. Do not select the first small entry shot.
        if spans and spans[-1]['end'] == start and spans[-1]['covered'] == covered:
            spans[-1]['end'] = end
        else:
            spans.append({'start':start, 'end':end, 'covered':covered})

    def rank(span):
        at = (span['start'] + span['end']) / 2
        shots = [s for s in pack.get('shots', []) if _interval(s)[0] <= at < _interval(s)[1]]
        text = ' '.join(str(v) for s in shots for k,v in s.items() if k not in {'characters','start','end'})
        front = bool(re.search(r'正面|面向镜头|面对镜头|facing the camera|\bfront\b', text, re.I))
        behind = bool(re.search(r'背对|背影|from behind|back to camera', text, re.I))
        return len(span['covered']), front - behind, _visible_at(data,at)[1], span['end'] - span['start']

    span = max(spans, key=rank)
    start,end = span['start'],span['end']
    middle = (start + end) / 2
    thumbnails = [_thumbnail_at(s) for s in (data.get('analysis') or {}).get('shots') or []]
    thumbnails = [at for at in thumbnails if at is not None and start <= at < end
                  and abs(at-middle) <= (end-start)/4]
    base = min(thumbnails, key=lambda at:abs(at-middle)) if thumbnails else middle
    # Only refine close to the chosen middle/analysis instant. Captions must
    # never drag a frame back to the moment a selected person enters the shot.
    candidates = [base + offset / 10 for offset in range(-3,4) if start <= base + offset / 10 < end]
    def caption_rank(at):
        masks, missing = _guard().frame_caption_masks(data, at)
        area = int(missing) + sum((m['bbox'][2]-m['bbox'][0])*(m['bbox'][3]-m['bbox'][1]) for m in masks)
        return area, abs(at-base)
    at = min(candidates, key=caption_rank)
    # Analysis-frame buttons keep their precise time. All other automatic
    # choices must agree with the captured and persisted two-decimal value.
    if at not in thumbnails:
        at = rounded_time(at,[(start,end)])
    picked = _frame_info(data,at)
    reason = '已选勾选对象都在画面里的一帧'
    visible = set(picked['characters']) | ({'product'} if picked['product'] else set())
    if selected - visible:
        reason = '没找到所有勾选对象同时出现的镜头，已选能看到最多对象的一帧'
    late = []
    for cid in [c.get('id') for c in pack.get('characters', []) if c.get('id') in selected]:
        entries = [max(a, _interval(s)[0]) for s in pack.get('shots', [])
                   if cid in (s.get('characters') or []) for a,b in ranges
                   if _interval(s)[1] > a and _interval(s)[0] < b]
        if entries and min(entries) > ranges[0][0] + .1 and cid in visible:
            char = next((c for c in pack.get('characters', []) if c.get('id') == cid), {})
            late.append(f"{char.get('name') or cid} {min(entries):.2f} 秒后进画面")
    if late:
        people = [c.get('name') or c['id'] for c in pack.get('characters', [])
                  if c.get('id') in selected & visible]
        together = '、'.join(people)
        result = f'已选{together}都在画面里的一帧' if len(people) > 1 else '已选他在画面里的一帧'
        if selected - visible:
            result = reason
        reason = '，'.join(late + [result])
    return {**picked, 'reason':reason}


def frame_mode(data: dict, sid: str) -> str:
    """Old captured frames retain their provenance without rewriting a project."""
    plan = _plan(data,sid)
    if plan.get('frame_mode') in ('auto','manual'):
        return plan['frame_mode']
    asset = next((a for a in data.get('assets', []) if a.get('id') == plan.get('frame_asset_id')), {})
    if asset.get('frame_reason') == 'scene_auto':
        return 'auto'
    return 'manual' if plan.get('frame_at') is not None or plan.get('frame_asset_id') else 'auto'


def nearest_thumbnail(data: dict, sid: str, at: float, directory: Path | None = None) -> dict:
    ranges = _ranges(data,sid)
    choices = []
    for shot in (data.get('analysis') or {}).get('shots') or []:
        if not _overlaps(shot,ranges):
            continue
        time = _thumbnail_at(shot)
        file = _safe_file(shot.get('frame') or shot.get('file'),directory)
        if (time is not None and file and any(a <= time < b for a,b in ranges) and abs(time-at) <= .3
                and _visible_at(data,time) == _visible_at(data,at)):
            choices.append({'suggested_file':file, 'suggested_at':time})
    if choices:
        return min(choices,key=lambda row:abs(row['suggested_at']-at))
    request = thumbnail_request(data,at,ranges)
    return {'suggested_url':request['url'], 'suggested_at':request['at']} if request else {}


def scene_cast(data: dict, directory: Path | None = None) -> dict:
    result = {}
    assets = {a.get('id'):a for a in data.get('assets', [])}
    for scene in _guard().scenes(data):
        sid = scene['id']
        subjects = effective_subjects(data, sid)
        for row in subjects:
            row['original_description'] = original_description(data,row['key'],sid)
            original = original_thumbnail(data,row['key'],directory,sid)
            casting = original_thumbnail(data,row['key'],directory)
            row.update(original_file=original.get('file'), original_url=original.get('url'),
                       cast_original_file=casting.get('file'), cast_original_url=casting.get('url'))
        plan = _plan(data,sid)
        fresh, legacy = _cameras(data, sid, directory)
        frame_id = plan.get('frame_asset_id')
        frame_asset = assets.get(frame_id) or {}
        at = plan.get('frame_at')
        mode = frame_mode(data,sid)
        frame_at = frame_asset.get('frame_at')
        if at is None and isinstance(frame_at, (int,float)):
            at = frame_at
        # The saved two-decimal input points to this precise captured instant by
        # asset id. A newly typed nearby time still captures a different frame.
        if mode == 'manual' and isinstance(at,(int,float)) and isinstance(frame_at,(int,float)) and round(at,2) == round(frame_at,2):
            at = frame_at
        try:
            choice = frame_choice(data, sid, at if mode == 'manual' else None)
        except ValueError:
            at, frame_id, frame_asset = None, None, {}
            choice = {**frame_choice(data,sid), 'warning':'原来选的参考帧已不在这一场，已重新建议自动选帧，请点“自动选帧”确认'}
        if frame_asset and (frame_asset.get('camera_scene') != sid
                or not isinstance(frame_at, (int,float)) or not math.isfinite(frame_at)
                or abs(frame_at - choice['at']) > .0005):
            frame_id, frame_asset = None, {}
            choice['warning'] = '原来保存的参考帧与当前秒数不一致，请重新选帧'
        if frame_asset.get('scene_frame_reason') and mode == 'manual' and plan.get('frame_mode') != 'manual':
            choice['reason'] = frame_asset['scene_frame_reason']
        frame = {**choice, 'frame_mode':mode, 'asset_id':frame_id, 'file':_safe_file(frame_asset.get('file'), directory)}
        if not frame['file']:
            frame.update(nearest_thumbnail(data,sid,choice['at'],directory))
        current_camera = camera_image_for(data,sid,directory)
        history = [a['id'] for a in data.get('assets', []) if a.get('type') == 'image'
                   and a['id'] not in scene_edits.archived_images(data)
                   and _asset_scene(data,a)[0] == sid and (_asset_scene(data,a)[2]
                   or camera_valid(data,a['id'],sid,directory) or plan.get('camera_asset') == a['id']
                   and _asset_scene(data,a)[1] in present_keys(data,sid))
                   and (a.get('scene_camera_binding') or a.get('scene_camera_scene')
                   or a.get('camera_character') or any(r.get('state') == 'completed' and r.get('asset_id') == a['id']
                   for r in (data.get('images') or {}).values()))]
        display_camera = current_camera or (plan.get('camera_asset') if 'camera_asset' in plan else next(iter(reversed(history)),None))
        result[sid] = {'scene_id':sid, 'merged_from':scene.get('merged_from', []), 'display_camera_asset':display_camera, 'name':scene.get('name') or sid,
                       'time_ranges':[list(pair) for pair in _ranges(data,sid)],
                       'subjects':subjects, 'frame_at':choice['at'], 'frame_asset_id':frame_id, 'frame_mode':mode,
                       'image_subject_keys':[s['key'] for s in subjects if s['on']],
                       'frame':frame, 'frame_choices':frame_choices(data,sid,directory),
                       'camera_asset':current_camera,
                       'camera_frame_notice':camera_frame_notice(data, sid, assets.get(current_camera) or {}, choice['at']),
                       'camera_candidates':list(dict.fromkeys(fresh + [aid for values in legacy.values() for aid in values] + history)),
                       'legacy_notice':'这一场有旧的单人主镜头图，可以点“改用这张”' if len(legacy)>1 else ''}
    return result


def camera_frame_notice(data: dict, sid: str, asset: dict, suggested_at: float) -> str:
    at = camera_frame(data, asset).get('frame_at')
    if not isinstance(at, (int, float)) or abs(at - suggested_at) < .01:
        return ''
    before, _ = _visible_at(data, at)
    after, _ = _visible_at(data, suggested_at)
    missing = [s['name'] for s in effective_subjects(data, sid)
               if s['on'] and s['kind'] == 'character' and s['key'] not in before and s['key'] in after]
    if not missing:
        return ''
    late = '、'.join(missing)
    detail = late + '还没进画面'
    together = '、'.join(s['name'] for s in effective_subjects(data, sid) if s['kind'] == 'character' and s['key'] in after) + '同框'
    return (f'在用的这张主镜头图用的是 {at:.2f} 秒的画面（{detail}）。想要{together}，'
            f'可以改用之前的版本，或按 {suggested_at:.2f} 秒重新生成')


def validate_plan(data: dict, plan: dict) -> None:
    if not isinstance(plan, dict):
        raise ValueError('分场设定格式不对，请刷新后重试')
    characters = {c['id'] for c in (data.get('breakdown') or {}).get('characters', [])
                  if c.get('id') and not _guard().offscreen_character(data,c['id'])}
    allowed = characters | {'product'}
    assets = {a['id']:a for a in data.get('assets', []) if a.get('id')}
    for sid, row in plan.items():
        edits = scene_edits.overlay(data)
        visible = {s['id'] for s in scene_edits.effective_scenes(data, include_dropped=True)}
        validation_data = {**data, 'replacements':{**(data.get('replacements') or {}),
            'scene_edits':{**edits, 'dropped':[]} if sid in visible else {}}}
        _scene(validation_data,sid)
        if not isinstance(row,dict) or not isinstance(row.get('subjects', {}), dict):
            raise ValueError('这一场的人物和商品设定格式不对')
        if not isinstance(row.get('added', []), list) or any(key not in allowed for key in row.get('added', [])):
            raise ValueError('只能添加选角里的人物或商品')
        if 'frame_at' in row and row['frame_at'] is not None:
            frame_choice(validation_data,sid,row['frame_at'])
        if 'frame_mode' in row and row['frame_mode'] not in ('auto','manual'):
            raise ValueError('选帧方式无效，请重新选择自动选帧或手动秒数')
        for key, setting in row.get('subjects', {}).items():
            if key not in allowed or not isinstance(setting,dict):
                raise ValueError('这一场的人物或商品设定无效')
            if 'on' in setting and not isinstance(setting['on'],bool):
                raise ValueError('这一场是否替换的设定格式不对')
            if 'description' in setting and not isinstance(setting['description'],str):
                raise ValueError('补充说明请填写文字')
            if 'asset_id' in setting and (not isinstance(setting['asset_id'],str) or assets.get(setting['asset_id'],{}).get('type') != 'image'):
                raise ValueError('这一场选择的照片不存在，请重新选一张素材图')
        for key in ('frame_asset_id','camera_asset'):
            if row.get(key) is not None and (not isinstance(row[key],str) or assets.get(row[key],{}).get('type') != 'image'):
                raise ValueError('这一场选择的图片不存在，请重新选择')
        frame = assets.get(row.get('frame_asset_id'))
        if frame:
            if frame.get('camera_scene') != sid or not isinstance(frame.get('frame_at'), (int,float)):
                raise ValueError('这张参考帧不属于当前场景，请重新选帧')
            if row.get('frame_at') is not None and round(float(row['frame_at']),2) != round(frame['frame_at'],2):
                raise ValueError('参考帧与截帧时间不一致，请重新选帧')
        camera = assets.get(row.get('camera_asset'))
        if camera and _asset_scene(data,camera)[0] != sid:
            raise ValueError('这张主镜头图不属于当前场景，请重新选择')
