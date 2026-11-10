"""Explicit, reasoning-only checks of a completed director version."""
from __future__ import annotations

import copy
import json
import math
import shutil
import uuid
from datetime import datetime

from . import asr, config, director, gateway, jobs, llm, media, review_request, segment_plan, store
from .gateway.errors import classify_error, diagnostic_detail

SYSTEM = '''你是短视频复刻的生成后验收员。只依据本版结果帧、参考素材、生成要求和可用的逐词时间判断，不猜测看不到或听不到的内容。结果帧前的 t=X.Xs 是结果视频的秒数，问题区间必须在结果时长内，start 和 end 必须填写数字秒数。
遵守四级优先级：① 人和商品换干净：原片人物的脸、发型、衣服和发饰配饰，旧商品、旧包装和字幕都不出现，新商品的外观和用法正确；② 剧情逻辑通顺，商品状态、台词和反应合理；③ 动作、机位、镜头顺序和节奏对上原片；④ 新衣服的款式细节、配饰、背景小物权重最低，不报这些细节。
level=high 只用于优先级①②的问题，以及让观众看不懂剧情的③问题；轻微的节奏、切点早晚这类③问题标 low；④不报。
判断某个人是谁时，先对照每个人物的参考照和新画面再下结论。
依次检查：
1. 被要求替换的原片人物的脸、发型、衣服和发饰配饰、旧商品、旧包装，以及字幕或文字有没有漏出，尤其检查镜头末尾；明确保留的身份、衣服或商品不算漏出。
2. 新商品外观、用法、状态是否符合已确认功能和剧情，人物身份是否稳定，多人是否混脸；不推测未确认功能，不要求界面出现具体文字。
3. 动作、镜头顺序、台词时机是否合理；有逐词时间时结合画面判断，没有时标明未转录，不臆测实际台词或给出已核实音轨的结论。
4. 穿模、多手、断肢、接触关系等物理问题。
只报有证据且影响观看或复刻目标的问题。level=high 表示需要处理，low 表示轻微可接受。what 用中文白话说明哪一秒发生什么、影响什么；how 说明点哪里怎么修。fix 只能是 rerun（第 04 步选中这一段，在“视频”里重新生成）、image_mode（第 04 步重新生成分段方案；旧商品从参考视频漏回时优先建议）、redraw_cell（第 04 步选中这一段，点这一镜重画）或 accept（问题轻微，可以接受）。redraw_cell 的 cell 必须是输入中的格 ID；其余可填空。
通过时 verdict=pass、issues=[]；发现问题时 verdict=fix 并逐条给出问题。summary 是中文一句话，未转录时明确验收范围只包含画面及可见时机。只输出 JSON：{"verdict":"pass|fix","summary":"中文一句话","issues":[{"start":"<start_seconds>","end":"<end_seconds>","level":"high|low","what":"中文白话：哪一秒出了什么问题、影响什么","fix":"rerun|image_mode|redraw_cell|accept","cell":"cXX 或空","how":"中文白话：点哪里怎么修"}]}'''

FAILURE = '这次检查没完成（原因：服务暂时不可用），视频本身没受影响。点“检查视频”再查一次。'


def _now():
    return datetime.now().astimezone().isoformat()


def _run(data, sid, rev):
    run = next((r for r in data.get('runs', {}).get(sid, []) if r.get('rev') == rev), None)
    if run is None:
        raise ValueError('找不到这版结果，请刷新第 04 步（对照和修改）的“视频”。')
    return run


def _file(directory, file):
    path = (directory / str(file or '')).resolve()
    if not path.is_relative_to(directory.resolve()) or not path.is_file():
        raise ValueError('本版验收素材缺失，请在第 04 步（对照和修改）的“视频”检查结果和参考素材。')
    return path


def freeze_inputs(data, segment, references, directory, package):
    """Freeze authoring evidence with the generation package, before submission."""
    cells = {c['id']: c for c in (data.get('storyboard') or {}).get('cells', [])}
    source_cells = []
    folder = package / 'review-source'
    folder.mkdir(parents=True, exist_ok=True)
    for index, cid in enumerate(segment['cells']):
        cell = cells[cid]
        source = _file(directory, cell.get('file'))
        dest = folder / f'cell-{index + 1:02d}{source.suffix.lower()}'
        shutil.copyfile(source, dest)
        source_cells.append({k: copy.deepcopy(cell[k]) for k in ('id', 'start', 'end') if k in cell} |
                            dict(file=dest.relative_to(directory).as_posix(), sha256=store.sha256_file(dest)))
    refs = copy.deepcopy(references)
    for ref in refs:
        ref['sha256'] = store.sha256_file(_file(directory, ref['file']))
    product = (data.get('replacements') or {}).get('product') or {}
    frozen = dict(segment=copy.deepcopy(segment), references=refs, source_cells=source_cells,
                  product={k: copy.deepcopy(product[k]) for k in ('action', 'description', 'look', 'story_checks') if k in product})
    lines, _ = director.speech_context(data, segment, directory)
    if lines or not (data.get('breakdown') or {}).get('dialogue'):
        frozen['speech_lines'] = lines
    frozen['speech_source'] = _speech_source(data, directory)
    return frozen


