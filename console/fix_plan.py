"""Review-guided plans and explicit, signature-bound repair application."""
from __future__ import annotations

import copy
import difflib
import json
import re
import uuid

from . import director, gateway, llm, segment_boards, segment_plan, segment_review, store
from .gateway.errors import CheckedLLMError

FIX_SYSTEM = '''你是短视频复刻的修改导演。只输出 JSON，为用户勾选的问题和补充意见提出可执行的修改方案。
遵守四级优先级：① 替换人物和商品准确、原片身份和旧商品不漏回、新商品外观和用法正确；② 剧情、商品状态和台词反应合理；③ 动作、机位、镜头顺序和节奏符合原片；④ 新衣服的细节、配饰和背景小物优先级最低，不为这些细节增加修改。
先核对当前新分镜和人物、商品照片。替换人物或新商品在某格新分镜里没画出来时，视频模型可能从参考视频补回原片内容；这类问题优先重画那一格，让新人物或新商品明确入镜。redraw 只包含本段确实需要重画的格，不重画无关的格。
why 用中文白话解释原因，request 写给单格重画的明确要求，保持其余人物、构图和动作。prompt 是改好的完整段落提示词，只改与问题有关的句子，其余尽量原样保留。使用输入中当前提交素材的图序号，不使用内部格号；保持生成方式、时长和段落结构。只使用已经确认的商品功能。
单格重画的 request 只描述这一格的一个瞬间，即原片截帧那一刻；同一人在一格里只出现一次，按原片截帧实际可见的人数画，不把同一人的前后动作画成两个分身。时段内之后才发生的动作不写进 request，交给视频去演。request 是独立完整的新画法，必须写清与问题有关的替换人物和商品，用素材标识指代照片，不依赖旧方案或推理输入的图号。
完整提示词遵循输入中的导演规则，不超过 4500 字。不要输出差异或付费确认，不执行任何生成。只输出：{"redraw":[{"cell":"本段格 ID","why":"为什么重画","request":"怎么重画"}],"prompt":"完整提示词","why":"一句话总原因"}。没有要重画的格时 redraw=[]。'''

STALE = '勾选、补充意见、新分镜或设定改过，请出修改方案。'


def _selection(run, body, *, allow_empty=False):
    indices, note = body.get('issues', []), body.get('note', '')
    issues = (run.get('review') or {}).get('issues') or []
    if (not isinstance(indices, list) or any(type(i) is not int or i < 0 or i >= len(issues) for i in indices)
            or not isinstance(note, str)):
        raise ValueError('勾选的问题或补充意见格式不正确')
    indices = sorted(set(indices)); note = note.strip()
    if not allow_empty and not indices and not note:
        raise ValueError('先勾选要改的问题，或填写补充意见')
    return indices, note


def _run(data, sid, rev):
    run = segment_review._run(data, sid, rev)
    if run.get('kind') != 'director' or run.get('state') != 'completed' or not run.get('result'):
        raise ValueError('请选择已经生成完成的视频版本')
    return run


def _review_sig(run):
    review = run.get('review') or {}
    return segment_plan.digest([run.get('result'), run.get('review_inputs'), run.get('preview'),
                               {k: review.get(k) for k in ('request_id', 'status', 'verdict', 'issues')}])


def _submitted(run):
    frozen = (run.get('review_inputs') or {}).get('segment') or {}
    tasks = (run.get('preview') or {}).get('tasks') or []
    prompt = frozen.get('prompt') or (tasks[0].get('prompt') if tasks else '')
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('这一版缺少提交时的提示词，请按当前设定生成新版后检查')
    return prompt, frozen.get('gen_images') or frozen.get('images') or [r.get('key') for r in run.get('references', [])]


