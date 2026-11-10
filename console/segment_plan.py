"""V2 story-first segmentation; model replies are archived before validation."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from itertools import combinations
from datetime import datetime

from . import cast, gateway, llm, scene_prompt, store, storyboard
from .gateway.errors import CheckedLLMError, JSONResponseError

INCOMPLETE = '分段方案不完整：'
LEGACY_WARNING = '分段方案是旧版本做的，点“重新生成分段方案”。'
STALE_WARNING = '原片、原片分镜或拆解改过，请重新生成分段方案。'
SILENT_SYSTEM = '''你是短视频复刻的分段导演，参照“链路五”的做法。先理解原片完整故事、节奏、表演、台词和商品作用。分段按完整的事、动作闭合、场景状态和替换风险来划，不按镜头数、格子数或字幕句子划。先选能承载连续关系的较少段数，每多分一段都要有具体理由。镜头切点只是候选的分界，不决定段数；一段里有多次硬切是正常的。分界放在格子之间，优先放在地点、人物出场或商品状态一起变化的硬切处，这样两段之间不需要衔接。
动笔前先针对这条片子分析风险：新旧商品在功能和状态上的差异会带来哪些画面变化；原片哪些时间点、台词或反应和新剧情不合；哪些画面容易漏出原片的人、旧商品、旧包装或文字。分析结论落实到本步的分段、生成方式和理由里，不单独输出分析过程。
判断每格的地点、人物、商品，以这一格的原片画面为准；拆解文字和人物列表可能混入相邻镜头的内容，与画面不符时以画面为准。分界的地点、人物出场、商品状态变化，都按画面来定。
每段单独准备素材：每段只放本段出现的人物和商品照片。本段没有出现的商品，不放商品照片；本段没有出现的人，不放这个人的照片。
生成方式：video = 本段新分镜 + 原片本段视频（无声、按比例拉伸），原片视频负责场景、机位、镜头顺序、人物动作，以及商品被拿取、展示和使用的方式和节奏；image = 只用本段新分镜和照片。取舍以本段画面证据为准：只换人物、商品不变时，原片视频能保住动作，但人物外观仍会把原人物带回来，也要提交目标新画面；被替换的旧商品或旧包装在本段原片里清楚可见时，无论大小，传原片视频都有漏回风险。权衡本段动作的价值和漏回风险，选择 video 或 image，并在 mode_reason 里说明理由。
客观限制：每段原片跨度 ≤ 15 秒；每段最多 6 格；每段最多提交 4 张图，video 模式另加 1 段原片视频。段落 ID 必须安全且唯一。所有段落按原片顺序连续、无漏格、无重格，连同 skip_cells 恰好覆盖每格一次。片尾的平台账号页、关注页放进 skip_cells，只能是结尾连续的格。mode 只能为 video 或 image；refs 只用已提供且本段出现的照片 key，不得重复或编造。
gen_images 是实际提交的图片有序列表，1–4 项，只能用本段格 ID 或 refs 中的照片 key，不得重复。格 ID 对应第 04 步（对照和修改）里该格的新画面单格整帧图，不提交拼图。新画面是最直接的目标，同时给出新人物、新衣服、新商品、机位和状态；优先选能看清被替换人物和新商品的关键格，并按时间顺序排列。新画面看不清脸或商品细节时，再补对应照片。本段出现的每个被替换人物和新商品，都要由所选新画面或对应照片覆盖。refs 仍是第 04 步（对照和修改）生成新分镜需要的本段人物和商品照片，不受提交图数量限制。
时长与分段代价：每段生成时长为 4–15 的整数秒，生成时长 = max(4, ceil(原片跨度))。原片跨度会被均匀拉伸到生成时长，放慢倍数 = 生成时长 ÷ 原片跨度。原片跨度越短，尤其不足 4 秒时，放慢越明显，动作节奏会被拖慢。每段独立生成，段越多，人物外观、光线和画面在段与段之间越容易不一致；参考视频和输出都要计费。选分界时优先让每段原片跨度接近整数秒、不少于 4 秒。短段只有在有具体理由且不能并入相邻段时才单独成段，例如地点、人物或商品状态的硬变化；理由写进 why_segments。
中文字段里的格号写成“第 N 格”，N 是全片格序号；cells 和 skip_cells 仍用实际格 ID。商品剧情检查以第 03 步回答为准，不确定的内容写进 questions，不把它当已确认能力。
只输出 JSON：{"summary":"中文一句话","why_segments":"中文一句白话：为什么是这几段","skip_cells":[],"questions":[],"segments":[{"id":"d01","cells":["c01","c02","c03"],"title":"中文标题","what":"拍什么，1–2 句中文","mode":"video","mode_reason":"中文一句","refs":["C1"],"gen_images":["c01","C1"]}]}'''

SPEECH_RULES = '有口播时，以理顺后的 speech_lines 为台词依据，台词带镜头；分段的分界只能放在标为“句间”的切点，优先选择原片停顿更长的切点；“句中”的切点不能作为分界。仍须遵守格数、时长和连续覆盖限制。'
SYSTEM = SILENT_SYSTEM + '\n' + SPEECH_RULES


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _fail(reason):
    raise ValueError(INCOMPLETE + reason)


def _file(directory, file):
    path = (directory / str(file or '')).resolve()
    if not path.is_relative_to(directory.resolve()) or not path.is_file():
        raise ValueError('原片分镜或参考照片不存在，请重新生成或选择')
    return path


def _hash(directory, file):
    try:return store.sha256_file(_file(directory, file))
    except (ValueError, OSError):return 'missing'


def material_options(data, directory):
    rep = data.get('replacements') or {}
    people = copy.deepcopy((data.get('breakdown') or {}).get('characters') or [])
    people.extend(dict(id=row['source_id'], name=row.get('name', '')) for row in rep.get('characters', []) if row.get('action')=='add')
    names = {row['id']:row.get('name', '') for row in people}
    roles = scene_prompt.role_names({'breakdown':{'characters':people}})
    entries = [row for row in rep.get('characters', []) if row.get('action') in ('replace', 'add')]
    if (rep.get('product') or {}).get('action') == 'replace':
        entries.append({**rep['product'], 'source_id':'product'})
    result=[]
    for row in entries:
        key=row['source_id'];name='商品' if key=='product' else (key+' '+names.get(key,'')).strip()
        try:file=cast._photo(data,row,directory).relative_to(directory.resolve()).as_posix()
        except (ValueError,OSError):file=''
        result.append(dict(key=key,name=name,label=name,asset_id=row.get('asset_id'),id=row.get('asset_id'),
                           file=file,available=bool(file),role=roles.get(key,'product' if key=='product' else key),
                           description=row.get('description') or '',added=row.get('action')=='add'))
    return result


def inputs_sig(data, directory):
    board=data.get('storyboard') or {}
    originals={file:_hash(directory,file) for file in
               [c.get('file') for c in board.get('cells',[])]+[s.get('file') for s in board.get('sheets',[])] if file}
    source=data.get('source') or {}
    inputs = [board, originals, data.get('breakdown') or {},source,_hash(directory,source.get('file'))]
    if (data.get('breakdown') or {}).get('speech_lines'):
        inputs.append(_hash(directory, 'analysis/transcript.raw.json'))
    return 'source-v1:'+digest(inputs)


def _legacy_inputs_current(data,directory):
    """Read-only compatibility for plans saved before source-only signatures."""
    plan=data.get('segments_plan') or {}
    board=data.get('storyboard') or {}
    originals={file:_hash(directory,file) for file in
               [c.get('file') for c in board.get('cells',[])]+[s.get('file') for s in board.get('sheets',[])] if file}
    photos=[{**m,'role':scene_prompt.signature_role_label(m['key'],m['role']),
             'sha256':_hash(directory,m['file'])} for m in material_options(data,directory)]
    if storyboard.status(data,directory)['stale']:
        return False
    if plan.get('inputs_sig')==digest([board,originals,data.get('breakdown') or {},data.get('replacements') or {},photos]):
        return True
    # Old casting can no longer reconstruct its old composite hash. The source
    # board binds the original video AND breakdown; segment sources bind cells.
    from . import segment_boards
    return bool(plan.get('inputs_sig') and plan.get('segments') and not storyboard.status(data,directory)['stale']
        and all((segment_boards.state_for(data,s['id']).get('source') or {}).get('source_sig')==
                segment_boards.source_sig(data,s['id'],directory) for s in plan['segments']))


def segment_photos(data, segment, directory):
    """The saved casting and cell contents are the sole photo authority."""
    cells={c['id']:c for c in (data.get('storyboard') or {}).get('cells',[])}
    rows=[cells.get(cid,{}) for cid in segment.get('cells',[])]
    order=list(dict.fromkeys(key for cell in rows for key in cell.get('characters',[])))
    if any(cell.get('product') or cell.get('product_later') for cell in rows):
        order.append('product')
    frozen=((data.get('derived_from') or {}).get('master_snapshot') or {}).get('boards',{}).get(segment.get('id'))
    if frozen:
        added={r.get('source_id') for r in (data.get('replacements') or {}).get('characters',[]) if r.get('action')=='add'}
        order.extend(key for key in frozen.get('material_keys',[]) if key in added and key not in order)
        # A kept master slot may now have a replacement even when old cell metadata
        # cannot locate it. clone_row records the conservative fallback in refs.
        original=((data.get('derived_from') or {}).get('master_snapshot') or {}).get('replacements') or {}
        kept={r.get('source_id') for r in original.get('characters',[]) if r.get('action')=='keep'}
        if (original.get('product') or {}).get('action')=='keep':kept.add('product')
        order.extend(key for key in segment.get('refs',[]) if key in kept and key not in order)
    photos={m['key']:m for m in material_options(data,directory) if m['asset_id']}
    return [{**photos[key],'enabled':True} for key in order if key in photos]


def generation_images(data, segment, directory):
    """Keep authored target cells, refresh photo keys, then fit full coverage into four."""
    options=segment_photos(data,segment,directory)
    for photo in options:
        if not photo['available']:_fail('参考照片不存在：'+photo['key'])
    photos=[m['key'] for m in options]
    seq=segment.get('cells') or []
    authored=segment.get('gen_images') or []
    targets=list(dict.fromkeys(key for key in authored if key in seq))
    ordered=list(dict.fromkeys([key for key in authored if key in targets or key in photos]+photos))
    cells={c['id']:c for c in (data.get('storyboard') or {}).get('cells',[])}
    required=set(photos)
    def coverage(keys):
        result=set(keys)&required
        for key in keys:
            if key in targets:
                result.update(cells.get(key,{}).get('characters') or [])
                # A product that appears later is absent from this still frame.
                if cells.get(key,{}).get('product'):result.add('product')
        return result
    if len(ordered)<=4:
        if not ordered:_fail(f'{segment.get("id", "")} 没有可提交的新画面或照片')
        return ordered
    candidates=[]
    # At most six authored cells. Photos are mandatory unless a retained new
    # frame already covers that identity; no arbitrary truncation can drop it.
    for count in range(min(4,len(targets))+1):
        for chosen in combinations(targets,count):
            missing=required-coverage(chosen)
            if len(chosen)+len(missing)>4:continue
            chosen_photos=[key for key in photos if key in missing]
            chosen_photos += [key for key in photos if key not in missing][:4-len(chosen)-len(chosen_photos)]
            selected=set(chosen)|set(chosen_photos)
            score=(len(chosen_photos),len(chosen),tuple(key in selected for key in targets))
            candidates.append((score,[key for key in ordered if key in selected]))
    if not candidates:_fail(f'{segment.get("id", "")} 四张图不足以覆盖本段人物和新商品，请调整本段新画面')
    return max(candidates,key=lambda candidate:candidate[0])[1]


def effective_segment(data, segment, directory):
    return {**segment,'refs':[m['key'] for m in segment_photos(data,segment,directory)],
            'gen_images':generation_images(data,segment,directory)}


def segment_sig(segment):
    """Director binding: generation mode still changes the prompt's inputs."""
    return digest({key:segment.get(key) for key in ('id','cells','mode','refs','source_start','source_end')})