def _speech_source(data, directory):
    source = data.get('source') or {}
    return dict(source=copy.deepcopy(source), sha256=director._hash(directory, source.get('file')))


def _can_add_speech(data, sid, context, directory):
    """Old evidence may borrow source speech only while its source still matches."""
    if context.get('speech_source'):
        return context['speech_source'] == _speech_source(data, directory)
    from . import storyboard
    legacy = copy.deepcopy(data)
    for key in ('speech_lines', 'speech_lines_state'):
        (legacy.get('breakdown') or {}).pop(key, None)
    try:
        return (not storyboard.status(legacy, directory)['stale']
                and bool(context['segment'].get('inputs_sig'))
                and context['segment']['inputs_sig'] == director.segment_inputs_sig(legacy, directory, sid))
    except (ValueError, KeyError, OSError):
        return False


def _inputs(data, sid, run, directory, folder):
    if run.get('review_inputs'):
        frozen = copy.deepcopy(run['review_inputs'])
        # Explicit checks of pre-V7 runs may use newly reconstructed immutable
        # source speech; never replace their submitted prompt or visual evidence.
        if 'speech_lines' not in frozen and _can_add_speech(data, sid, frozen, directory):
            lines, _ = director.speech_context(data, frozen['segment'], directory)
            if lines:
                frozen['speech_lines'] = lines
        return frozen
    # Older completed versions can be checked only while their authoring evidence
    # still matches. Never silently compare an old result with today's new plan.
    segment = director.get_segment(data, sid)
    if (not run.get('director_sig') or director.segment_sig(segment) != run['director_sig']
            or segment.get('inputs_sig') != director.segment_inputs_sig(data, directory, sid)
            or not run.get('references')):
        raise ValueError('旧版本缺少对应的验收素材；回第 04 步（对照和修改）的“视频”查看本版视频，或按当前方案生成新版后验收。')
    return freeze_inputs(data, segment, run['references'], directory, folder)


def _transcript(clip, folder, info):
    try:
        if not info.get('audio_stream_count') or not asr.available():
            return '未转录'
        cfg = config.load()
        wav = media.extract_audio_wav(clip, folder / 'audio.wav')
        result = asr.transcribe_best(wav, cfg.get('ASR_MODEL', 'medium'), cfg.get('ASR_COMPUTE_TYPE', 'int8'),
                                     cfg.get('ASR_LANGUAGE') or None, cfg.get('ASR_ENGINE', 'auto'), local_only=True)
        words = [w for row in result.get('segments', []) for w in row.get('words', [])]
        if not words:
            return '未转录：没有可用的逐词时间'
        store.atomic_write_json(folder / 'transcript.json', result)
        return '结果音轨逐词时间：' + json.dumps(words, ensure_ascii=False)
    except Exception:
        # Optional local models must not cause downloads or fail visual review.
        return '未转录：本地转录不可用'


