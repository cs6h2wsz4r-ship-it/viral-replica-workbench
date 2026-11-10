"""Director plans, local storyboard references and explicit plan edits.

Reasoning writes a complete plan only after validation. Video submission remains in
the bridge; this module never submits image or video generation jobs.
"""
from __future__ import annotations

import copy
import json
import math
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

from . import cast, gateway, llm, scene_prompt, store, storyboard, storyboard_edits
from .gateway.errors import CheckedLLMError, JSONResponseError

INCOMPLETE = '导演方案不完整：'
STALE_WARNING = '上游内容改过，回第 04 步（对照和修改）检查这一段的新分镜，再在“提示词”里重新写这一段。'
LEGACY_WARNING = '这个方案是旧版本做的，点“全部写提示词”重新生成。'
PREREQUISITE = '先在第 04 步（对照和修改）生成每段的新分镜'
REWRITE_WARNING = '这一段的说明没写好，点“重新写这一段”再试一次。'
SILENT_SYSTEM = '''你是短视频复刻导演，按“链路五”的写法，为每段写一条能直接提交给视频模型的完整提示词。第 04 步（对照和修改）已定好分段、生成方式和提交的素材，并已画好每格的新画面；你不改这些，只写提示词。只输出 JSON。
动笔前先针对这条片子分析风险：新旧商品在功能和状态上的差异会带来哪些画面变化；原片哪些时间点、台词或反应和新剧情不合；哪些画面容易漏回原片的人、衣服、发饰、旧商品、旧包装或文字。结论落实到提示词和 check 里，不单独输出分析过程。
优先级从高到低：
① 人和商品换干净：原片人物的脸、发型、衣服和发饰配饰，旧商品、旧包装和字幕都不出现，新商品的外观和用法正确。
② 剧情逻辑通顺：新商品按它自己的真实功能和使用方式表现，不照搬原片旧商品的外观和状态；旧商品没有、新商品具备的状态，在剧情需要时按常识表现，这不算编造功能；界面上的具体文字不要写。台词和反应放在剧情上说得通的时刻；原片时间点与剧情不合时移到合理的时刻，并在 summary 里用一句话说明。
③ 动作、机位、镜头顺序和节奏对上原片。
④ 新衣服的款式细节、配饰、背景小物权重最低，不为它们写约束。
每段提示词按下面顺序写成连贯的中文段落，可以按时间分行，不加小标题：
1. 开头一句：写实手机竖屏视频、时长、比例。
2. 每份素材的编号和职责，编号严格使用请求给出的“图1…图N、视频1”：新画面说明它是哪一刻替换后的状态；人物照锁定这个人的脸、发型、年龄、体型和穿着；商品照锁定商品外观；视频1只提供场景、机位运镜、镜头顺序、切点和人物动作节奏，它里面的人物外观、衣服、发饰、旧商品和文字一律不用。
3. 替换和保持：换谁、换成什么样（发型、衣服的大类和颜色）、新商品什么样；保持哪些场景和不变的商品。
4. 按时间顺序写完整动作和镜头：用请求给出的成片时间窗写“a–b 秒：……”，写机位或景别、人物动作、商品状态、台词的说话人和逐字内容（保留原语言）。镜头之间按原片硬切，不编造原片没有的动作和过渡。video 模式下动作细节由视频1提供，只写清每个时间窗在做什么和关键状态，不逐帧复述。
5. 起止状态和连续性：首段写开头状态，非末段写结尾状态；同一人物、衣服和商品跨镜头一致；手、接触和商品结构物理可信。
6. 声音：环境声、动作声和确定的台词；没有旁白；原片明确有配乐才保留配乐。
7. 禁止项：逐项点名原片人物的脸、发型、衣服、发饰配饰，旧商品、旧包装和品牌文字，以及字幕、水印、平台账号、拼贴、分屏和新增人物。
不写素材里看不到、也无法确认的功能；不写“照分镜生成”这类空话；不写 c01 这类内部格号。商品剧情检查以第 03 步回答为准，回答“不确定”时只提问，不当成已确认能力。每条提示词不超过 4500 字，删掉重复约束和空泛形容词。
check 写 3 条左右，最多 6 条具体动作或画面的生成后检查项。
输出：{"summary":"一句话思路（中文）","questions":[],"segments":[{"id":"d01","what":"这一段拍什么（中文白话）","check":["生成后重点看什么"],"prompt":"完整提示词"}]}
用输入的英文角色词区分每个人，多人身份不能合并或互换，即使长相相近也不行。商品按真实用法使用或摆放，展示时正面或功能面朝向镜头；原片视频提供商品被拿取、展示和使用的方式和节奏。收到验收意见时，只针对意见修改相关内容，把修改落实成可执行的画面要求，其余保持。'''

SPEECH_RULES = '''有口播时，台词和画面分成两条独立的线，以“本段口播线”为依据，单独写一块“口播”：按句写完整台词的说话人和逐字内容，以及开始时间，保留原语言，句与句之间的停顿照原片经拉伸后的时间写；明确写“连续说完，切换镜头时不停顿”。
画面部分按每格成片时间窗只写动作、机位和商品状态，不再按镜头分配“逐字说……”；口播中的整句不能被画面描述拆开。镜头切点结合“切点与口播对应”写成“说到‘某词’时，按视频1硬切到……”，句间停顿处则明确写在该停顿时切；image 模式直接写硬切到目标画面，不引用不存在的视频1。'''
SYSTEM = SILENT_SYSTEM.replace('商品状态、台词的说话人和逐字内容（保留原语言）', '商品状态').replace('不加小标题', '画面部分不加小标题') + '\n' + SPEECH_RULES