def visual_segment_sig(data, segment):
    """Keep the source's visual binding across mode changes, including old saves.

    Old boards, photo overrides and cell revisions embed the mode-sensitive hash
    (sometimes inside another hash). Retain that exact anchor only if every
    visual field still matches with one of the two legal historical modes. A
    new/changed source starts with the existing hash format; reads never migrate
    projects or rewrite frozen versions and revision inputs.
    """
    state=((data.get('segment_boards') or {}).get('segments') or {}).get(segment.get('id')) or {}
    saved=(state.get('source') or {}).get('segment_sig')
    if saved and any(saved==segment_sig({**segment,'mode':mode}) for mode in ('video','image')):
        return saved
    return segment_sig(segment)


def get_segment(data, segment_id):
    segment=next((s for s in (data.get('segments_plan') or {}).get('segments',[]) if s.get('id')==segment_id),None)
    if segment is None:raise ValueError('先在第 04 步（对照和修改）生成分段方案')
    return segment


def status(data, directory):
    plan=data.get('segments_plan') or {}
    legacy=bool(plan and any('gen_images' not in row for row in plan.get('segments',[])))
    matches=plan.get('inputs_sig')==inputs_sig(data,directory)
    if plan and not matches and not str(plan.get('inputs_sig') or '').startswith('source-v1:'):
        matches=_legacy_inputs_current(data,directory)
    stale=bool(plan and (legacy or not matches))
    result = dict(stale=stale,needs_generate=not plan or stale,warning=LEGACY_WARNING if legacy else STALE_WARNING if stale else '')
    if (data.get('breakdown') or {}).get('speech_lines'):
        result['speech_warnings'] = speech_warnings(data, plan)
    return result