def messages(context, directory, clip, folder):
    info = media.probe(clip)
    duration = info.get('duration')
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise ValueError('结果视频时长无法读取，请在第 04 步（对照和修改）的“视频”检查视频后重试。')
    segment = context['segment']
    parts = []
    visual_duration = min(duration, info.get('video_duration') or duration)
    if visual_duration < duration:
        parts.append(dict(type='text', text=f'画面时长：{visual_duration:.3f}s；其后的音轨尾部没有画面，不推测不存在的帧。'))
    visual_note = parts[:]
    parts = []
    refs = context.get('references') or []
    if not refs or not context.get('source_cells'):
        raise ValueError('本版提交图片或原片单格素材缺失，请在第 04 步（对照和修改）的“视频”检查参考素材。')
    for ref in refs + context['source_cells']:
        path = _file(directory, ref.get('file'))
        if ref.get('sha256') != store.sha256_file(path):
            raise ValueError('本版参考素材已改变，请在第 04 步（对照和修改）的“视频”检查本版视频，或重新生成新版后验收。')
        key = ref.get('key')
        label = ((str(ref.get('tag') or '') + ' ' + str(ref.get('description') or ref.get('label') or key)).strip()
                 if key else f'原片单格 {ref["id"]}')
        parts.append(dict(type='text', text=label))
        parts.extend(llm.image_part(path))
    parts.append(dict(type='text', text='本版生成要求：' + json.dumps({k: segment.get(k) for k in
        ('cells', 'what', 'check', 'prompt', 'mode', 'source_start', 'source_end', 'stretch')}, ensure_ascii=False)
        + '\n原片格子时间（原片时钟，结果时间以生成要求为准）：' + json.dumps(
            [{k: c.get(k) for k in ('id', 'start', 'end')} for c in context['source_cells']], ensure_ascii=False)
        + '\n商品已确认信息：' + json.dumps(context.get('product') or {}, ensure_ascii=False)))
    if context.get('speech_lines'):
        parts.append(dict(type='text', text='本段理顺后的台词（成片时钟，秒）：'+json.dumps(context['speech_lines'], ensure_ascii=False)))
    parts.append(dict(type='text', text=_transcript(clip, folder, info)))
    times = review_request.segment_times(visual_duration, info.get('fps'))
    frames = review_request.Frames(clip, folder)
    def build(selected, width):
        sampling = ('以下按每 0.5 秒采样。' if visual_duration <= 10 and selected == times else
                    f'以下在全长上采样 {len(selected)} 帧，帧标签为真实秒数。')
        content = [dict(type='text', text=f'结果视频时长：{duration:.3f}s。{sampling}')]
        content += visual_note + frames.parts(selected, width) + parts
        return [dict(role='system', content=SYSTEM), dict(role='user', content=content)]
    return review_request.bounded(build, times), duration


def _number(value):
    if isinstance(value, bool):
        raise ValueError('问题时间无法识别')
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError('问题时间无法识别') from None
    if not math.isfinite(result):
        raise ValueError('问题时间无法识别')
    return result


def normalize_issue(item, duration):
    if not isinstance(item, dict):
        raise ValueError('问题格式不正确')
    a, b = sorted((_number(item.get('start')), _number(item.get('end'))))
    if duration < b <= duration + .5:
        b = duration
    if not 0 <= a <= b <= duration:
        raise ValueError('问题时间超出结果范围')
    level = str(item.get('level') or '').strip().lower()
    if level not in ('high', 'low') or not isinstance(item.get('what'), str) or not item['what'].strip():
        raise ValueError('问题缺少说明或级别')
    return dict(start=a, end=b, level=level, what=item['what'].strip())


def validate(value, duration, cells):
    if not isinstance(value, dict) or value.get('verdict') not in ('pass', 'fix'):
        raise ValueError('检查结论格式不完整')
    if not isinstance(value.get('summary'), str) or not value['summary'].strip() or not isinstance(value.get('issues'), list):
        raise ValueError('检查说明或问题列表缺失')
    issues, dropped = [], 0
    for item in value['issues']:
        try:
            normalized = normalize_issue(item, duration)
            fix = str(item.get('fix') or '').strip().lower()
            cell = str(item.get('cell') or '').strip().lower()
            if fix not in ('rerun', 'image_mode', 'redraw_cell', 'accept'):
                raise ValueError('修复方式不正确')
            if cell and cell not in cells or fix == 'redraw_cell' and not cell:
                raise ValueError('格号不属于本段')
            # V6 renders the modification plan instead of the old how text.
            how = item.get('how') if isinstance(item.get('how'), str) else ''
            issues.append({**normalized, 'fix': fix, 'cell': cell, 'how': how.strip()})
        except (ValueError, TypeError):
            dropped += 1
    if value['verdict'] == 'fix' and not issues:
        raise ValueError('没有可用的检查问题')
    return dict(verdict='fix' if issues else 'pass', summary=value['summary'].strip(), issues=issues, dropped_issues=dropped)


def error_details(exc):
    """Closed user-facing labels; provider payloads never enter saved errors."""
    kind = classify_error(exc)
    if isinstance(exc, TimeoutError) or any(word in str(exc).lower() for word in ('timeout', 'timed out', '超时')):
        return 'transient', '网络超时'
    return kind, {'transient': '服务暂时不可用', 'invalid_request': '模型返回格式无法识别',
                  'empty': '模型返回格式无法识别', 'reasoning_exhausted': '模型没有返回完整结果',
                  'auth': '模型服务认证失败', 'quota': '模型服务额度不足', 'too_large': '请求太大'}.get(kind, '服务暂时不可用')