def prompt_changes(before, after):
    def sentences(text):
        return [s.strip() for s in re.findall(r'[^。；！？\n]+[。；！？]?|[。；！？]', text) if s.strip()]
    old, new = sentences(before), sentences(after)
    result = []
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    for kind, a, b, c, d in matcher.get_opcodes():
        if kind != 'equal':
            result.append({'before': ''.join(old[a:b]), 'after': ''.join(new[c:d])})
    return result


def messages(data, sid, run, indices, note, directory):
    from . import review_request
    segment = director.get_segment(data, sid)
    request = director.messages(data, directory, target=segment)
    parts = request[1]['content']
    # Keep the shared story/neighbor context without its conflicting output schema.
    for part in parts:
        if part.get('type') == 'text' and '相邻段上下文：' in part['text']:
            part['text'] = '相邻段上下文：' + part['text'].split('相邻段上下文：', 1)[1]
    version = segment_boards.current(data, sid)
    for cid in segment['cells']:
        parts.append({'type': 'text', 'text': f'当前新分镜单格 {cid}'})
        parts.extend(llm.image_part(director._file(directory, version['cells'][cid])))
    for photo in director.material_options(data, directory, segment):
        parts.append({'type': 'text', 'text': '本段当前人物或商品照片：' + photo['name'] + ' ' + photo.get('description', '')})
        parts.extend(llm.image_part(director._file(directory, photo['file'])))
    prompt, images = _submitted(run)
    parts.append({'type': 'text', 'text': '用户勾选的问题：' + json.dumps(
        [(run.get('review') or {}).get('issues', [])[i] for i in indices], ensure_ascii=False)
        + '\n补充意见：' + note + '\n这一版提交时的 prompt 和 gen_images：'
        + json.dumps(dict(prompt=prompt, gen_images=images), ensure_ascii=False)})
    rules = '\n'.join(line for line in request[0]['content'].splitlines()
                      if not line.startswith(('输出：', 'check 写')))
    return review_request.enforce([{'role': 'system', 'content': rules + '\n本次只按以下修改方案格式输出：\n' + FIX_SYSTEM},
                                   {'role': 'user', 'content': parts}])


def validate(value, data, sid, directory):
    if (not isinstance(value, dict) or not isinstance(value.get('redraw'), list)
            or not isinstance(value.get('why'), str) or not value['why'].strip()):
        raise ValueError('修改方案格式不完整，请出修改方案')
    segment = director.get_segment(data, sid); redraw, seen = [], set()
    for row in value['redraw']:
        if not isinstance(row, dict):
            raise ValueError('重画要求格式不完整')
        cid = str(row.get('cell') or '').strip().lower()
        if cid not in segment['cells'] or cid in seen:
            raise ValueError('重画格号必须属于本段且不能重复')
        if any(not isinstance(row.get(k), str) or not row[k].strip() for k in ('why', 'request')):
            raise ValueError('重画原因和要求不能为空')
        redraw.append(dict(cell=cid, why=row['why'].strip(), request=row['request'].strip())); seen.add(cid)
    revised = director.validate_plan({'segments': [{**segment, 'prompt': value.get('prompt')}]},
                                    data, directory, target_id=sid)['segments'][0]
    if revised.get('needs_rewrite'):
        raise ValueError('修改方案提示词不完整：' + revised.get('rewrite_reason', '请出修改方案'))
    result = dict(redraw=redraw, prompt=revised['prompt'], why=value['why'].strip())
    if revised.get('speech_warning'):
        result['speech_warning'] = revised['speech_warning']
    return result


def _billing(data, sid):
    # Same duration/model rule as jimmy_bridge.director_preview; no preview or API.
    from . import jimmy_bridge
    row = director.get_segment(data, sid)
    route = gateway.routes.resolve('video', data)
    seconds = row['duration']
    return seconds + (seconds if row['mode'] == 'video' and route['model'] == jimmy_bridge.MINI_VIDEO else 0)