def cut_speech(data, cut, directory=None, *, word_rows=None):
    """Classify the original clock, including cuts within a spoken word."""
    lines = (data.get('breakdown') or {}).get('speech_lines') or []
    result = dict(cut=cut, speech='无口播')
    if not lines:
        return result
    if word_rows is None:
        from . import speech_lines
        try: word_rows = speech_lines.words(data, directory)
        except (ValueError, OSError): word_rows = []
    for index, line in enumerate(lines, 1):
        if float(line['start']) < cut < float(line['end']):
            result.update(speech='句中', line=index)
            following = next((i for i, word in enumerate(word_rows) if word['end'] > cut), len(word_rows))
            pair = word_rows[max(0, following-1):following+1]
            result['between'] = [word['word'] for word in pair]
            return result
    previous = next((line for line in reversed(lines) if float(line['end']) <= cut), None)
    following = next((line for line in lines if float(line['start']) >= cut), None)
    result.update(speech='句间', pause=round(max(0, float(following['start'])-float(previous['end'])), 3)
                  if previous and following else 0)
    return result


def speech_warnings(data, plan):
    result = []
    segments = (plan or {}).get('segments') or []
    for index, segment in enumerate(segments[:-1], 1):
        if cut_speech(data, segment['source_end'], word_rows=[])['speech'] == '句中':
            result.append(f'第 {index} 段和第 {index+1} 段的分界落在一句台词中间，拼接处台词可能不连贯；可以点“重新生成分段方案”再试。')
    return result