def _fail(reason):
    raise ValueError(INCOMPLETE + reason)


def _file(directory, file):
    path = (directory / str(file or '')).resolve()
    if not path.is_relative_to(directory.resolve()) or not path.is_file():
        raise ValueError('参考图片不存在或不在项目内')
    return path


def _hash(directory, file):
    try:
        return store.sha256_file(_file(directory, file))
    except (OSError, ValueError):
        return 'missing'


def _current(data, sid):
    from . import segment_boards
    return segment_boards.current(data, sid)


def require_ready(data, directory, segment_id=None):
    from . import segment_plan, segment_boards
    plan = data.get('segments_plan') or {}
    if not plan.get('segments') or segment_plan.status(data, directory).get('stale'):
        raise ValueError('先在第 04 步（对照和修改）生成或更新分段方案')
    sources = {s['id']: s for s in segment_boards.sheets(data)}
    for segment in plan['segments']:
        if segment_id is not None and segment['id'] != segment_id:
            continue
        sid = segment['id']; source = sources.get(sid); current = _current(data, sid)
        if not source or not current or current.get('storyboard_sig') != storyboard_edits.board_sig(data, sid):
            raise ValueError(PREREQUISITE)
        if storyboard_edits.casting_stale(data,sid,directory):
            raise ValueError('这一段按旧选角生成，请先生成新分镜')
        if source.get('segment_sig') != segment_plan.visual_segment_sig(data, segment):
            raise ValueError('第 04 步（对照和修改）的分段方案改过这一段，请重新生成这一段的新分镜。')
        _file(directory, source.get('file')); _file(directory, current.get('file'))
        for cid in segment['cells']:
            _file(directory, current.get('cells', {}).get(cid))


def _roles(data):
    people = copy.deepcopy((data.get('breakdown') or {}).get('characters') or [])
    people.extend({'id':r['source_id'], 'name':r.get('name', '')}
                  for r in (data.get('replacements') or {}).get('characters', []) if r.get('action') == 'add')
    return scene_prompt.role_names({'breakdown': {'characters': people}})


def material_options(data, directory, segment=None):
    if segment is not None:
        from . import segment_plan
        roles=_roles(data)
        return [{**m,'roles':['product' if m['key']=='product' else 'character'],'role':roles.get(m['key'],'')}
                for m in segment_plan.segment_photos(data,segment,directory)]
    cells = (data.get('storyboard') or {}).get('cells') or []
    if segment is not None:
        selected = set(segment.get('cells') or [])
        cells = [c for c in cells if c['id'] in selected]
    order = list(dict.fromkeys(cid for c in cells for cid in c.get('characters', [])))
    rep = data.get('replacements') or {}
    names = {c['id']:c.get('name', '') for c in (data.get('breakdown') or {}).get('characters', [])}
    roles = _roles(data)
    entries = [r for r in rep.get('characters', []) if r.get('action') in ('replace', 'add')]
    entries.sort(key=lambda r:order.index(r['source_id']) if r['source_id'] in order else len(order))
    if (rep.get('product') or {}).get('action') == 'replace':
        entries.append({**rep['product'], 'source_id':'product'})
    result = []
    for row in entries:
        key = row['source_id']; product = key == 'product'
        name = '商品' if product else (key + ' ' + (row.get('name') or names.get(key, ''))).strip()
        try:
            file = str(cast._photo(data, row, directory).relative_to(directory.resolve()))
        except (ValueError, OSError):
            file = ''
        result.append(dict(key=key, id=row.get('asset_id'), asset_id=row.get('asset_id'), name=name, label=name,
                           file=file, roles=['product' if product else 'character'], description=row.get('description') or '',
                           role=roles.get(key, ''), added=row.get('action') == 'add', available=bool(file),
                           enabled=product or key in order or row.get('action') == 'add'))
    return result


def segment_inputs_sig(data, directory, segment_id, *, allow_pending=False):
    from . import segment_plan, segment_boards
    segment = segment_plan.get_segment(data, segment_id)
    state = segment_boards.state_for(data, segment_id)
    # Added migration evidence must not invalidate an otherwise identical prompt.
    version = {key:value for key,value in (_current(data,segment_id) or {}).items()
               if key not in ('settings_sig','settings_materials')}
    materials = [{**m, 'role': scene_prompt.signature_role_label(m['key'], m['role']),
                  'sha256': _hash(directory, m['file'])}
                 for m in material_options(data, directory, segment)]
    try:images=segment_plan.generation_images(data,segment,directory)
    except ValueError:
        if not allow_pending:raise
        images=['pending',segment.get('gen_images')]
    inputs = [segment_plan.segment_sig(segment), images,
        segment_boards.source_sig(data, segment_id, directory), storyboard_edits.board_sig(data, segment_id),
        state.get('source'), state.get('current'),
        version, {cid: _hash(directory, file) for cid, file in version.get('cells', {}).items()},
        _hash(directory, version.get('file')), materials,
        (data.get('replacements') or {}).get('product', {}).get('story_checks', []),
        data.get('breakdown') or {}]
    # Preserve persisted pre-V2e signatures until an explicit Step04 rerun.
    generation = (data.get('segments_plan') or {}).get('generation_id')
    if generation: inputs.append(generation)
    if (data.get('breakdown') or {}).get('speech_lines'):
        inputs.append(_hash(directory, 'analysis/transcript.raw.json'))
    return storyboard_edits.digest(inputs)


def inputs_sig(data, directory):
    return storyboard_edits.digest([segment_inputs_sig(data, directory, s['id'],allow_pending=True)
        for s in (data.get('segments_plan') or {}).get('segments', [])])