def generate(pid, sid, rev, body):
    directory = store.project_dir(pid)
    with store._lock(pid):
        data = store.load(pid); run = _run(data, sid, rev)
        director.require_ready(data, directory, sid)
        indices, note = _selection(run, body)
        if (run.get('review') or {}).get('status') == 'running':
            raise ValueError('检查进行中，请完成后出修改方案')
        token = uuid.uuid4().hex
        frozen = {'issues': indices, 'note': note, 'request_id': token}
        def begin(current):
            target = _run(current, sid, rev)
            target.pop('fix_plan', None)
            target['fix_plan_input'] = frozen
        store.update(pid, begin)
        signature = director.segment_inputs_sig(data, directory, sid)
        authored = director.segment_sig(director.get_segment(data, sid))
        review_sig = _review_sig(run)
        result_sha = store.sha256_file(segment_review._file(directory, run['result']))
    request = messages(data, sid, run, indices, note, directory)
    value, _ = gateway.reason_checked('director', request, project=pid, max_tokens=8000)
    checked = validate(value, data, sid, directory)
    if checked.get('speech_warning'):
        retry = request + [dict(role='assistant', content=json.dumps(value, ensure_ascii=False)),
                           dict(role='user', content='请重写修改方案：'+checked['speech_warning']+' 返回完整修改方案 JSON。')]
        from . import review_request
        try:
            second, _ = gateway.reason_checked('director', review_request.enforce(retry), project=pid, max_tokens=8000)
            checked = validate(second, data, sid, directory)
        except (ValueError, CheckedLLMError, OSError):
            store.log(pid, '修改方案的台词自动重写没完成，已保留这一版。', 'warn')
    base_prompt, _ = _submitted(run)
    board = segment_boards.current(data, sid)['id']
    plan = dict(**checked, id=token, issues=indices, note=note, base_prompt=base_prompt, base_board=board,
                expected_board=board, expected_sig=signature, author_sig=authored, review_sig=review_sig, result_sha256=result_sha,
                created_at=segment_review._now(), status='ready', completed_cells=[],
                prompt_changes=prompt_changes(base_prompt, checked['prompt']), video_seconds=_billing(data, sid))
    def commit(current):
        target = _run(current, sid, rev)
        if (target.get('fix_plan_input') != frozen or _review_sig(target) != review_sig
                or store.sha256_file(segment_review._file(directory, target['result'])) != result_sha
                or director.segment_inputs_sig(current, directory, sid) != signature
                or director.segment_sig(director.get_segment(current, sid)) != authored):
            raise ValueError(STALE)
        target['fix_plan'] = plan
    return store.update(pid, commit)


def _matches(data, sid, run, plan, directory):
    try:
        selected = run.get('fix_plan_input') or {}
        return (selected.get('issues') == plan['issues'] and selected.get('note') == plan['note']
                and selected.get('request_id') == plan['id'] and _review_sig(run) == plan['review_sig']
                and store.sha256_file(segment_review._file(directory, run['result'])) == plan['result_sha256']
                and director.segment_inputs_sig(data, directory, sid) == plan['expected_sig']
                and director.segment_sig(director.get_segment(data, sid)) == plan['author_sig'])
    except (KeyError, ValueError, OSError):
        return False


def started_video(data, sid, plan):
    """Read legacy bindings without consuming a merely prepared preview."""
    if not plan.get('id'):
        return None
    for run in (data.get('runs') or {}).get(sid, []):
        if ((run.get('fix_plan_binding') or {}).get('id') == plan['id']
                and run.get('state') in ('submitting', 'running', 'query_pending', 'completed', 'failed', 'unknown')):
            return run['rev']
    return plan.get('video_rev') if plan.get('status') == 'video_started' else None