def _cn(text, numbers):
    if not isinstance(text,str):return ''
    if not numbers:return text
    ids='|'.join(re.escape(cid) for cid in sorted(numbers,key=len,reverse=True))
    token=rf'(?<![A-Za-z0-9_])({ids})(?![A-Za-z0-9_])'
    def replace(match):
        first,last=match.groups()
        label=str(numbers[first])+(f'–{numbers[last]}' if last is not None else '')
        return f'第 {label} 格'
    return re.sub(token+rf'(?:\s*[至–-]\s*{token})?',replace,text)


def _normalize_keys(keys, available):
    # Prefer an exact real key, then tolerate surrounding whitespace. Never
    # reorder distinct materials: their order defines the prompt's 图N labels.
    return list(dict.fromkeys(key if key in available else key.strip() for key in keys))


def validate_gen_images(segment, data):
    """Validate submitted full-frame targets/photos independently of Step05 identity."""
    sid=segment.get('id',''); seq=segment.get('cells') or []; refs=segment.get('refs') or []
    images=segment.get('gen_images')
    if not isinstance(images,list) or any(not isinstance(key,str) for key in images):
        _fail(f'{sid} gen_images 必须是图片 key 列表')
    images = _normalize_keys(images, set(seq) | set(refs))
    if not 1<=len(images)<=4:
        _fail(f'{sid} gen_images 必须提供 1–4 张图')
    if any(key not in seq and key not in refs for key in images):
        _fail(f'{sid} gen_images 只能使用本段格号或 refs 中的照片')
    cells={c['id']:c for c in (data.get('storyboard') or {}).get('cells',[])}
    replaced={r['source_id'] for r in (data.get('replacements') or {}).get('characters',[])
              if r.get('action') in ('replace','add')}
    required=set().union(*(set(cells.get(cid,{}).get('characters') or []) for cid in seq)) & replaced
    has_product=any(cells.get(cid,{}).get('product') or cells.get(cid,{}).get('product_later') for cid in seq) or 'product' in refs
    if (data.get('replacements') or {}).get('product',{}).get('action')=='replace' and has_product:
        required.add('product')
    covered=set(images) & set(refs)
    for cid in images:
        if cid in seq:
            covered.update(cells.get(cid,{}).get('characters') or [])
            if cells.get(cid,{}).get('product'):covered.add('product')
    missing=required-covered
    if missing:
        names={r['id']:r.get('name','') for r in (data.get('breakdown') or {}).get('characters',[])}
        labels=['新商品' if key=='product' else (key+' '+names.get(key,'')).strip() for key in sorted(missing)]
        _fail(f'{sid} 生成图缺少覆盖：'+ '、'.join(labels))
    return list(images)