def segment_sig(segment):
    keys = ('id', 'cells', 'mode', 'images', 'gen_images', 'prompt', 'stretch',
            'source_start', 'source_end', 'source_seconds', 'target_seconds', 'duration', 'needs_rewrite')
    return storyboard_edits.digest({k:segment.get(k) for k in keys})


def get_segment(data, segment_id):
    segment = next((s for s in (data.get('director') or {}).get('segments', []) if s.get('id') == segment_id), None)
    if segment is None:
        raise ValueError('没有这段导演方案，请刷新页面')
    return segment


def is_legacy(plan):
    return bool(plan and plan.get('format_version') != 3)


def _normalize_checks(checks):
    if isinstance(checks, str):
        checks = [checks]
    # Preserve malformed values for validation; never invent string content.
    if not isinstance(checks, list) or any(not isinstance(value, str) for value in checks):
        return checks
    return [value.strip() for value in checks if value.strip()][:6]


def _prompt_problem(segment, data=None):
    prompt=segment.get('prompt')
    if not isinstance(prompt,str) or not prompt.strip():
        return '模型没有写这一段的完整提示词'
    if len(prompt)>5000:
        return f'这一段的提示词有 {len(prompt)} 字符，超过 5000 上限'
    expected={f'图{i}' for i in range(1,len(segment.get('images') or [])+1)}
    if segment.get('mode')=='video':expected.add('视频1')
    actual={kind+number for kind,number in re.findall(r'(视频|图)\s*(\d+)',prompt)}
    if actual != expected:
        return '提示词素材编号不完整或含不存在的编号：'+'、'.join(sorted(actual ^ expected))
    ids=[c['id'] for c in ((data or {}).get('storyboard') or {}).get('cells',[])]
    if any(
            re.search(r'(?<![A-Za-z0-9_])'+re.escape(cid)+r'(?![A-Za-z0-9_])',prompt) for cid in ids):
        return '提示词不能包含内部格号'
    checks=_normalize_checks(segment.get('check'))
    if not isinstance(checks,list) or not checks or any(not isinstance(v,str) for v in checks):
        return '生成后检查项必须包含至少 1 条非空字符串'
    return ''


def refresh_prompt_status(segment, data):
    """Recheck the caller's in-memory row; never save or request a model."""
    segment['check'] = _normalize_checks(segment.get('check'))
    problem = _prompt_problem(segment, data)
    segment['needs_rewrite'] = bool(problem or segment.get('structure_needs_rewrite'))
    if problem:
        segment['rewrite_reason'] = problem
    else:
        segment.pop('rewrite_reason', None)
    warning = speech_problem(segment, data)
    if warning:
        segment['speech_warning'] = warning
    else:
        segment.pop('speech_warning', None)


def speech_context(data, segment, directory=None):
    """Independent speech and visual-cut clocks, never re-sentence by cell."""
    from . import speech_lines, segment_plan
    lines = (data.get('breakdown') or {}).get('speech_lines') or []
    if not lines:
        return [], []
    if directory is None and data.get('id'):
        directory = store.project_dir(data['id'])
    try: words = speech_lines.words(data, directory)
    except (ValueError, OSError): words = []
    start, end = float(segment['source_start']), float(segment['source_end'])
    stretch = float(segment['stretch'])
    def clock(second):
        return round((min(end, max(start, float(second)))-start)*stretch, 2)
    result = []
    for index, line in enumerate(lines, 1):
        if float(line['start']) >= end or float(line['end']) <= start:
            continue
        bounds = line.get('words') or []
        scoped = [w for w in words if (len(bounds) == 2 and bounds[0] <= w['index'] <= bounds[1])
                  or (len(bounds) != 2 and w['start'] < line['end'] and w['end'] > line['start'])]
        selected = [w for w in scoped if w['start'] < end and w['end'] > start]
        text = line['text']
        if selected and selected != scoped:
            text = ' '.join(w['word'] for w in selected)
        elif scoped and not selected:
            continue
        # No invented word times for sentence-only fallback transcripts.
        pause_end = float(lines[index]['start']) if index < len(lines) else float(line['end'])
        result.append(dict(line=index, start=clock(line['start']), end=clock(line['end']),
                           text=text, speaker=line.get('speaker', ''),
                           pause_after=round(max(0, min(end, pause_end)-max(start, float(line['end'])))*stretch, 2),
                           words=[dict(index=w['index'], word=w['word'], start=clock(w['start']), end=clock(w['end'])) for w in selected]))
    cells = {c['id']: c for c in (data.get('storyboard') or {}).get('cells', [])}
    cuts = []
    for cid in segment.get('cells', [])[1:]:
        cut = float(cells[cid]['start'])
        if not start < cut < end:
            continue
        state = segment_plan.cut_speech(data, cut, word_rows=words)
        current = next((w for w in words if w['start'] <= cut < w['end']), None)
        entry = dict(time=clock(cut), source_cut=cut, speech=state['speech'])
        if state['speech'] == '句中':
            if current:
                entry.update(word=current['word'], position='正在说的词')
            else:
                upcoming = next((w for w in words if w['start'] >= cut), None)
                entry.update(word=upcoming['word'] if upcoming else '', position='句内词间，接下来说到该词')
            entry['between'] = state.get('between', [])
        else:
            entry.update(position='处于句间停顿', pause=round(state.get('pause', 0)*stretch, 2))
        cuts.append(entry)
    return result, cuts


