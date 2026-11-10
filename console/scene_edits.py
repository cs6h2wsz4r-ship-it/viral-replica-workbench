"""Read-only scene overlay. All consumers share this effective scene list."""
from __future__ import annotations

import copy
import math


def ranges(scene: dict) -> list[tuple[float, float]]:
    out = []
    for value in scene.get('timeRanges') or []:
        try:
            a, b = map(float, value)
            if math.isfinite(a) and math.isfinite(b) and b > a:
                out.append((a, b))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def original_scenes(data: dict) -> list[dict]:
    raw = (data.get('breakdown') or {}).get('scene') or []
    if not any(ranges(scene) for scene in raw):
        return [{**(raw[0] if raw else {'name': '默认场景'}), 'id': 'S1'}]
    return raw


def adjacent(left: dict, right: dict) -> bool:
    a, b = ranges(left), ranges(right)
    return bool(a and b and abs(a[-1][1] - b[0][0]) < 1e-6)


def overlay(data: dict) -> dict:
    value = (data.get('replacements') or {}).get('scene_edits')
    return value if isinstance(value, dict) else {}


def merge_groups(data: dict) -> list[list[str]]:
    raw = original_scenes(data)
    ids = [s.get('id') for s in raw]
    used, groups = set(), []
    for group in overlay(data).get('merges') or []:
        if (not isinstance(group, list) or len(group) < 2 or any(not isinstance(s, str) for s in group)
                or len(set(group)) != len(group) or any(s not in ids or s in used for s in group)):
            continue
        start = ids.index(group[0])
        if ids[start:start+len(group)] != group:
            continue
        rows = raw[start:start+len(group)]
        if not all(adjacent(a, b) for a, b in zip(rows, rows[1:])):
            continue
        groups.append(group)
        used.update(group)
    return groups


def effective_scenes(data: dict, *, include_dropped: bool = False) -> list[dict]:
    raw = original_scenes(data)
    groups = merge_groups(data)
    by_first = {g[0]: g for g in groups}
    hidden = {sid for g in groups for sid in g[1:]}
    by_id = {s['id']: s for s in raw}
    out = []
    for scene in raw:
        sid = scene['id']
        if sid in hidden:
            continue
        if sid in by_first:
            group = by_first[sid]
            intervals = sorted(pair for member in group for pair in ranges(by_id[member]))
            union = []
            for a, b in intervals:
                if union and a <= union[-1][1] + 1e-6:
                    union[-1][1] = max(union[-1][1], b)
                else:
                    union.append([a, b])
            scene = {**scene, 'name': ' + '.join(by_id[s].get('name') or s for s in group),
                     'timeRanges': union, 'merged_from': list(group)}
        out.append(scene)
    dropped = {s for s in overlay(data).get('dropped') or [] if isinstance(s, str)}
    # Corrupt/stale saved overlays must never remove the last remaining scene.
    if out and all(s['id'] in dropped for s in out):
        dropped.discard(out[-1]['id'])
    return [{**s, 'dropped': True} if s['id'] in dropped else s for s in out] if include_dropped else [s for s in out if s['id'] not in dropped]


def plan(data: dict, sid: str) -> dict:
    edits = overlay(data)
    # A reanalysis may remove one constituent id. Ignore that entire merge,
    # including its composed settings; derive the original saved setting.
    active = {g[0] for g in merge_groups(data)}
    backups = edits.get('original_plans') or {}
    for first, saved in backups.items():
        if first not in active and sid in saved:
            return saved[sid] or {}
    return ((data.get('replacements') or {}).get('scene_plan') or {}).get(sid) or {}


def fingerprint_edits(data: dict) -> dict:
    groups = merge_groups(data)
    dropped = [s['id'] for s in effective_scenes(data, include_dropped=True) if s.get('dropped')]
    return {'merges': groups, 'dropped': dropped} if groups or dropped else {}


def archived_images(data: dict) -> set[str]:
    edits = overlay(data)
    archived = set(edits.get('archived_images') or [])
    active = {g[0] for g in merge_groups(data)}
    for first, ids in (edits.get('original_images') or {}).items():
        if first not in active:
            archived.difference_update(ids)
    return archived


def _camera_ids(data: dict, sid: str) -> list[str]:
    ids = [a['id'] for a in data.get('assets', []) if (a.get('scene_camera_scene') or a.get('camera_scene')) == sid
           and (a.get('scene_camera_binding') or a.get('scene_camera_scene') or a.get('camera_character'))]
    ids += [r.get('asset_id') or r.get('id') for r in (data.get('images') or {}).values()
            if (r.get('for_task') == f'scene-camera:{sid}' or str(r.get('for_task') or '').startswith('camera:')
                 and str(r.get('for_task') or '').endswith(':'+sid))]
    return list(dict.fromkeys(a for a in ids if a))