def validate_plan(value, data, directory, *, assign_ids=False):
    # Model replies get sequential IDs; validating persisted plans (PUT) keeps
    # their existing IDs, including aliases created before stable numbering.
    if not isinstance(value,dict) or not isinstance(value.get('segments'),list) or not value['segments']:
        _fail('没有有效段落')
    cells=(data.get('storyboard') or {}).get('cells') or []
    expected=[c['id'] for c in cells];byid={c['id']:c for c in cells};numbers={cid:i+1 for i,cid in enumerate(expected)}
    if not expected or len(byid)!=len(cells):_fail('原片分镜不存在或格号重复')
    skip=value.get('skip_cells',[])
    if not isinstance(skip,list) or any(not isinstance(cid,str) for cid in skip) or (skip and skip!=expected[-len(skip):]):
        _fail('skip_cells 只能是结尾连续的格子')
    generated=expected[:-len(skip)] if skip else expected
    available={m['key'] for m in material_options(data,directory) if m['available']}
    ids=set();covered=[];segments=[]
    for index,row in enumerate(value['segments'],1):
        if not isinstance(row,dict):_fail('段落格式不正确')
        sid=f'd{index:02d}' if assign_ids else row.get('id')
        if not isinstance(sid,str) or not store.SAFE_ID.fullmatch(sid) or sid in ids:_fail('段落 ID 必须唯一且安全')
        ids.add(sid);seq=row.get('cells')
        if not isinstance(seq,list) or not seq or any(not isinstance(cid,str) or cid not in byid for cid in seq):_fail('段落格号不存在或为空')
        if len(seq)>6:_fail(f'{sid} 超过 6 格')
        if seq!=generated[len(covered):len(covered)+len(seq)]:_fail('所有段落必须连续、按顺序覆盖每格恰好一次')
        covered.extend(seq)
        mode = row.get('mode')
        if isinstance(mode, str):mode = mode.strip().lower()
        if mode not in ('video','image'):_fail(f'{sid} 的 mode 只能是 video 或 image')
        refs=row.get('refs')
        if not isinstance(refs,list) or any(not isinstance(key,str) for key in refs):
            _fail(f'{sid} 参考照片必须是照片 key 列表')
        refs = _normalize_keys(refs, available)
        if any(key not in available for key in refs):_fail(f'{sid} 的参考照片不存在')
        gen_images = validate_gen_images({**row, 'refs': refs}, data)
        try:
            start,end=float(byid[seq[0]]['start']),float(byid[seq[-1]]['end']);seconds=end-start
            valid=math.isfinite(start) and math.isfinite(end) and start>=0 and seconds>0
            for cid in seq:
                a,b=float(byid[cid]['start']),float(byid[cid]['end'])
                valid=valid and math.isfinite(a) and math.isfinite(b) and start<=a<b<=end
        except (TypeError,ValueError,KeyError,OverflowError):valid=False
        if not valid:_fail(f'{sid} 的原片时间无效')
        if seconds>15:_fail(f'{sid} 原片跨度超过 15 秒')
        duration=max(4,min(15,math.ceil(seconds)))
        item={key:_cn(row.get(key),numbers) for key in ('title','what','mode_reason')}
        item.update(id=sid,cells=list(seq),mode=mode,refs=list(refs),gen_images=gen_images,source_start=start,source_end=end,
                    source_seconds=seconds,duration=duration,target_seconds=duration,stretch=duration/seconds)
        segments.append(item)
    if covered!=generated:_fail('所有段落必须连续、按顺序覆盖每格恰好一次（含 skip_cells）')
    questions=value.get('questions') or []
    return dict(format_version=2,summary=_cn(value.get('summary'),numbers),why_segments=_cn(value.get('why_segments'),numbers),
                skip_cells=list(skip),questions=[_cn(q,numbers) for q in questions if isinstance(q,str)] if isinstance(questions,list) else [],segments=segments)