def speech_problem(segment, data):
    from . import speech_lines
    if not (data.get('breakdown') or {}).get('speech_lines') or not isinstance(segment.get('prompt'), str):
        return ''
    if any(key not in segment for key in ('source_start', 'source_end', 'stretch')):
        return ''
    lines, _ = speech_context(data, segment)
    missing = []
    for line in lines:
        words = line['words'] or [dict(word=word) for word in line['text'].split()]
        if words and not speech_lines.contains_words(segment['prompt'], words):
            missing.append(str(line['line']))
    return ('第 '+ '、'.join(missing) +' 句台词未连续完整写入提示词，拼接处台词可能不连贯；可以点“重新写这一段”再试。') if missing else ''


def _replace_cell_references(text, numbers, segment_cells=None):
    """Translate actual IDs only; Chinese neighbors are not word boundaries."""
    if not text or not numbers:
        return text
    ids = '|'.join(re.escape(cid) for cid in sorted(numbers, key=len, reverse=True))
    # ASCII token boundaries avoid matching c01 inside abc01/c010, while allowing
    # Chinese prose such as c06严格 and ranges such as c04至c08.
    token = rf'(?<![A-Za-z0-9_])({ids})(?![A-Za-z0-9_])'
    pattern = token + rf'(?:\s*[至–-]\s*{token})?'
    panels = {cid: i for i, cid in enumerate(segment_cells or [], 1)}

    def replace(match):
        first, last = match.groups()
        if segment_cells is None:
            label = str(numbers[first])
            if last is not None:
                label += f'–{numbers[last]}'
            return f'第 {label} 格'
        kind = 'panel' if first in panels else 'shot'
        label = f'{kind} {panels.get(first, numbers[first])}'
        if last is not None:
            end_kind = 'panel' if last in panels else 'shot'
            end = panels.get(last, numbers[last])
            label += f'–{end}' if kind == end_kind else f'–{end_kind} {end}'
        return label

    return re.sub(pattern, replace, text)


def _ordered_segments(rows, ids):
    """Bind content by unique ID; response order does not change segment order."""
    if len(rows) != len(ids) or any(not isinstance(row, dict) for row in rows):
        _fail('提示词必须按第 04 步（对照和修改）的分段方案逐段返回')
    byid = {}
    for row in rows:
        sid = row.get('id')
        if not isinstance(sid, str) or sid not in ids or sid in byid:
            _fail('提示词段落 ID 与第 04 步（对照和修改）的分段方案不一致或重复')
        byid[sid] = row
    return [byid[sid] for sid in ids]


def validate_plan(value, data, directory, *, preserve_brief=False, reference_cells=None, target_id=None):
    """Bind free-form model prompts to the fixed Step04 structure and clock."""
    from . import segment_plan
    source=data.get('segments_plan') or {}
    if source.get('format_version')!=2:
        _fail('先在第 04 步（对照和修改）生成分段方案')
    if not isinstance(value,dict) or not isinstance(value.get('segments'),list):
        _fail('没有有效段落')
    wanted=[row for row in source['segments'] if target_id is None or row['id']==target_id]
    rows=_ordered_segments(value['segments'], [row['id'] for row in wanted])
    cells=(data.get('storyboard') or {}).get('cells',[])
    numbers={c['id']:i for i,c in enumerate(cells,1)}
    legacy_ids={t.get('id') for t in (data.get('script') or {}).get('tasks',[])}
    normalized=[]
    for planned,row in zip(wanted,rows):
        if preserve_brief:
            # Text edits keep their original bindings (including pending rows).
            # Only explicit authoring may bind a prompt to today's new photos.
            structural=copy.deepcopy(get_segment(data,planned['id']))
        else:
            local={**data,'storyboard':{**data['storyboard'],'cells':[c for c in cells if c['id'] in planned['cells']]}}
            effective=segment_plan.effective_segment(data,planned,directory)
            fixed=segment_plan.validate_plan({'segments':[effective]},local,directory)['segments'][0]
            structural={**fixed,'images':list(fixed['gen_images'])}
        if structural['id'] in legacy_ids:_fail('导演段落 ID 不能与已有脚本任务 ID 重复')
        if preserve_brief and any(row.get(k)!=structural[k] for k in ('id','cells','mode','images','gen_images')):
            _fail('格号、生成方式和参考照片由第 04 步（对照和修改）的分段方案确定，请重新生成分段方案')
        item={**structural,'what':_replace_cell_references(row.get('what'),numbers) if isinstance(row.get('what'),str) else '',
              'prompt':row.get('prompt'),'check':_normalize_checks(copy.deepcopy(row.get('check'))),
              'edited':bool(preserve_brief and row.get('edited'))}
        if isinstance(item['check'],list):
            item['check']=[_replace_cell_references(v,numbers) if isinstance(v,str) else v for v in item['check']]
        if preserve_brief and row.get('structure_needs_rewrite'):
            item['structure_needs_rewrite'] = True
        refresh_prompt_status(item, data)
        if preserve_brief and row.get('selected_run'):item['selected_run']=row['selected_run']
        item['inputs_sig']=row.get('inputs_sig') if preserve_brief else segment_inputs_sig(data,directory,planned['id'])
        item['plan_sig']=row.get('plan_sig') if preserve_brief else segment_plan.segment_sig(planned)
        item['warnings']=segment_warnings(data,item)
        normalized.append(item)
    summary=value.get('summary') if isinstance(value.get('summary'),str) else ''
    questions=value.get('questions',[])
    return dict(format_version=3,summary=_replace_cell_references(summary,numbers),
                questions=[_replace_cell_references(q,numbers) for q in questions if isinstance(q,str)] if isinstance(questions,list) else [],
                segments=normalized)


def plan_warnings(plan):
    return []


