"""Local V2 preview assembly from explicitly selected completed segment results."""
import copy
import json
import math
import re
import uuid
from datetime import datetime

from . import director, gateway, jobs, llm, media, segment_plan, segment_review, store


FINAL_SYSTEM = '''你是短视频整片检查员。只依据整片采样帧、接缝密集采样、各段剧情说明和人物商品参考照片，检查拼接以后才出现的问题。
只查：相邻段之间人物身份、衣服和商品是否一致；接缝是否突兀或商品状态前后对不上；全片剧情是否能连起来。每段内部的问题不报，不根据看不见或听不到的内容推测。先对照参考照片，明确保留或剧情合理变化的不算错误。
遵守四级优先级：① 人物和商品身份及正确用法；② 剧情和状态逻辑；③ 接缝动作、机位和节奏；④ 服饰细节、配饰、背景小物权重最低，不为其报问题。影响身份或剧情的问题标 high，轻微可接受的接缝节奏标 low。
问题 start、end 使用整片秒数，segment 必须是输入中需要处理的段 ID，what 用中文白话说明接缝哪里不连贯。无问题 verdict=pass、issues=[]；有问题 verdict=fix。只输出 JSON：{"verdict":"pass|fix","summary":"一句话说明","issues":[{"start":0,"end":1,"level":"high|low","segment":"段 ID","what":"问题说明"}]}。'''


def selections(data, directory):
    rows = (data.get('segments_plan') or {}).get('segments') or []
    prompts = {s['id']: s for s in (data.get('director') or {}).get('segments', [])}
    clips, chosen, warnings = [], [], []
    for index, segment in enumerate(rows, 1):
        sid = segment['id']
        prompt = prompts.get(sid, {})
        bound = (not director.is_legacy(data.get('director'))
                 and all(prompt.get(k) == segment.get(k) for k in ('cells', 'source_start', 'source_end')))
        rev = prompt.get('selected_run') if bound else None
        run = next((r for r in data.get('runs', {}).get(sid, [])
                    if rev and r.get('rev') == rev and r.get('state') == 'completed' and r.get('result')), None)
        if run and any(k in run and run[k] != segment.get(k) for k in ('source_start', 'source_end')):
            run = None
        path = (directory / run['result']).resolve() if run else None
        if path is None or not path.is_relative_to(directory.resolve()) or not path.is_file():
            warnings.append(f'第 {index} 段还没有选定的结果，回第 04 步（对照和修改），选中这一段，在“视频”里生成或选定一版。')
        else:
            clips.append(path)
            chosen.append({'id': sid, 'rev': rev})
    if not rows:
        warnings.append('先在第 04 步（对照和修改）生成分段方案。')
    return clips, chosen, warnings


def view(data, directory):
    clips, chosen, warnings = selections(data, directory)
    saved = data.get('final_preview') or {}
    return dict(**saved, ready=bool(clips) and not warnings, warnings=warnings,
                review_warnings=review_warnings(data, chosen),
                stale=bool(saved and (warnings or saved.get('segments') != chosen)))


def review_warnings(data, chosen):
    selected = {r['id']: r['rev'] for r in chosen}
    warnings = []
    for index, segment in enumerate((data.get('segments_plan') or {}).get('segments', []), 1):
        sid = segment['id']
        run = next((r for r in data.get('runs', {}).get(sid, []) if r.get('rev') == selected.get(sid)), {})
        review = run.get('review') or {}
        issues = sorted(review.get('issues', []), key=lambda issue: (float(issue.get('start', 0)), float(issue.get('end', 0))))
        high = [re.sub(r'[\s。；！？.!?;，,]+$', '', issue['what'])
                for issue in issues if issue.get('level') == 'high']
        if review.get('verdict') == 'fix' and high and not review.get('accepted'):
            warnings.append(f'第 {index} 段选用的版本检查有问题：' + '；'.join(high) +
                            '，回第 04 步（对照和修改），选中这一段，在“视频”里处理，或确认接受后再拼接。')
    return warnings


def generate(pid):
    with store._lock(pid):
        data = store.load(pid)
        directory = store.project_dir(pid)
        clips, chosen, warnings = selections(data, directory)
        if warnings:
            raise ValueError(warnings[0])
        first = media.probe(clips[0])
        output = directory / 'final' / f'preview-{datetime.now():%Y%m%d-%H%M%S-%f}.mp4'
        # No source-duration trimming, subtitles, music, gain or speech treatment.
        media.concat(clips, output, first.get('width') or 720, first.get('height') or 1280,
                     fps=first.get('fps') or 24, target_seconds=None)
        result = {'file': store.rel(pid, output), 'duration': media.probe(output)['duration'],
                  'segments': chosen, 'timeline': timeline(data, directory, chosen),
                  'generated_at': datetime.now().astimezone().isoformat()}
        return store.update(pid, lambda current: current.update(final_preview=result))


def timeline(data, directory, chosen):
    rows, start = [], 0
    for selection in chosen:
        sid, rev = selection['id'], selection['rev']
        run = segment_review._run(data, sid, rev)
        clip = segment_review._file(directory, run.get('result'))
        duration = segment_review._number(media.probe(clip).get('duration'))
        if duration <= 0:
            raise ValueError('段落视频时长无法读取')
        frozen = (run.get('review_inputs') or {}).get('segment') or {}
        authored = next((r for r in (data.get('director') or {}).get('segments', []) if r['id'] == sid), {})
        rows.append(dict(id=sid, rev=rev, start=start, end=start + duration, what=frozen.get('what') or authored.get('what', '')))
        start += duration
    return rows