def messages(data, directory):
    board=data.get('storyboard') or {};pack=data.get('breakdown') or {};context=[]
    for i,c in enumerate(board.get('cells',[]),1):
        product=storyboard.product_at(c['frame_at'],c['end'],pack) if 'frame_at' in c else c
        text=storyboard.cell_text(c,pack)
        context.append({'id':c['id'],'序号':i,'start':c['start'],'end':c['end'],
                        '拆解识别的人物（可能混入相邻镜头）':c.get('characters',[]),
                        '拍摄瞬间的商品状态':product.get('product',''),'动作':text['action'],'note':text['note']})
    parts=[dict(type='text',text='完整拆解：'+json.dumps({k:pack.get(k) for k in ('overview','beats','dialogue','productMoments','characters','scene','sound')},ensure_ascii=False)+
                '\n每格信息：'+json.dumps(context,ensure_ascii=False)+'\n第 03 步选角和商品设定（含商品剧情检查回答）：'+json.dumps(data.get('replacements') or {},ensure_ascii=False)+
                '\n当前通道每段可选的生成时长为 4–15 秒（最短 4 秒、最长 15 秒，均为整数秒）。')]
    if pack.get('speech_lines'):
        from . import speech_lines
        try: words = speech_lines.words(data, directory)
        except (ValueError, OSError): words = []
        cuts = [cut_speech(data, c['start'], word_rows=words) for c in board.get('cells', [])[1:]]
        parts[0]['text'] += ('\nspeech_lines（理顺后的台词）：' + json.dumps(pack['speech_lines'], ensure_ascii=False)
                             + '\n格子切点的口播状态：' + json.dumps(cuts, ensure_ascii=False))
    for sheet in board.get('sheets',[]):
        parts.append(dict(type='text',text='整片原片分镜总览（带格号）：'+sheet['id']))
        parts+=llm.image_part(_file(directory,sheet.get('file')))
    for i,c in enumerate(board.get('cells',[]),1):
        parts.append(dict(type='text',text=f"第 {i} 格（{c['id']}）原片画面，{c['start']:g}–{c['end']:g} 秒"))
        parts+=llm.image_part(_file(directory,c.get('file')))
    for photo in material_options(data,directory):
        parts.append(dict(type='text',text='照片：'+json.dumps(photo,ensure_ascii=False)))
        if photo['available']:parts+=llm.image_part(_file(directory,photo['file']))
    return [dict(role='system',content=SYSTEM if pack.get('speech_lines') else SILENT_SYSTEM),dict(role='user',content=parts)]