def segment_warnings(data, segment):
    # Validation reasons are shown beside the editable prompt, not as warnings.
    return []


def _storyboard_ref(segment):
    sid = segment['id']
    if not isinstance(sid, str) or not store.SAFE_ID.fullmatch(sid):
        _fail('段落 ID 不安全')
    return dict(key='storyboard', id='director-storyboard-' + sid, asset_id='director-storyboard-' + sid,
                file=f'analysis/director/seg-{sid}-storyboard.jpg', label='分镜图', name='分镜图',
                type='image', roles=['storyboard'], internal=True, description='本段当前新分镜，按行从左至右排列')


def references(data, segment, directory):
    from . import segment_plan
    planned=segment_plan.get_segment(data,segment['id'])
    keys=segment_plan.generation_images(data,planned,directory)
    if segment.get('images',keys)!=keys:
        _fail('提交图片与第 04 步（对照和修改）的分段方案不同，请在“提示词”里重新写这一段')
    options={r['key']:r for r in material_options(data,directory,segment)}
    current=(_current(data,segment['id']) or {}).get('cells',{})
    numbers={c['id']:i for i,c in enumerate(data.get('storyboard',{}).get('cells',[]),1)}
    rows=[]
    for index,key in enumerate(keys,1):
        if key in planned['cells']:
            row=dict(key=key,id='director-cell-'+segment['id']+'-'+key,asset_id='director-cell-'+segment['id']+'-'+key,
                     file=current.get(key,''),cell=key,roles=['target_cell'],role='target_cell',
                     description=f'第 {numbers[key]} 格的新画面（替换后的状态）')
        else:
            if key not in options or not options[key]['available']:_fail('参考照片不存在：'+str(key))
            row=copy.deepcopy(options[key])
            row['description']='新商品照片' if key=='product' else row['role']+' 的人物照'
        _file(directory,row['file'])
        row.update(type='image',label=f'图{index}',tag=f'图{index}',duty=row['description'])
        rows.append(row)
    return rows


def generation_materials(data, segment, directory):
    rows=references(data,segment,directory)
    if segment['mode']=='video':
        description=f"原片 {segment['source_start']:g}–{segment['source_end']:g} 秒，均匀拉伸到 {segment['duration']:g} 秒"
        rows.append(dict(key='source_video',type='video',label='视频1',tag='视频1',
                         file=(data.get('source') or {}).get('file',''),description=description,duty=description,
                         source_start=segment['source_start'],source_end=segment['source_end'],duration=segment['duration']))
    return rows


def _cell_images(data, segment):
    current = (_current(data, segment['id']) or {}).get('cells', {})
    cells = {c['id']: c for c in (data.get('storyboard') or {}).get('cells', [])}
    return [dict(id=cid, cell=cid, file=current.get(cid, ''), index=cells.get(cid, {}).get('index'),
                 start=cells.get(cid, {}).get('start'), end=cells.get(cid, {}).get('end'))
            for cid in segment.get('cells') or []]