def view(data, sid, run, directory):
    plan = copy.deepcopy(run.get('fix_plan') or {})
    if not plan:
        return plan
    video_rev = started_video(data, sid, plan)
    if video_rev or plan.get('status') == 'video_started':
        plan.update(status='video_started', video_rev=video_rev)
        return plan
    if plan.get('status') == 'cancelled':
        return plan
    if not _matches(data, sid, run, plan, directory):
        plan.update(status='stale', error_reason=STALE)
        return plan
    image = (data.get('images') or {}).get(plan.get('active_image'), {})
    if image.get('state') in ('failed', 'error', 'cancelled'):
        plan.update(status='failed', error_reason='这一镜没画完，可以重试或取消；已画好的镜头会保留。')
    return plan


def _current(data, sid, rev, body, directory):
    run = _run(data, sid, rev); plan = run.get('fix_plan') or {}
    if not body.get('plan_id') or body['plan_id'] != plan.get('id') or plan.get('status') == 'cancelled':
        raise ValueError('修改方案已经作废，请出修改方案')
    if started_video(data, sid, plan) or plan.get('status') == 'video_started':
        raise ValueError('这个方案已经生成过视频，请查看新版本，或重新出方案')
    if not _matches(data, sid, run, plan, directory):
        raise ValueError(STALE)
    return run, plan


def update(pid, sid, rev, body):
    def commit(data):
        run = _run(data, sid, rev)
        if body.get('action') == 'cancel':
            plan = run.get('fix_plan') or {}
            if not body.get('plan_id') or body['plan_id'] != plan.get('id'):
                raise ValueError('修改方案已经改变，请刷新页面')
            if started_video(data, sid, plan) or plan.get('status') == 'video_started':
                raise ValueError('这个方案已经生成过视频，请查看新版本，或重新出方案')
            plan.update(status='cancelled', cancelled_at=segment_review._now())
            return
        indices, note = _selection(run, body, allow_empty=True)
        old = run.get('fix_plan_input') or {}
        if body.get('action') == 'invalidate' or (indices, note) != (old.get('issues'), old.get('note')):
            run.pop('fix_plan', None)
            run['fix_plan_input'] = dict(issues=indices, note=note, request_id=uuid.uuid4().hex)
    return store.update(pid, commit)


def apply(pid, sid, rev, body):
    directory = store.project_dir(pid)
    def commit(data):
        _, plan = _current(data, sid, rev, body, directory)
        if plan.get('status') != 'ready':
            return
        row = director.get_segment(data, sid)
        revised = director.validate_plan({'segments': [{**row, 'prompt': plan['prompt']}]},
                                        data, directory, target_id=sid)['segments'][0]
        if row.get('selected_run'):
            revised['selected_run'] = row['selected_run']
        row.clear()
        row.update(revised)
        row.update(prompt=plan['prompt'], prompt_source='fix_plan', fix_plan_id=plan['id'], edited=True)
        director.refresh_prompt_status(row, data)
        data['director']['inputs_sig'] = director.inputs_sig(data, directory)
        plan.update(status='redrawing' if plan['redraw'] else 'ready_video', applied_at=segment_review._now(),
                    author_sig=director.segment_sig(row))
    return store.update(pid, commit)


def preview_context(data, sid, body, directory):
    marker = body.get('fix_plan')
    if not marker:
        return None
    if not isinstance(marker, dict):
        raise ValueError('修改方案标记格式不正确')
    _, plan = _current(data, sid, marker.get('rev'), {'plan_id': marker.get('id')}, directory)
    remaining = [r for r in plan['redraw'] if r['cell'] not in plan['completed_cells']]
    if plan.get('status') != 'redrawing' or not remaining:
        raise ValueError('先按方案修改，再逐镜重画')
    active = (data.get('images') or {}).get(plan.get('active_image'), {})
    if active.get('state') in ('running', 'query_pending', 'submitting'):
        raise ValueError('上一镜还在生成，请等它完成')
    expected = remaining[0]
    if body.get('cell_id') != expected['cell'] or str(body.get('request') or '').strip() != expected['request']:
        raise ValueError('请按方案顺序和要求重画，已画好的镜头无需重复')
    return {**marker, 'expected_sig': plan['expected_sig']}