def _save_response(pid, value, *, raw=False):
    directory=store.project_dir(pid);folder=directory/'analysis/segments/responses';folder.mkdir(parents=True,exist_ok=True)
    while True:
        path=folder/f'{datetime.now().astimezone():%Y%m%dT%H%M%S.%f}-full.json'
        try:
            with path.open('x',encoding='utf-8') as stream:stream.write(value if raw else json.dumps(value,ensure_ascii=False,indent=2))
            return path.relative_to(directory).as_posix()
        except FileExistsError:continue


def generate(pid):
    from . import segment_boards, speech_lines
    data=store.load(pid);directory=store.project_dir(pid)
    segment_boards.require_idle(data)
    if not (data.get('storyboard') or {}).get('cells') or not data['storyboard'].get('sheets'):
        raise ValueError('先在第 02 步生成原片分镜')
    # Explicit button action only: neither views nor server startup may migrate.
    speech_source = speech_lines.source_fingerprint(data, speech_lines.read_transcript(directory))
    data = speech_lines.ensure(pid)
    if speech_lines.source_fingerprint(data, speech_lines.read_transcript(directory)) != speech_source:
        raise ValueError('原片或拆解刚刚改过，请完成后重新生成分段方案')
    sig=inputs_sig(data,directory);old=copy.deepcopy(data.get('segments_plan'));responses=[]
    def capture(content):
        path=_save_response(pid,content or '',raw=True);responses.append(path)
        try:llm.extract_json(content or '')
        except JSONResponseError:store.log(pid,INCOMPLETE+'模型没有返回完整 JSON；模型原始回复：'+path,'error')
    try:
        request = messages(data, directory)
        try:value,_=gateway.reason_checked('segment_plan',request,project=pid,max_tokens=8000,on_response=capture)
        except JSONResponseError:_fail('模型没有返回完整 JSON，请重试')
        except CheckedLLMError as exc:
            if 'JSON' in str(exc).upper():_fail('模型没有返回完整 JSON，请重试')
            raise
        if not responses:responses.append(_save_response(pid,value))
        plan=validate_plan(value,data,directory,assign_ids=True)
        warnings = speech_warnings(data, plan)
        if warnings:
            retry = request + [dict(role='assistant', content=json.dumps(value, ensure_ascii=False)),
                               dict(role='user', content='请重写分段方案：'+'\n'.join(warnings)+'\n只在句间切点分段，返回完整 JSON。')]
            count = len(responses)
            try:
                value, _ = gateway.reason_checked('segment_plan', retry, project=pid, max_tokens=8000, on_response=capture)
                if len(responses) == count: responses.append(_save_response(pid, value))
                plan = validate_plan(value, data, directory, assign_ids=True)
            except (ValueError, CheckedLLMError, JSONResponseError, OSError):
                store.log(pid, '口播分界自动重写没完成，已保留首个可用分段方案，请留意分界提示。', 'warn')
        for row in plan['segments']:
            row['mode_suggested']=row['mode']
        plan.update(inputs_sig=sig,generated_at=datetime.now().astimezone().isoformat())
        plan['generation_id']=plan['generated_at']
        def commit(current):
            segment_boards.require_idle(current)
            if inputs_sig(current,directory)!=sig or current.get('segments_plan')!=old:
                raise ValueError('内容刚刚改过，请重新生成分段方案')
            states=segment_boards.reuse_for_plan(current,plan,directory)
            current['segments_plan']=plan
            current['segment_boards']={'segments':states}
            current['segment_boards']={'segments':segment_boards.build_sources(current,directory)}
        store.update(pid,commit);store.log(pid,'分段方案已生成')
        return store.load(pid)
    except (ValueError,CheckedLLMError) as exc:
        if responses:store.log(pid,f'{exc}；模型原始回复：{responses[-1]}','error')
        raise


