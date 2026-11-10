"""Read-only master dashboards and explicitly confirmed, bounded batch work.

Queues are deliberately process-local: importing/reloading or reading a board never
resumes generation. Persisted preview markers only invalidate old confirmations.
"""
from __future__ import annotations

from contextlib import ExitStack
import copy
import json
import math
from pathlib import Path
import re
import threading
import uuid

from . import config, director, final_preview, fission, gateway, jobs, segment_boards, segment_plan, store
from . import storyboard_edits as edits
from .channel_text import user_message

CHANGED = '刚才有内容改过，清单已更新，请再确认一次'
LIMITS = {'images': 2, 'videos': 3}
ACTIVE = {'running', 'submitting', 'query_pending', 'unknown'}
FAILED = {'failed', 'error', 'query_pending', 'unknown'}
_lock = threading.RLock()
_reason_slots = threading.BoundedSemaphore(2)
_queues = {'images': [], 'videos': []}
_admitted = {}


def remote_active(record):
    # Legacy recovery can label a stopped query worker as failed while its
    # remote task is still unresolved. Retry evidence outranks that label.
    return bool(record.get('state') in ACTIVE or record.get('query_retry'))


def has_remote_work(data):
    return any(remote_active(record) for record in data.get('images', {}).values()) or any(
        remote_active(record) for runs in data.get('runs', {}).values() for record in runs)


def _identity(kind, record):
    keys = ('id', 'preview_sha256', 'run_dir') if kind == 'images' else ('rev', 'job_id', 'job_sha256', 'package')
    return [record.get(key) for key in keys]


def _same_record(item, record):
    return bool(record and record.get('batch_confirmation') == item['claim']
                and _identity(item['kind'], record) == item['_identity'])


def _terminal(item, record):
    return bool(_same_record(item, record) and not record.get('query_retry')
                and record.get('state') in {'completed', 'failed', 'error', 'cancelled'})


def _kind(kind):
    if kind not in LIMITS:
        raise ValueError('未知批量操作')


def _batches(master_id):
    store.project_dir(master_id)  # Validate even when the master was deleted.
    batches = []
    for path in config.BATCHES_DIR.glob('*.json'):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if value.get('master_id') == master_id:
                batches.append(value)
        except (OSError, ValueError):
            continue
    return sorted(batches, key=lambda b: (b.get('created_at', ''), b['id']), reverse=True)


def _members(batches):
    return sorted({item['project_id'] for batch in batches for item in batch.get('items', [])
                   if isinstance(item.get('project_id'), str) and store.SAFE_ID.fullmatch(item['project_id'])})


def _file(directory, value):
    path = (directory / str(value or '')).resolve()
    return path if path.is_relative_to(directory.resolve()) and path.is_file() else None


def _files(value, directory):
    """Fingerprint referenced inputs; reject traversal without opening other files."""
    result = {}
    def visit(node, key=''):
        if isinstance(node, dict):
            for name, child in node.items():
                visit(child, name)
        elif isinstance(node, list):
            for child in node:
                visit(child, key)
        elif isinstance(node, str) and (key in {'file', 'clean_file', 'path'} or key.startswith('c') and re.fullmatch(r'c\d+', key)):
            path = _file(directory, node)
            result[node] = store.sha256_cached(path) if path else 'missing'
    visit(value)
    return result