def register_preview(data, sid, marker, image_id, cid):
    plan = _run(data, sid, marker['rev'])['fix_plan']
    plan.update(active_image=image_id, active_cell=cid, error_reason=None)


def video_binding(data, sid, directory, marker=None):
    """Bind a new paid preview only while it belongs to an active applied plan."""
    row = director.get_segment(data, sid)
    if marker is not None:
        if not isinstance(marker, dict):
            raise ValueError('修改方案标记格式不正确')
        _, requested = _current(data, sid, marker.get('rev'), {'plan_id': marker.get('id')}, directory)
        if (requested.get('status') != 'ready_video' or row.get('prompt_source') != 'fix_plan'
                or row.get('fix_plan_id') != requested['id']):
            raise ValueError('修改方案已经改变，请重新出方案')
        return {'id': requested['id'], 'rev': marker['rev'], 'sid': sid}
    if row.get('prompt_source') != 'fix_plan':
        return None
    for run in (data.get('runs') or {}).get(sid, []):
        plan = run.get('fix_plan') or {}
        if plan.get('id') != row.get('fix_plan_id') or plan.get('status') == 'cancelled':
            continue
        if started_video(data, sid, plan) or plan.get('status') == 'video_started':
            return None
        if not _matches(data, sid, run, plan, directory):
            continue
        if plan.get('status') != 'ready_video':
            raise ValueError('先完成修改方案里的分镜重画，再生成视频预览')
        return {'id': plan['id'], 'rev': run['rev'], 'sid': sid}
    # Invalidated/cancelled plans keep authored text for a later explicit manual
    # preview. Their older paid confirmations remain bound and are rejected below.
    return None


def verify_video_binding(data, sid, binding, directory):
    if not isinstance(binding, dict) or binding.get('sid') != sid:
        raise ValueError('修改方案预览已经失效，请重新生成视频预览')
    _, plan = _current(data, sid, binding.get('rev'), {'plan_id': binding.get('id')}, directory)
    if plan.get('status') != 'ready_video' or video_binding(data, sid, directory) != binding:
        raise ValueError('修改方案已经改变，请重新生成视频预览')


def mark_video_started(data, sid, binding, video_rev, directory):
    """Consume the plan in the same locked write as its confirmed video."""
    verify_video_binding(data, sid, binding, directory)
    plan = _run(data, sid, binding['rev'])['fix_plan']
    plan.update(status='video_started', video_rev=video_rev, video_started_at=segment_review._now())


def complete(before, current, sid, record, directory):
    """Only the matching plan's own completed cell may refresh prompt bindings."""
    context = record.get('storyboard_context') or {}; marker = context.get('fix_plan')
    if not marker:
        return
    try:
        run = _run(before, sid, marker['rev']); old = run.get('fix_plan') or {}
        if (old.get('id') != marker.get('id') or old.get('status') != 'redrawing'
                or old.get('active_image') != record['id'] or old.get('expected_sig') != marker.get('expected_sig')
                or not _matches(before, sid, run, old, directory)):
            return
        row = director.get_segment(current, sid); plan = _run(current, sid, marker['rev'])['fix_plan']
        cid = context['cell_id']; signature = director.segment_inputs_sig(current, directory, sid)
        row['inputs_sig'] = signature
        current['director']['inputs_sig'] = director.inputs_sig(current, directory)
        plan.update(expected_sig=signature, expected_board=segment_boards.current(current, sid)['id'],
                    author_sig=director.segment_sig(row), completed_cells=[*plan['completed_cells'], cid],
                    active_cell=None, active_image=None, error_reason=None)
        if len(plan['completed_cells']) == len(plan['redraw']):
            plan.update(status='ready_video', redrawn_at=segment_review._now())
    except (ValueError, KeyError, OSError):
        # A late result is still a usable image; never revive a cancelled/stale plan.
        return