def check_messages(data, saved, directory, clip, folder):
    from . import review_request
    info = media.probe(clip)
    duration = segment_review._number(info.get('duration'))
    if duration <= 0:
        raise ValueError('整片时长无法读取')
    rows = copy.deepcopy(saved.get('timeline')) or timeline(data, directory, saved.get('segments') or [])
    if not rows:
        raise ValueError('整片缺少段落信息，请重新拼接预览')
    visual = min(duration, info.get('video_duration') or duration)
    times = {float(n) for n in range(math.ceil(visual))}
    for row in rows[1:]:
        seam = row['start']
        times.update(round(seam + offset / 4, 6) for offset in range(-6, 7) if 0 <= seam + offset / 4 < visual)
    if len(times) > review_request.MAX_FRAMES:
        times.add(review_request.last_frame(visual, info.get('fps')))
    parts = []
    seen = set()
    for selection in rows:
        try:
            planned = segment_plan.get_segment(data, selection['id'])
        except ValueError:
            continue
        for photo in director.material_options(data, directory, planned):
            key = (photo['key'], photo['file'])
            if key in seen:
                continue
            seen.add(key)
            parts.append(dict(type='text', text='人物或商品参考：' + photo['name'] + ' ' + photo.get('description', '')))
            parts.extend(llm.image_part(director._file(directory, photo['file'])))
    frames = review_request.Frames(clip, folder, prefix='整片 ')
    def build(selected, width):
        sampling = (f'全片与接缝合计采样 {len(selected)} 帧，帧标签为真实秒数；只查跨段问题。' if len(selected) != len(times) else
                    '全片每 1 秒一帧，接缝前后各 1.5 秒按每 0.25 秒采样；只查跨段问题。')
        content = [dict(type='text', text='各段在整片里的起止秒数和 what：' + json.dumps(rows, ensure_ascii=False) + '\n' + sampling)]
        content += frames.parts(selected, width) + parts
        return [dict(role='system', content=FINAL_SYSTEM), dict(role='user', content=content)]
    return review_request.bounded(build, times), duration, [row['id'] for row in rows]


def validate_check(value, duration, segments):
    if (not isinstance(value, dict) or value.get('verdict') not in ('pass', 'fix')
            or not isinstance(value.get('summary'), str) or not value['summary'].strip()
            or not isinstance(value.get('issues'), list)):
        raise ValueError('整片检查格式不完整')
    issues, dropped = [], 0
    for item in value['issues']:
        try:
            issue = segment_review.normalize_issue(item, duration)
            sid = str(item.get('segment') or '').strip()
            if sid not in segments:
                raise ValueError('问题段落不属于整片')
            issues.append({**issue, 'segment': sid})
        except (ValueError, TypeError):
            dropped += 1
    if value['verdict'] == 'fix' and not issues:
        raise ValueError('整片没有可用的问题记录')
    return dict(verdict='fix' if issues else 'pass', summary=value['summary'].strip(), issues=issues, dropped_issues=dropped)


def check(pid, body=None):
    with store._lock(pid):
        data = store.load(pid); directory = store.project_dir(pid)
        saved = copy.deepcopy(data.get('final_preview') or {})
        if body and body.get('file', saved.get('file')) != saved.get('file'):
            raise ValueError('整片预览已经改变，请检查当前视频')
        clip = segment_review._file(directory, saved.get('file'))
        signature = store.sha256_file(clip)
        if jobs.snapshot(pid).get('final-check', {}).get('state') == 'running':
            return data
        token = uuid.uuid4().hex
        initial = dict(status='running', request_id=token, file=saved['file'], file_sha256=signature, started_at=segment_review._now())
        accepted = (saved.get('check') or {}).get('accepted')
        if accepted:
            initial['accepted'] = copy.deepcopy(accepted)
        store.update(pid, lambda d: d['final_preview'].update(check=initial))

        def save(report):
            def commit(current):
                preview = current.get('final_preview') or {}
                if (preview.get('file') == saved['file'] and (preview.get('check') or {}).get('request_id') == token
                        and clip.is_file() and store.sha256_file(clip) == signature):
                    result = {**initial, **report, 'generated_at': segment_review._now()}
                    if report.get('status') == 'failed' and preview['check'].get('accepted'):
                        result['accepted'] = copy.deepcopy(preview['check']['accepted'])
                    else:
                        result.pop('accepted', None)
                    preview['check'] = result
            store.update(pid, commit)

        def work():
            request = None
            try:
                folder = directory / 'analysis/final-checks' / token
                folder.mkdir(parents=True, exist_ok=True)
                request, duration, segments = check_messages(data, saved, directory, clip, folder)
                value, _ = gateway.reason_checked('director', request, project=pid, max_tokens=8000)
                report = {**validate_check(value, duration, segments), 'status': 'completed', 'duration': duration,
                          'inputs_sig': segment_plan.digest([signature, request]), 'error': None}
            except Exception as exc:
                report = segment_review.failure_report(exc, request)
            try:
                save(report)
            except store.ProjectDeletedError:
                pass
        try:
            jobs.start(pid, 'final-check', work, '整片检查视频')
        except Exception as exc:
            save(segment_review.failure_report(exc))
        return store.load(pid)


def accept_check(pid, body):
    if type(body.get('accepted')) is not bool:
        raise ValueError('请选择接受或撤销')
    directory = store.project_dir(pid)
    def commit(data):
        saved = data.get('final_preview') or {}; report = saved.get('check') or {}
        if (body['accepted'] and report.get('status') != 'completed' or report.get('file') != saved.get('file')
                or body.get('file', saved.get('file')) != saved.get('file')
                or report.get('file_sha256') != store.sha256_file(segment_review._file(directory, saved.get('file')))):
            raise ValueError('整片预览或检查结果已经改变，请检查当前视频')
        if body['accepted']:
            report['accepted'] = {'at': segment_review._now()}
        else:
            report.pop('accepted', None)
    return store.update(pid, commit)