def _settings_version():
    # File metadata binds settings changes without reading credentials during GET.
    try:
        stat = config.ENV_FILE.stat()
        return [stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
    except FileNotFoundError:
        return None


def _binding(data, kind, rec):
    directory = store.project_dir(data['id'])
    sid = rec['storyboard_context']['sheet_id'] if kind == 'images' else rec['_segment_id']
    state = segment_boards.state_for(data, sid)
    materials = edits.material_options(data, sid, directory)
    segment = segment_plan.get_segment(data, sid)
    record = {key: value for key, value in rec.items()
              if key not in {'batch_confirmation', 'batch_error', '_segment_id'}}
    inputs = dict(record=record, replacements=data.get('replacements'), segment=segment,
                  storyboard=data.get('storyboard'), source=data.get('source'), materials=materials,
                  board=state, routes=data.get('routes'))
    if kind == 'videos':
        inputs['settings'] = _settings_version()
        inputs['director'] = {key: value for key, value in director.get_segment(data, sid).items()
                              if key != 'selected_run'}
    return segment_plan.digest([inputs, _files(inputs, directory)])


def _key(kind, pid, sid, ident):
    return kind, pid, sid, ident


def _record(data, item):
    if item['kind'] == 'images':
        return data.get('images', {}).get(item['image_id'])
    return next((r for r in data.get('runs', {}).get(item['segment_id'], []) if r.get('rev') == item['rev']), None)


def _board_ready(data, sid, directory):
    try:
        director.require_ready(data, directory, sid)
        return True
    except (ValueError, KeyError, OSError):
        return False


def _prompt_ready(data, sid, directory):
    try:
        row = copy.deepcopy(director.get_segment(data, sid))
        director.refresh_prompt_status(row, data)
        return (not director.is_legacy(data.get('director')) and not row.get('needs_rewrite')
                and row.get('inputs_sig') == director.segment_inputs_sig(data, directory, sid)
                and row.get('plan_sig') == segment_plan.segment_sig(segment_plan.get_segment(data, sid)))
    except (ValueError, KeyError, OSError):
        return False


def _image_valid(data, rec):
    ctx = rec.get('storyboard_context') or {}
    if (rec.get('state') != 'pending_confirmation' or rec.get('batch_error') or not ctx.get('sheet_id')
            or ctx.get('cell_id') or ctx.get('selected_cells') or ctx.get('fix_plan')):
        return False
    try:
        state = segment_boards.state_for(data, ctx['sheet_id'])
        if ctx.get('storyboard_sig') != state.get('storyboard_sig') or ctx.get('plan_sig') != (state.get('plan') or {}).get('sig'):
            return False
        edits.verify_preview(data['id'], rec)
        preview = _file(store.project_dir(data['id']), str(rec.get('run_dir', '')) + '/image-job.preview.json')
        if not preview or store.sha256_file(preview) != rec.get('preview_sha256'):
            return False
        if rec.get('channel') == 'official':
            from . import jimmy_bridge
            jimmy_bridge._official_preview_current(data['id'], rec['id'])
        return True
    except (ValueError, KeyError, OSError, gateway.GatewayError):
        return False


def _video_valid(data, sid, rec):
    if rec.get('state') != 'pending_confirmation' or rec.get('batch_error') or rec.get('fix_plan_binding'):
        return False
    directory = store.project_dir(data['id'])
    try:
        from . import jimmy_bridge
        segment = director.get_segment(data, sid)
        if not _board_ready(data, sid, directory) or not _prompt_ready(data, sid, directory):
            return False
        if rec.get('director_sig') != director.segment_sig(segment):
            return False
        jimmy_bridge.verify_preview_current(data, copy.deepcopy(segment), rec, directory)
        path = _file(directory, str(rec['package']) + '/review/jimmy-job.preview.json')
        if not path or store.sha256_file(path) != rec.get('job_sha256'):
            return False
        package = json.loads(path.read_text(encoding='utf-8'))
        handoff = _file(directory, str(rec['package']) + '/review/jimmy-handoff.json')
        if not handoff or json.loads(handoff.read_text(encoding='utf-8')).get('state') != 'ready_for_confirmation':
            return False
        def verify(node):
            if isinstance(node, dict):
                if node.get('path'):
                    reference = _file(directory, node['path'])
                    # Cached for board polling; jimmy_bridge.confirm re-verifies the package from disk.
                    if not reference or node.get('sha256') and store.sha256_cached(reference) != node['sha256']:
                        raise ValueError('视频参考文件已改变')
                for child in node.values():
                    verify(child)
            elif isinstance(node, list):
                for child in node:
                    verify(child)
        verify(package)
        _billing(rec)
        return True
    except (ValueError, KeyError, TypeError, OSError, gateway.GatewayError):
        return False


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('计费预览缺少秒数，请打开项目重新生成预览')
    return value


def _billing(rec):
    output = rec.get('output_seconds')
    if output is None:
        output = sum(_number(task.get('duration')) for task in (rec.get('preview') or {}).get('tasks', []))
    reference = rec.get('reference_seconds')
    if reference is None:
        match = re.search(r'参考视频\s*(\d+(?:\.\d+)?)\s*(?:s|秒)', rec.get('billing_note') or '')
        if match:
            reference = float(match.group(1))
        elif any(task.get('reference_video_ids') for task in (rec.get('preview') or {}).get('tasks', [])):
            raise ValueError('计费预览缺少参考视频秒数，请打开项目重新生成预览')
        else:
            reference = 0
    return dict(billing_seconds=_number(rec.get('billing_seconds')), output_seconds=_number(output), reference_seconds=_number(reference))


def _pending(data, kind):
    output = []
    for index, segment in enumerate((data.get('segments_plan') or {}).get('segments', []), 1):
        sid = segment['id']
        records = (list(data.get('images', {}).values()) if kind == 'images' else data.get('runs', {}).get(sid, []))
        for rec in reversed(records):
            if kind == 'images' and (rec.get('storyboard_context') or {}).get('sheet_id') != sid:
                continue
            ident = rec.get('id') if kind == 'images' else rec.get('rev')
            if _key(kind, data['id'], sid, ident) in _admitted:
                break
            # A later accepted generation supersedes older unpaid previews,
            # including a manual single/multi-cell repair of this segment.
            if kind == 'images' and (remote_active(rec) or rec.get('state') == 'completed'):
                break
            valid = _image_valid(data, rec) if kind == 'images' else _video_valid(data, sid, rec)
            if not valid:
                continue
            item = dict(kind=kind, project_id=data['id'], name=data['name'], segment_id=sid, segment_index=index,
                        channel=rec.get('channel') or 'third_party')
            if kind == 'images':
                item['image_id'] = rec['id']
                item.update(model=(rec.get('preview') or {}).get('model'),
                            quality=(rec.get('preview') or {}).get('quality'))
            else:
                item.update(rev=rec['rev'], job_sha256=rec['job_sha256'], **_billing(rec))
            item['_binding'] = _binding(data, kind, {**rec, '_segment_id': sid})
            item['_claim'] = rec.get('batch_confirmation')
            output.append(item)
            break  # Same as v5BoardPreview: latest valid whole-segment preview.
    return output


def _photos(data, directory):
    try:
        return [dict(key=c['key'], kind=c['kind'], name=c['master_name'], file=c['file'])
                for c in fission.columns(data, directory)[0] if c.get('file')]
    except (ValueError, KeyError, OSError):
        return []


def _cell_image_record(data, versions, version, cid):
    """Untouched cells in a partial redraw retain their original model provenance."""
    seen = set()
    by_id = {v['id']: v for v in versions}
    while version and version.get('id') not in seen:
        seen.add(version.get('id'))
        record = (data.get('images') or {}).get(version.get('image_id'), {})
        context = record.get('storyboard_context') or {}
        origin = version.get('from') or ''
        selected = version.get('selected_cells') or context.get('selected_cells')
        if not selected:
            selected = origin[6:].split(',') if origin.startswith('cells:') else [origin[5:]] if origin.startswith('cell:') else []
        if not selected and context.get('cell_id'):
            selected = [context['cell_id']]
        if not selected or cid in selected:
            return record
        parent = version.get('base_version') or context.get('base_current')
        index = versions.index(version)
        version = by_id.get(parent) if parent else versions[index-1] if index else None
    return {}


def _boards(data):
    cells = {c['id']: c for c in (data.get('storyboard') or {}).get('cells', [])}
    result = []
    for segment_index, segment in enumerate((data.get('segments_plan') or {}).get('segments', []), 1):
        state = segment_boards.state_for(data, segment['id'])
        version = segment_boards.current(data, segment['id']) or {}
        for cid in segment.get('cells', []):
            cell = cells.get(cid, {})
            if (version.get('cells') or {}).get(cid):
                record = _cell_image_record(data, state.get('versions', []), version, cid)
                preview = record.get('preview') or {}
                result.append(dict(file=version['cells'][cid], cell_id=cid, index=cell.get('index'),
                                   segment_id=segment['id'], segment_index=segment_index, version=version.get('id'),
                                   start=cell.get('start'), end=cell.get('end'), channel=record.get('channel'),
                                   model=preview.get('model'), quality=preview.get('quality')))
    return result


def _latest_image(data, sid):
    return next((r for r in reversed(list(data.get('images', {}).values()))
                 if (r.get('storyboard_context') or {}).get('sheet_id') == sid), {})


def _selected(data, sid, directory):
    try:
        segment = director.get_segment(data, sid)
        rev = segment.get('selected_run')
        planned = segment_plan.get_segment(data, sid)
        if any(segment.get(key) != planned.get(key) for key in ('cells', 'source_start', 'source_end')):
            return False
        run = next((r for r in data.get('runs', {}).get(sid, []) if r.get('rev') == rev), {})
        if any(key in run and run[key] != planned.get(key) for key in ('source_start', 'source_end')):
            return False
        return bool(run.get('state') == 'completed' and _file(directory, run.get('result')))
    except (ValueError, KeyError):
        return False


def _row(data, pending):
    pid = data['id'];directory = store.project_dir(pid);segments = (data.get('segments_plan') or {}).get('segments', [])
    active_jobs = jobs.snapshot(pid);errors = [];steps = {'boards': [], 'prompts': [], 'videos': []}
    prepare_images = [];prepare_videos = []
    all_boards = bool(segments) and all(_board_ready(data, s['id'], directory) for s in segments)
    for index, segment in enumerate(segments, 1):
        sid = segment['id'];image = _latest_image(data, sid)
        runs = data.get('runs', {}).get(sid, []);run = runs[-1] if runs else {}
        image_ready = _board_ready(data, sid, directory);prompt_ready = _prompt_ready(data, sid, directory)
        video_ready = _selected(data, sid, directory)
        for stage, rec, kind, ready in (('boards', image, 'images', image_ready), ('videos', run, 'videos', video_ready)):
            has_pending = any(item['segment_id'] == sid for item in pending[kind])
            queued = any(key[0] == kind and key[1] == pid and key[2] == sid for key in _admitted)
            prep = ((data.get('fission_preparation' if kind == 'images' else 'fission_video_preparation') or {}).get('segments') or {}).get(sid, {})
            scheduled = active_jobs.get('fission-prepare' if kind == 'images' else 'fission-video-prepare', {}).get('state') == 'running'
            preparing = scheduled and prep.get('status') == 'preparing'
            admission_error = next((entry.get('error') for key, entry in _admitted.items()
                                    if key[0] == kind and key[1] == pid and key[2] == sid and entry.get('error')), None)
            error = admission_error or rec.get('batch_error')
            if not error and (rec.get('state') in FAILED or rec.get('query_retry')):
                error = rec.get('error') or rec.get('query_error') or '查询尚未完成，请重查'
            if not error and not ready and not has_pending and not queued and prep.get('status') == 'failed':
                error = prep.get('error') or '准备失败，请打开这一段重试'
            if not error and not ready and not has_pending and not queued and not scheduled and prep.get('status') in ('queued', 'preparing'):
                error = '准备任务已中断，请重新准备这一段'
            state = 'failed' if error else 'running' if queued or remote_active(rec) or preparing else 'pending_confirmation' if has_pending else 'completed' if ready else 'idle'
            phase = prep.get('phase')
            action = ('已确认，排队中' if queued and rec.get('state') == 'pending_confirmation' else
                      '正在准备预览' if preparing and phase == 'preview' else
                      '正在整理改法' if preparing and kind == 'images' else
                      '正在写提示词' if preparing else '正在生成' if state == 'running' else '等确认' if has_pending else '')
            detail = f'第 {index} 段' + action if action else ''
            steps[stage].append(dict(done=ready, state=state, detail=detail))
            if error:
                retry = dict(kind='open', project_id=pid, segment_id=sid)
                if rec.get('state') == 'query_pending' or rec.get('query_retry') and rec.get('remote_task_id'):
                    retry.update(kind='image-query' if kind == 'images' else 'video-query')
                    retry.update(image_id=rec.get('id')) if kind == 'images' else retry.update(rev=rec.get('rev'))
                errors.append(dict(segment_id=sid, segment_index=index, stage=stage, error=user_message(error), retry=retry))
            blocked = queued or scheduled or remote_active(rec) or has_pending or rec.get('state') in FAILED or rec.get('batch_error')
            if kind == 'images' and not ready and not blocked:
                prepare_images.append(sid)
            if kind == 'videos' and all_boards and not ready and not blocked and not any(r.get('state') == 'completed' for r in runs):
                prepare_videos.append(sid)
        prep = ((data.get('fission_video_preparation') or {}).get('segments') or {}).get(sid, {})
        writing = not prompt_ready and prep.get('status') == 'preparing' and active_jobs.get('fission-video-prepare', {}).get('state') == 'running'
        steps['prompts'].append(dict(done=prompt_ready, state='completed' if prompt_ready else 'running' if writing else 'failed' if prep.get('status') == 'failed' else 'idle', detail=f'第 {index} 段正在写提示词' if writing else ''))
    progress = {}
    for stage, values in steps.items():
        state = next((s for s in ('failed', 'running', 'pending_confirmation') if any(v['state'] == s for v in values)),
                     'completed' if values and all(v['done'] for v in values) else 'idle')
        progress[stage] = dict(done=sum(v['done'] for v in values), total=len(values), state=state,
                               detail=next((v['detail'] for v in values if v['state'] == state and state != 'completed'), ''))
    saved = final_preview.view(data, directory)
    complete = bool(saved.get('file') and not saved.get('stale') and _file(directory, saved['file']))
    final_job = next((job for key, job in active_jobs.items() if key.startswith('fission-final:') and job.get('state') == 'running'), {})
    final_error = None if complete else (data.get('fission_final') or {}).get('error')
    progress['final'] = dict(done=int(complete), total=1, state='completed' if complete else 'running' if final_job.get('state') == 'running' else 'failed' if final_error else 'idle', detail='正在拼接成片' if final_job.get('state') == 'running' else '')
    if final_error:
        errors.append(dict(stage='final', segment_id=None, error=user_message(final_error), retry={'kind':'open','project_id':pid}))
    derived = data.get('derived_from') or {};names = {c['id']: c.get('name', '') for c in (data.get('breakdown') or {}).get('characters', [])}
    changes = '；'.join(('商品' if c.get('key') == 'product' else (c.get('key', '') + ' ' + names.get(c.get('key'), '')).strip()) + ' → ' + str(c.get('to_name') or '') for c in derived.get('changes', []))
    return dict(project_id=pid, name=data['name'], number=derived.get('fission_number', derived.get('row')),
                changes=changes, photos=_photos(data, directory), boards=_boards(data), progress=progress, errors=errors,
                final=saved if complete else None, _prepare_images=prepare_images, _prepare_videos=prepare_videos)


def _collect(master_id):
    batches = _batches(master_id);pending = {'images': [], 'videos': []};result = [];seen = set();source = {}
    for batch in batches:
        rows = []
        for item in batch.get('items', []):
            pid = item.get('project_id')
            if not pid or pid in seen:
                continue
            try:
                data = store.load(pid)
                if (data.get('derived_from') or {}).get('project_id') != master_id:
                    continue
            except (ValueError, FileNotFoundError):
                continue
            seen.add(pid);source[pid] = data
            candidates = {kind: _pending(data, kind) for kind in LIMITS}
            for kind in LIMITS:
                pending[kind].extend(candidates[kind])
            rows.append(_row(data, candidates))
        if rows:
            result.append(dict(id=batch['id'], created_at=batch.get('created_at'), items=rows))
    membership = [(b['id'], [item.get('project_id') for item in b.get('items', [])]) for b in batches]
    summaries = {}
    for kind, items in pending.items():
        summary = dict(token=segment_plan.digest([master_id, kind, membership, items]) if items else None,
                       project_count=len({item['project_id'] for item in items}), items=items)
        if kind == 'images':
            summary['image_count'] = len(items)
        else:
            summary.update({key: sum(item[key] for item in items) for key in ('billing_seconds', 'output_seconds', 'reference_seconds')})
        summaries[kind] = summary
    try:
        master = store.load(master_id);master_row = dict(id=master_id, name=master['name'], exists=True,
                                                       photos=_photos(master, store.project_dir(master_id)), boards=_boards(master))
    except FileNotFoundError:
        name = next((((d.get('derived_from') or {}).get('master_snapshot') or {}).get('name') for d in source.values()), '')
        master_row = dict(id=master_id, name=name or '母版已删除', exists=False, photos=[], boards=[])
    rows = [row for batch in result for row in batch['items']]
    return dict(master=master_row, batches=result, total=len(rows), completed=sum(bool(row['final']) for row in rows),
                active=any(value['state'] == 'running' for row in rows for value in row['progress'].values()),
                can_prepare_images=any(row['_prepare_images'] for row in rows), can_prepare_videos=any(row['_prepare_videos'] for row in rows), pending=summaries)


def _public(view):
    view = copy.deepcopy(view)
    for batch in view['batches']:
        for row in batch['items']:
            row.pop('_prepare_images');row.pop('_prepare_videos')
    for summary in view['pending'].values():
        for item in summary['items']:
            item.pop('_binding', None);item.pop('_claim', None)
    return view


def view(master_id):
    with _lock:
        return _public(_collect(master_id))


def prepare(master_id, kind):
    _kind(kind)
    with _lock:
        snapshot = _collect(master_id)
        for batch in snapshot['batches']:
            for row in batch['items']:
                selected = row['_prepare_' + kind];pid = row['project_id']
                if not selected:
                    continue
                def work(pid=pid, selected=tuple(selected)):
                    for sid in selected:
                        if kind == 'images':
                            # Existing helper applies its own two-request reason limit.
                            fission.prepare_segment(pid, sid, preserve_plan=True)
                            continue
                        try:
                            store.update(pid, lambda d:d.setdefault('fission_video_preparation', {}).setdefault('segments', {}).setdefault(sid, {}).update(status='preparing', phase='prompt', error=None))
                            with _reason_slots:
                                data = store.load(pid);directory = store.project_dir(pid)
                                director.require_ready(data, directory)
                                if not _prompt_ready(data, sid, directory):
                                    director.save(pid, {'action':'rewrite', 'segment_id':sid})
                                store.update(pid, lambda d:d['fission_video_preparation']['segments'][sid].update(phase='preview'))
                                rec = gateway.video_preview(pid, sid)
                            store.update(pid, lambda d:d['fission_video_preparation']['segments'][sid].update(status='pending_confirmation', rev=rec['rev'], error=None))
                        except Exception as exc:
                            message = user_message(exc)
                            store.update(pid, lambda d:d.setdefault('fission_video_preparation', {}).setdefault('segments', {}).setdefault(sid, {}).update(status='failed', error=message))
                jobs.start(pid, 'fission-prepare' if kind == 'images' else 'fission-video-prepare', work,
                           '准备裂变新分镜' if kind == 'images' else '准备裂变视频提示词和预览')
    return view(master_id)


def confirm(master_id, kind, body):
    _kind(kind)
    with _lock, store._lock(master_id), ExitStack() as locks:
        batches = _batches(master_id)
        for pid in _members(batches):
            locks.enter_context(store._lock(pid))
        # A child can itself create a batch while holding its project lock.
        # Match create's project -> batch order; the master lock already keeps
        # this dashboard's membership stable during the initial batch read.
        locks.enter_context(fission._batch_lock)
        snapshot = _collect(master_id);summary = snapshot['pending'][kind]
        if not body.get('token') or summary['token'] != body['token'] or not summary['items']:
            raise ValueError(CHANGED)
        # Every item was revalidated while all member project locks are held.
        # Claim before releasing them; repeated/competing confirmations see no list.
        claim = uuid.uuid4().hex
        claims = []
        for item in summary['items']:
            record = _record(store.load(item['project_id']), item)
            entry = {**item, 'master_id': master_id, 'claim': claim, '_identity': _identity(kind, record)}
            ident = item.get('image_id') if kind == 'images' else item['rev']
            key = _key(kind, item['project_id'], item['segment_id'], ident)
            store.update(item['project_id'], lambda d, item=item:_record(d, item).update(batch_confirmation=claim))
            claims.append((key, entry))
        # A disk-write failure may invalidate tokens, but cannot leave a partly
        # runnable queue. Nothing is admitted until every claim was persisted.
        for key, entry in claims:
            _admitted[key] = entry
            _queues.setdefault(kind, []).append(key)
    _pump(kind)
    return view(master_id)


_remote_cache = {}


def _remote_keys(kind):
    """Remote-active keys across all projects; reparse a project only when its file changed."""
    alive = set();keys = set()
    for path in config.PROJECTS_DIR.glob('*/project.json'):
        pid = path.parent.name
        if not store.SAFE_ID.fullmatch(pid):
            continue
        try:
            stat = path.stat();stamp = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        except FileNotFoundError:
            continue
        alive.add(path)
        cached = _remote_cache.get(path)
        if not cached or cached[0] != stamp:
            try:
                current = store.load(pid)
            except (FileNotFoundError, ValueError):
                continue
            found = {'images': set(), 'videos': set()}
            for iid, rec in current.get('images', {}).items():
                if remote_active(rec):
                    found['images'].add(_key('images', pid, (rec.get('storyboard_context') or {}).get('sheet_id'), iid))
            for sid, runs in current.get('runs', {}).items():
                for rec in runs:
                    if remote_active(rec):
                        found['videos'].add(_key('videos', pid, sid, rec.get('rev')))
            cached = _remote_cache[path] = (stamp, found)
        keys.update(cached[1][kind])
    for path in set(_remote_cache) - alive:
        _remote_cache.pop(path, None)
    return keys


def _pump(kind):
    """Reserve live remote tasks, not just quick submission threads."""
    with _lock:
        queue = _queues.setdefault(kind, [])
        for key, item in list(_admitted.items()):
            if key[0] != kind or key in queue:
                continue
            try:
                rec = _record(store.load(item['project_id']), item)
            except FileNotFoundError:
                # Removing local evidence does not prove the paid remote task
                # ended. Keep this admitted reservation until a terminal record
                # is observed; normal UI deletion rejects unresolved children.
                continue
            if rec is None:
                continue  # Clearing local run history is not remote completion.
            if _terminal(item, rec):
                _admitted.pop(key, None)
        live = set()
        # Existing individual submissions and another master's batch also occupy
        # capacity. A query timeout is not evidence that the remote task ended.
        live.update(_remote_keys(kind))
        live.update(key for key in _admitted if key[0] == kind and key not in queue)
        active = len(live)
        while queue and active < LIMITS[kind]:
            key = queue.pop(0);item = _admitted.get(key)
            if not item:
                continue
            pid = item['project_id'];sid = item['segment_id'];attempted = False
            try:
                with store._lock(pid):
                    data = store.load(pid);rec = _record(data, item)
                    if (not rec or rec.get('state') != 'pending_confirmation' or rec.get('batch_confirmation') != item['claim']
                            or _binding(data, kind, {**rec, '_segment_id': sid}) != item['_binding']):
                        raise ValueError(CHANGED)
                    valid = _image_valid(data, rec) if kind == 'images' else _video_valid(data, sid, rec)
                    if not valid:
                        raise ValueError(CHANGED)
                    attempted = True
                    if kind == 'images':
                        gateway.image_confirm(pid, item['image_id'])
                    else:
                        gateway.video_confirm(pid, sid, item['rev'], item['job_sha256'])
                active += 1
            except Exception as exc:
                message = user_message(exc);item['error'] = message
                try:
                    def mark_error(data):
                        current = _record(data, item)
                        if _same_record(item, current):
                            current['batch_error'] = message
                    store.update(pid, mark_error)
                    current = _record(store.load(pid), item)
                except FileNotFoundError:
                    current = None
                worker_key = 'img:' + item['image_id'] if kind == 'images' else f"gen:{sid}:{item['rev']}"
                rolled_back = (_same_record(item, current) and current.get('state') == 'pending_confirmation'
                               and not current.get('query_retry') and not current.get('remote_task_id')
                               and jobs.snapshot(pid).get(worker_key, {}).get('state') != 'running')
                if not attempted or _terminal(item, current) or rolled_back:
                    _admitted.pop(key, None)
                else:
                    # Once the confirmation call was entered, absent/replaced
                    # evidence cannot prove that a remote task was never made.
                    active += 1


def task_finished(pid, key):
    """Called only by an admitted worker's completion, never by a GET refresh."""
    if key.startswith(('img:', 'image-query:')):
        _pump('images')
        return
    if key != 'selection-finished' and not key.startswith(('gen:', 'query:', 'resume:')):
        return
    try:
        with store._lock(pid):
            data = store.load(pid)
            if not data.get('derived_from'):
                return
            changed = False
            for segment in (data.get('director') or {}).get('segments', []):
                runs = [r for r in data.get('runs', {}).get(segment['id'], []) if r.get('state') == 'completed' and _file(store.project_dir(pid), r.get('result'))]
                if not segment.get('selected_run') and len(runs) == 1:
                    segment['selected_run'] = runs[0]['rev'];changed = True
            if changed:
                selected = {s['id']: s.get('selected_run') for s in data['director']['segments']}
                store.update(pid, lambda d:[s.update(selected_run=selected[s['id']]) for s in d['director']['segments'] if selected.get(s['id']) and not s.get('selected_run')])
            status = final_preview.view(data, store.project_dir(pid))
            clips, chosen, warnings = final_preview.selections(data, store.project_dir(pid))
            signature = segment_plan.digest(chosen)
            marker = data.get('fission_final') or {}
            if clips and not warnings and (not status.get('file') or status.get('stale')) and marker.get('signature') != signature:
                def assemble():
                    with store._lock(pid):
                        current = store.load(pid)
                        _, selected_now, _ = final_preview.selections(current, store.project_dir(pid))
                        if segment_plan.digest(selected_now) != signature:
                            return  # A newer explicit selection superseded this queued cut.
                        try:
                            final_preview.generate(pid)
                        except Exception as exc:
                            message = user_message(exc)
                            store.update(pid, lambda d:d.setdefault('fission_final', {}).update(error=message))
                # Different selections have different jobs. The project lock in
                # generate serializes local work, including the runner handoff.
                try:
                    jobs.start(pid, 'fission-final:' + signature, assemble, '拼接裂变成片')
                except Exception as exc:
                    message = user_message(exc)
                    store.update(pid, lambda d:d.setdefault('fission_final', {}).update(error=message))
                else:
                    store.update(pid, lambda d:d.update(fission_final={'signature':signature, 'error':None}))
    except FileNotFoundError:
        pass
    finally:
        _pump('videos')