def save(pid, segment_id, body):
    from . import segment_boards
    if not isinstance(body,dict) or any(k not in ('mode','refs','id','segment_id','action') for k in body):
        raise ValueError('这里只能调整这一段的生成方式和参考照片')
    with store._lock(pid):
        data=store.load(pid);directory=store.project_dir(pid);old=data.get('segments_plan')
        if not old:raise ValueError('先在第 04 步（对照和修改）生成分段方案')
        candidate=copy.deepcopy(old)
        # Freeze legacy recommendations before applying the first user override.
        for row in candidate['segments']:
            row.setdefault('mode_suggested',row['mode'])
        segment=get_segment({'segments_plan':candidate},segment_id)
        previous_mode=segment['mode']
        for key in ('mode','refs'):
            if key in body:segment[key]=body[key]
        if 'refs' in body:
            refs=body['refs']
            available={m['key'] for m in material_options(data,directory) if m['available']}
            if not isinstance(refs,list) or any(not isinstance(key,str) for key in refs):
                _fail('参考照片必须是照片 key 列表')
            segment['refs']=_normalize_keys(refs,available)
            if any(key not in available for key in segment['refs']):_fail('参考照片不存在')
        live={**candidate,'segments':[effective_segment(data,s,directory) for s in candidate['segments']]}
        checked=validate_plan(live,data,directory)
        for row,saved in zip(checked['segments'],candidate['segments']):
            row.update(refs=copy.deepcopy(saved['refs']),gen_images=copy.deepcopy(saved['gen_images']),
                       mode_suggested=saved['mode_suggested'])
        checked.update(inputs_sig=old.get('inputs_sig'),generated_at=old.get('generated_at'),edited_at=datetime.now().astimezone().isoformat())
        if 'generation_id' in old:checked['generation_id']=old['generation_id']
        mode_changed=get_segment({'segments_plan':checked},segment_id)['mode']!=previous_mode
        def commit(current):
            current['segments_plan']=checked
            current['segment_boards']={'segments':segment_boards.build_sources(current,directory)}
            if mode_changed:
                # A round trip must not silently revive a prompt or paid preview.
                # Keep authored text, selected/completed runs and all image state.
                for row in (current.get('director') or {}).get('segments',[]):
                    if row.get('id')==segment_id:row.update(inputs_sig=None,plan_sig=None)
                for run in (current.get('runs') or {}).get(segment_id,[]):
                    if run.get('state')=='pending_confirmation':run['state']='superseded'
        return store.update(pid,commit)