def build_storyboard(pid, segment_id):
    with store._lock(pid):
        data = store.load(pid); directory = store.project_dir(pid)
        segment = get_segment(data, segment_id)
        images = _cell_images(data, segment)
        if not images:
            raise ValueError(PREREQUISITE)
        tiles = []
        for row in images:
            try:
                with Image.open(_file(directory, row['file'])) as image:
                    tiles.append(image.convert('RGB'))
            except (OSError, ValueError):
                raise ValueError(PREREQUISITE) from None
        width, height = tiles[0].size; count = len(tiles)
        from .segment_boards import layout
        columns, rows = layout(count)
        gap = 6
        image = Image.new('RGB', (columns*width+(columns-1)*gap, rows*height+(rows-1)*gap), 'white')
        for index, tile in enumerate(tiles):
            # Fit preserves aspect ratio if an older single-cell edit has a different size.
            tile = ImageOps.pad(tile, (width, height), method=Image.Resampling.LANCZOS, color='white')
            image.paste(tile, ((index % columns)*(width+gap), (index // columns)*(height+gap)))
        asset = _storyboard_ref(segment); path = directory / asset['file']
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, quality=95, subsampling=0)
        asset['sha256'] = store.sha256_file(path)
        def commit(d):
            assets = d.setdefault('assets', [])
            index = next((i for i, a in enumerate(assets) if a.get('id') == asset['id']), len(assets))
            if index == len(assets):
                assets.append(asset)
            else:
                assets[index] = asset
        store.update(pid, commit)
        return asset


def messages(data, directory, *, target=None, review_context=None):
    from . import segment_boards, segment_plan
    pack = data.get('breakdown') or {}
    parts = [{'type': 'text', 'text': '原片故事、节奏、对白和商品信息：' + json.dumps(
        {k: pack.get(k) for k in ('overview', 'beats', 'dialogue', 'productMoments', 'characters', 'scene', 'sound', 'performance')}, ensure_ascii=False)}]
    byid = {c['id']: c for c in data['storyboard']['cells']}
    sources = {s['id']: s for s in segment_boards.sheets(data)}
    chosen = [s for s in data['segments_plan']['segments'] if target is None or s['id'] == target['id']]
    for planned in chosen:
        # UI-only history must not change the model request, including key order.
        planned={key:value for key,value in planned.items() if key!='mode_suggested'}
        planned=segment_plan.effective_segment(data,planned,directory)
        sid = planned['id']; source = sources[sid]; version = _current(data, sid) or {}
        changes = {r['cell']: r for r in (segment_boards.state_for(data, sid).get('plan') or {}).get('cells', [])}
        context = []
        for cid in planned['cells']:
            cell = byid[cid]; text = storyboard.cell_text(cell, pack)
            context.append({**cell, 'action': text['action'], 'note': text['note'], 'plan': changes.get(cid, {})})
        windows=[dict(cell=cid,number=list(byid).index(cid)+1,
                      start=round((float(byid[cid]['start'])-planned['source_start'])*planned['stretch'],1),
                      end=round((float(byid[cid]['end'])-planned['source_start'])*planned['stretch'],1)) for cid in planned['cells']]
        materials=generation_materials(data,planned,directory)
        hint='每条完整提示词不超过 4500 字，硬上限 5000 字符，删除重复约束和空泛形容词。'
        if target and (target.get('needs_rewrite') or target.get('speech_warning')):
            hint+='上一版需要修改：'+str(target.get('rewrite_reason') or target.get('speech_warning') or '')
        parts.append({'type':'text','text':f'本段 {sid}：共 {len(planned["cells"])} 格。'
            '\n第 04 步已确定，不能修改：'+json.dumps(planned,ensure_ascii=False)+
            '\n生成时长：'+str(planned['duration'])+' 秒；每格成片时间窗：'+json.dumps(windows,ensure_ascii=False)+
            '\n每格拆解文字和改图方案：'+json.dumps(context,ensure_ascii=False)+'\n'+hint})
        lines, cuts = speech_context(data, planned, directory)
        if lines:
            parts.append(dict(type='text', text='本段口播线（成片时钟，秒）：'+json.dumps(lines, ensure_ascii=False)
                              +'\n切点与口播对应（成片时钟，秒）：'+json.dumps(cuts, ensure_ascii=False)))
        for label,file in (('本段原片分镜（带格号）',source['file']),('本段当前新分镜拼图（带格号）',version.get('file'))):
            parts.append({'type':'text','text':f'{sid} {label}，只给推理模型理解，不提交给视频模型'})
            parts.extend(llm.image_part(_file(directory,file)))
        for material in materials:
            parts.append({'type':'text','text':material['label']+'：'+material['description']})
            if material['type']=='image':parts.extend(llm.image_part(_file(directory,material['file'])))
    replacements = copy.deepcopy(data.get('replacements') or {})
    checks = (replacements.get('product') or {}).get('story_checks', [])
    for check in checks:
        if not check.get('answer') or str(check['answer']).lower() in ('unsure', 'unknown'):
            check['answer'] = '不确定'
    parts.append({'type': 'text', 'text': '选角和商品设定：' + json.dumps(replacements, ensure_ascii=False) +
        '\n商品剧情检查（不确定不能猜测）：' + json.dumps(checks, ensure_ascii=False)})
    if target:
        segments=data['segments_plan']['segments']
        index=next(i for i,s in enumerate(segments) if s['id']==target['id'])
        neighbors=[]
        for i in (index-1,index+1):
            if not 0<=i<len(segments):continue
            adjacent=segments[i];sid=adjacent['id']
            neighbor={'id':sid,'cells':[byid[cid] for cid in adjacent['cells']],
                      'source_start':adjacent['source_start'],'source_end':adjacent['source_end']}
            authored=next((s for s in (data.get('director') or {}).get('segments',[]) if s['id']==sid),None)
            if authored:neighbor['prompt']=authored.get('prompt','')
            neighbors.append(neighbor)
            source=sources.get(sid,{})
            version=_current(data,sid) or {}
            for label,file in (('相邻段原片分镜',source.get('file')),('相邻段新分镜',version.get('file'))):
                if file:
                    parts.append({'type':'text','text':sid+' '+label+'，只作上下文'})
                    parts.extend(llm.image_part(_file(directory,file)))
        parts.append({'type': 'text', 'text': f'只输出 id={target["id"]} 的单个段落 JSON，仅含内容字段和 id，不输出整个计划。'
            '字段与整片输出 segments 内的段落一致：id、what、check、prompt。相邻段上下文：' + json.dumps(neighbors, ensure_ascii=False)})
    if review_context:
        parts.append({'type':'text','text':'本版已完成的验收意见 issues：'+json.dumps(review_context['issues'],ensure_ascii=False)+
                      '\n这一版当时提交的提示词：'+review_context['prompt']})
    has_speech = any(speech_context(data, segment, directory)[0] for segment in chosen)
    return [{'role': 'system', 'content': SYSTEM if has_speech else SILENT_SYSTEM}, {'role': 'user', 'content': parts}]


def _save_response(pid, kind, value, *, raw=False):
    if kind != 'full' and (not isinstance(kind, str) or not store.SAFE_ID.fullmatch(kind)):
        _fail('段落 ID 不安全')
    directory = store.project_dir(pid)
    folder = directory / 'analysis/director/responses'
    folder.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also protects replies arriving within the same clock tick.
    while True:
        path = folder / f'{datetime.now().astimezone():%Y%m%dT%H%M%S.%f}-{kind}.json'
        try:
            with path.open('x', encoding='utf-8') as stream:
                stream.write(value if raw else json.dumps(value, ensure_ascii=False, indent=2))
            return path.relative_to(directory).as_posix()
        except FileExistsError:
            continue


def _response_error(pid, reason, path):
    store.log(pid, f'{reason}；模型原始回复：{path}', 'error')


@contextmanager
def _validate_response(pid, path):
    try:
        yield
    except ValueError as exc:
        _response_error(pid, str(exc), path)
        raise


def _reason(pid, request, max_tokens, kind='full'):
    responses = []
    def capture(content):
        try:
            parsed = llm.extract_json(content)
        except JSONResponseError:
            path = _save_response(pid, kind, content, raw=True)
            responses.append(path)
            _response_error(pid, INCOMPLETE + '模型没有返回完整 JSON，请重试', path)
        else:
            responses.append(_save_response(pid, kind, parsed))

    try:
        value, _ = gateway.reason_checked('director', request, project=pid, max_tokens=max_tokens,
                                          on_response=capture)
        # Also support checked clients that supply an already parsed reply.
        if not responses:
            responses.append(_save_response(pid, kind, value))
        return value, responses[-1]
    except JSONResponseError:
        _fail('模型没有返回完整 JSON，请重试')
    except CheckedLLMError as exc:
        if 'JSON' in str(exc).upper():
            _fail('模型没有返回完整 JSON，请重试')
        raise


def generate(pid):
    data = store.load(pid); directory = store.project_dir(pid)
    require_ready(data, directory)
    sig = inputs_sig(data, directory); old = copy.deepcopy(data.get('director'))
    value, response = _reason(pid, messages(data, directory), 16000)
    with _validate_response(pid, response):
        plan = validate_plan(value, data, directory)
    for segment in plan['segments']:
        segment.pop('selected_run', None)
    plan.update(inputs_sig=sig, generated_at=datetime.now().astimezone().isoformat())
    def commit(current):
        if inputs_sig(current, directory) != sig or current.get('director') != old:
            raise ValueError('内容刚刚改过，请重新生成全部提示词')
        current['director'] = plan
    store.update(pid, commit)
    automatic_plan = copy.deepcopy(plan)
    for segment in plan['segments']:
        if segment['needs_rewrite'] or segment.get('speech_warning'):
            try:
                updated = _rewrite(pid, segment['id'], expected_plan=automatic_plan, expected_sig=sig, speech_retry=False)
                if updated is None:
                    break
                automatic_plan = copy.deepcopy(updated['director'])
            except (ValueError, CheckedLLMError, OSError) as exc:
                store.log(pid, f'{segment["id"]} 自动重写未完成：{exc}', 'error')
    return store.load(pid)


def _rewrite(pid, segment_id, *, expected_plan=None, expected_sig=None, review_rev=None, speech_retry=True):
    from . import segment_plan
    data = store.load(pid); directory = store.project_dir(pid)
    sig = inputs_sig(data, directory)
    if expected_plan is not None and (data.get('director') != expected_plan or sig != expected_sig):
        return None
    require_ready(data, directory, segment_id)
    planned=segment_plan.get_segment(data,segment_id)
    old=copy.deepcopy(data.get('director'))
    existing_plan=old if old and not is_legacy(old) else None
    segment=next((s for s in (existing_plan or {}).get('segments',[]) if s['id']==segment_id),planned)
    review_context = _review_context(data,segment_id,review_rev) if review_rev is not None else None
    request = messages(data, directory, target=segment, review_context=review_context)
    value, response = _reason(pid, request, 6000, segment_id)
    with _validate_response(pid, response):
        if review_context is not None and isinstance(value,dict):
            value={**value,'what':segment.get('what'),'check':copy.deepcopy(segment.get('check'))}
        revised = validate_plan({'segments': [value]}, data, directory, target_id=segment_id)['segments'][0]
        if speech_retry and revised.get('speech_warning'):
            retry = request + [dict(role='assistant', content=json.dumps(value, ensure_ascii=False)),
                               dict(role='user', content='请重写这一段：'+revised['speech_warning']+' 返回同样的单段 JSON。')]
            try:
                second, _ = _reason(pid, retry, 6000, segment_id)
                if review_context is not None and isinstance(second, dict):
                    second = {**second, 'what': segment.get('what'), 'check': copy.deepcopy(segment.get('check'))}
                revised = validate_plan({'segments': [second]}, data, directory, target_id=segment_id)['segments'][0]
            except (ValueError, CheckedLLMError, OSError):
                store.log(pid, '台词自动重写没完成，已保留这一版，可手动重新写这一段。', 'warn')
        if segment.get('selected_run') and all(segment.get(k) == revised.get(k)
                                               for k in ('cells', 'source_start', 'source_end')):
            revised['selected_run'] = segment['selected_run']
        candidate = copy.deepcopy(existing_plan) if existing_plan else dict(format_version=3,summary='',questions=[],segments=[])
        existing={s['id']:s for s in candidate['segments']}
        candidate['segments']=[existing.get(s['id']) or {
            **copy.deepcopy(s),'images':[],'prompt':'','what':'','check':[],
            'needs_rewrite':True,'rewrite_reason':'待写','inputs_sig':None,'plan_sig':None
        } for s in data['segments_plan']['segments']]
        index = next(i for i, row in enumerate(candidate['segments']) if row['id'] == segment_id)
        candidate['segments'][index] = revised
        candidate['inputs_sig'] = inputs_sig(data, directory)
        def commit(current):
            if current.get('director') != old or inputs_sig(current, directory) != sig:
                raise ValueError('内容刚刚改过，请重新重写本段')
            if review_context is not None and _review_context(current,segment_id,review_rev)!=review_context:
                raise ValueError('验收意见刚刚改过，请重新重写本段')
            current['director'] = candidate
        return store.update(pid, commit)


def _review_context(data, sid, revision):
    run=next((r for r in data.get('runs',{}).get(sid,[]) if r.get('rev')==revision),None)
    review=(run or {}).get('review') or {}
    if not run or run.get('state')!='completed' or review.get('status')!='completed' or review.get('verdict')!='fix':
        raise ValueError('请选择本段已完成且验收需要修改的版本')
    frozen=(run.get('review_inputs') or {}).get('segment') or {}
    tasks=(run.get('preview') or {}).get('tasks') or []
    prompt=frozen.get('prompt') or (tasks[0].get('prompt') if tasks else None) or frozen.get('brief')
    if not isinstance(prompt,str) or not prompt.strip():
        raise ValueError('这一版缺少当时提交的提示词，无法按验收意见重写')
    if not isinstance(review.get('issues'),list) or not review['issues']:
        raise ValueError('这一版缺少检查问题，请点“检查视频”')
    return dict(prompt=prompt,issues=copy.deepcopy(review['issues']),revision=revision,result=run.get('result'))


def save(pid, body):
    if not isinstance(body, dict):
        raise ValueError('提示词修改格式不正确')
    action = body.get('action', 'save')
    if action not in ('save', 'rewrite', 'prompt', 'brief', 'select-run'):
        raise ValueError('未知的导演方案操作；生成方式和参考照片由分段方案确定，请到第 04 步（对照和修改）重新生成分段方案')
    segment_id = body.get('segment_id') or body.get('id')
    if action == 'rewrite':
        return _rewrite(pid, segment_id, review_rev=body.get('review_rev'))
    if action == 'select-run':
        with store._lock(pid):
            data = store.load(pid); directory = store.project_dir(pid)
            if not data.get('director'):
                raise ValueError('请先生成全部提示词')
            if is_legacy(data['director']):
                raise ValueError(LEGACY_WARNING)
            revision = body.get('selected_run') or body.get('rev')
            run = next((r for r in data.get('runs', {}).get(segment_id, []) if r.get('rev') == revision), None)
            if not run or run.get('state') != 'completed' or not run.get('result'):
                raise ValueError('请选择已完成的成片版本')
            try:
                _file(directory, run['result'])
            except ValueError:
                raise ValueError('已完成的成片文件不存在') from None
            result = store.update(pid, lambda current: get_segment(current, segment_id).__setitem__('selected_run', revision))
        if data.get('derived_from'):
            # Explicit selection completes the user's task; never run on GET.
            # Notify outside the project lock to keep batch lock ordering safe.
            from . import fission_dashboard
            fission_dashboard.task_finished(pid, 'selection-finished')
            return store.load(pid)
        return result
    with store._lock(pid):
        data = store.load(pid); directory = store.project_dir(pid)
        old = data.get('director')
        if not old:
            raise ValueError('请先生成全部提示词')
        if is_legacy(old):
            raise ValueError(LEGACY_WARNING)
        plan = copy.deepcopy(old)
        if action == 'save':
            supplied = copy.deepcopy(body.get('plan') or body.get('director'))
            if not isinstance(supplied, dict) or not isinstance(supplied.get('segments'), list):
                _fail('没有有效段落')
            byid = {s['id']: s for s in old['segments']}
            supplied['segments'] = _ordered_segments(supplied['segments'], list(byid))
            for row in supplied['segments']:
                previous = byid[row['id']]
                if any(row.get(k) != previous.get(k) for k in ('cells', 'mode', 'images', 'gen_images')):
                    _fail('格号、生成方式和参考照片由第 04 步（对照和修改）的分段方案确定，请重新生成分段方案')
                row.update(edited=bool(previous.get('edited') or row.get('prompt') != previous.get('prompt')),
                           inputs_sig=previous.get('inputs_sig'), plan_sig=previous.get('plan_sig'),
                           structure_needs_rewrite=previous.get('structure_needs_rewrite', False))
                if previous.get('selected_run'):
                    row['selected_run'] = previous['selected_run']
                else:
                    row.pop('selected_run', None)
            plan.update(validate_plan(supplied, data, directory, preserve_brief=True))
        else:
            row = get_segment({'director': plan}, segment_id)
            row.update(prompt=body.get('prompt') if action=='prompt' else body.get('brief'), edited=True)
            # Manual text does not change timestamps, source bindings or other segments.
            refresh_prompt_status(row, data)
        return store.update(pid, lambda current: current.update(director=plan))


def view(data, directory):
    from . import segment_plan, segment_boards
    try:
        require_ready(data, directory)
        ready, reason = True, ''
    except (ValueError, OSError, KeyError) as exc:
        ready, reason = False, str(exc)
    plan = data.get('director') or {}; legacy = is_legacy(plan)
    if legacy:
        return dict(ready=ready, reason=reason, stale=True, legacy=True, warnings=[LEGACY_WARNING], segments=[])
    rows = []
    for segment in plan.get('segments', []):
        # project_view passes its response copy. Refresh both the editor's plan
        # and its derived status without migrating anything on disk during GET.
        refresh_prompt_status(segment, data)
        notice = STALE_WARNING
        try:
            stale = segment.get('inputs_sig') != segment_inputs_sig(data, directory, segment['id'])
            if segment.get('plan_sig') != segment_plan.segment_sig(segment_plan.get_segment(data, segment['id'])):
                notice = '这一段的分段设置改过，请重新写这一段提示词。'
        except (ValueError, KeyError):
            stale = True
        try:
            materials=generation_materials(data,segment,directory)
        except (ValueError,KeyError):
            materials=[]
        rows.append({**copy.deepcopy(segment), 'stale': stale, 'warnings': [notice] if stale else [],
                     'material_options': material_options(data, directory, segment), 'generation_materials': materials,
                     'cell_images': _cell_images(data, segment), 'storyboard': _storyboard_ref(segment)})
    stale = any(r['stale'] for r in rows)
    return dict(ready=ready, reason=reason, stale=stale, legacy=False,
                warnings=[STALE_WARNING] if stale else [], summary=plan.get('summary', ''),
                questions=plan.get('questions') or [], segments=rows, material_options=material_options(data, directory))