def discard_invalid_merges(data: dict) -> None:
    """Materialize ignored snapshots in memory; callers own any persistence."""
    edits = copy.deepcopy(overlay(data))
    active = {g[0] for g in merge_groups(data)}
    backups = edits.get('original_plans') or {}
    invalid = set(backups) - active
    if not invalid:
        return
    rep = data.setdefault('replacements', {})
    plans = rep.setdefault('scene_plan', {})
    archived = set(edits.get('archived_images') or [])
    known = {s['id'] for s in original_scenes(data)}
    for first in invalid:
        archived.update(_camera_ids(data, first))
        archived.difference_update((edits.get('original_images') or {}).pop(first, []))
        for sid, value in backups.pop(first).items():
            if sid not in known or value is None:
                plans.pop(sid, None)
            else:
                plans[sid] = copy.deepcopy(value)
    edits['merges'] = copy.deepcopy(merge_groups(data))
    edits['archived_images'] = sorted(archived)
    rep['scene_edits'] = edits


def apply(data: dict, action: str, sid: str, next_sid: str | None = None) -> None:
    """Mutate only a locked in-memory project; the caller saves atomically."""
    discard_invalid_merges(data)
    rows = effective_scenes(data, include_dropped=True)
    row = next((s for s in rows if s['id'] == sid), None)
    if row is None:
        raise ValueError('当前项目没有这一场，请刷新后重试')
    if action not in {'merge', 'split', 'drop', 'restore'}:
        raise ValueError('不支持这个分场操作，请刷新后重试')
    rep = data.setdefault('replacements', {})
    edits = copy.deepcopy(overlay(data))
    edits['merges'] = copy.deepcopy(merge_groups(data))
    edits['dropped'] = [s['id'] for s in rows if s.get('dropped')]
    plans = rep.setdefault('scene_plan', {})
    if action == 'merge':
        index = rows.index(row)
        other = rows[index+1] if index+1 < len(rows) else None
        if row.get('dropped') or not other or other.get('dropped') or other['id'] != next_sid or not adjacent(row, other):
            raise ValueError('只能和时间上紧挨着的下一场合并，请先恢复已去掉的场景')
        left = row.get('merged_from') or [sid]
        right = other.get('merged_from') or [next_sid]
        backups = edits.setdefault('original_plans', {})
        saved = {}
        for scene in (row, other):
            if scene.get('merged_from'):
                saved.update(backups.pop(scene['id'], {}))
            else:
                saved[scene['id']] = copy.deepcopy(plans.get(scene['id']))
        backups[sid] = saved
        originals = edits.setdefault('original_images', {})
        original_ids, cameras = [], []
        archived = archived_images(data)
        subjects, added = {}, []
        from . import scene_plan
        for scene in (row, other):
            current_ids = _camera_ids(data, scene['id'])
            cameras.extend(current_ids)
            original_ids.extend(originals.pop(scene['id'], []) if scene.get('merged_from') else
                                [aid for aid in current_ids if aid not in archived])
            current = plan(data, scene['id'])
            settings = current.get('subjects') or {}
            for subject in scene_plan.effective_subjects(data,scene['id']):
                key = subject['key']
                subjects[key] = {**settings.get(key, {}), 'on':subject['on'], **subjects.get(key, {})}
            added.extend(current.get('added') or [])
        originals[sid] = list(dict.fromkeys(original_ids))
        edits['archived_images'] = list(dict.fromkeys(sorted(archived) + cameras))
        plans[sid] = {'subjects': subjects, 'added': list(dict.fromkeys(added)), 'camera_asset': None}
        edits['merges'] = [g for g in edits['merges'] if g[0] not in {sid, next_sid}] + [left+right]
    elif action == 'split':
        if not row.get('merged_from'):
            raise ValueError('这一场没有合并，可以直接使用')
        saved = edits.get('original_plans', {}).pop(sid, {})
        originals = set(edits.get('original_images', {}).pop(sid, []))
        archived = set(edits.get('archived_images') or [])
        archived.update(_camera_ids(data,sid))
        edits['archived_images'] = sorted(archived - originals)
        for member in row['merged_from']:
            original = saved.get(member)
            if original is None:
                plans.pop(member, None)
            else:
                plans[member] = copy.deepcopy(original)
        edits['merges'] = [g for g in edits['merges'] if g[0] != sid]
    elif action == 'drop':
        if not row.get('dropped') and len(effective_scenes(data)) <= 1:
            raise ValueError('至少要保留一场')
        edits['dropped'] = list(dict.fromkeys(edits['dropped']+[sid]))
    else:
        edits['dropped'] = [s for s in edits['dropped'] if s != sid]
    rep['scene_edits'] = edits