def failure_report(exc, request=None):
    kind, reason = error_details(exc)
    return dict(status='failed', error_kind=kind, error_reason=reason,
                error_detail=diagnostic_detail(exc, **(review_request.metrics(request) if request else {})),
                error=f'这次检查没完成（原因：{reason}），视频本身没受影响。点“检查视频”再查一次。')


def reason(request, *, pid, max_tokens=8000, on_response=None):
    for attempt in range(2):
        try:
            return gateway.reason_checked('director', request, project=pid, max_tokens=max_tokens, on_response=on_response)
        except Exception as exc:
            if attempt or error_details(exc)[0] != 'transient':
                raise


def accept(pid, sid, rev, body):
    if type(body.get('accepted')) is not bool:
        raise ValueError('请选择接受或撤销')
    def commit(data):
        run = _run(data, sid, rev)
        review = run.get('review') or {}
        if run.get('state') != 'completed' or body['accepted'] and review.get('status') != 'completed':
            raise ValueError('先完成这版视频的检查')
        if body['accepted']:
            review['accepted'] = {'at': _now()}
        else:
            review.pop('accepted', None)
    return store.update(pid, commit)


def start(pid, sid, rev, *, force=False):
    """Only user actions start checks; a recorded running check may resume."""
    with store._lock(pid):
        data = store.load(pid)
        run = copy.deepcopy(_run(data, sid, rev))
        if run.get('kind') != 'director' or run.get('state') != 'completed' or not run.get('result'):
            if force:
                raise ValueError('这一版还没有生成完成，请在第 04 步（对照和修改）的“视频”等待结果。')
            return data
        key = f'review:{sid}:{rev}'
        existing = run.get('review') or {}
        if jobs.snapshot(pid).get(key, {}).get('state') == 'running':
            return data
        if not force and existing.get('status') != 'running':
            return data
        attempt = uuid.uuid4().hex
        def begin(current):
            target = _run(current, sid, rev)
            target.pop('fix_plan', None)
            target.pop('fix_plan_input', None)
            target['review'] = {'status': 'running', 'request_id': attempt, 'started_at': _now(), 'error': None}
            if existing.get('accepted'):
                target['review']['accepted'] = copy.deepcopy(existing['accepted'])
        store.update(pid, begin)

        def save(report):
            def commit(current):
                current_run = _run(current, sid, rev)
                if ((current_run.get('review') or {}).get('request_id') == attempt
                        and current_run.get('result') == run['result']):
                    saved = {**report, 'request_id': attempt, 'generated_at': _now()}
                    if report.get('status') == 'failed' and current_run['review'].get('accepted'):
                        saved['accepted'] = copy.deepcopy(current_run['review']['accepted'])
                    else:
                        saved.pop('accepted', None)
                    current_run['review'] = saved
            store.update(pid, commit)

        def work():
            report = dict(status='failed', inputs_sig=None)
            request = None
            try:
                directory = store.project_dir(pid)
                folder = directory / 'analysis/segments/reviews' / attempt
                folder.mkdir(parents=True, exist_ok=True)
                context = _inputs(data, sid, run, directory, folder)
                clip = _file(directory, run['result'])
                video_sha = store.sha256_file(clip)
                request, duration = messages(context, directory, clip, folder)
                report['inputs_sig'] = segment_plan.digest([video_sha, request])

                responses = []
                def capture(content):
                    path = folder / f'response-{len(responses) + 1:03d}.json'
                    path.write_text(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2), encoding='utf-8')
                    responses.append(store.rel(pid, path))
                    report['response_file'] = store.rel(pid, path)

                value, _ = reason(request, pid=pid, max_tokens=8000, on_response=capture)
                if not responses:
                    capture(value)
                checked = validate(value, duration, context['segment']['cells'])
                if store.sha256_file(clip) != video_sha:
                    raise ValueError('检查期间结果视频改变，请点“检查视频”再查一次。')
                report.update(checked, status='completed', error=None, duration=duration)
            except Exception as exc:
                report.update(failure_report(exc, request))
                if existing.get('accepted'):
                    report['accepted'] = copy.deepcopy(existing['accepted'])
            try:
                save(report)
            except store.ProjectDeletedError:
                pass

        try:
            jobs.start(pid, key, work, f'{sid} {rev} 检查视频')
        except Exception as exc:
            report = {**failure_report(exc), 'inputs_sig': None}
            if existing.get('accepted'):
                report['accepted'] = copy.deepcopy(existing['accepted'])
            save(report)
        return store.load(pid)
