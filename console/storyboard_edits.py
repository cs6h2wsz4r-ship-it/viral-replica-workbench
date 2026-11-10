"""Per-sheet edit plans and explicit preview/confirmation handoff to the image bridge."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from PIL import Image
from . import cast, gateway, llm, scene_prompt, store, storyboard, storyboard_images, segment_plan, segment_boards
from .channel_text import user_message
from .gateway.errors import CheckedLLMError, JSONResponseError

INCOMPLETE = '模型给的方案不完整，请重试'
SETTINGS_WARNING = '选角或商品改过，改图方案可能要重新生成'
PLAN_WARNING = '改图方案改过，当前分镜还是按旧方案生成的'
SOURCE_WARNING = '原片分镜变了，这里的改图结果对不上格号，需要重新生成方案'
SINGLE_INSTANT_RULE = '每一格是一张静止画面，只画一个瞬间：原片这一格 frame_at 截帧那一刻。同一个人在一格里只出现一次，不能为表现前后动作画出同一人的两个分身。这一格时段里之后才发生的动作不写进 changes，交给视频去演。以原片这一格截帧里实际可见的人数为准，有几个人就画几个人，不把时段人物列表当作同一时刻的人数。'
SYSTEM = '''你是短视频复刻的分镜改图师。只按用户需求和素材改，没有提到的东西都保持原片。
动笔前先针对这条片子分析风险：新旧商品在功能和状态上的差异会带来哪些画面变化；原片哪些时间点、台词或反应和新剧情不合；哪些画面容易漏出原片的人、旧商品、旧包装或文字。分析结论落实到本步每格的改法和保持项里，不单独输出分析过程。
优先级从高到低：
① 人和商品换干净：原片人物的脸、发型、衣服和发饰配饰，旧商品、旧包装和字幕都不出现，新商品的外观和用法正确。
② 剧情逻辑通顺：新商品按它自己的真实功能和使用方式表现，不照搬原片旧商品的外观和状态；旧商品没有、新商品具备的状态，在剧情需要时按常识表现。这不算编造功能；界面上的具体文字不要写。台词和反应放在剧情上说得通的时刻；原片的时间点与画面剧情不合时，移到合理的格子，并在 summary 里用一句话说明。
③ 动作、机位、镜头顺序和节奏对上原片。
④ 新衣服的款式细节、配饰、背景小物权重最低，不必与参考照完全一致，不为它们写约束、不提问。
拼图里的每一格都要列出来，不改的格 changes 留空。changes 和 keep 必须是字符串。
changes 写成具体、可执行的中文：换谁、换成哪张图里的人、商品换成什么、怎么使用，以及画面文字的处理。
keep 写这一格必须保持的机位、构图、姿势、场地和光线。
被替换的人物默认穿人物参考照里的衣服；参考照看不出衣服时，换成与原片明显不同、符合人物和场景的衣服。原片人物的衣服、发饰和配饰不保留：它们是原人物的身份线索，留下会让视频模型把原人物带回来。用户明确要求保留原衣服时照做。新衣服的细节不必和参考照完全一致（优先级④）。不同人物不能合并成同一个人。
被替换的人物只露出手、手臂、腿、背影或衣角的格子，也算这个人出现了。changes 要写明改成新人物，不能留空；肤色对应新人物，袖口换成新人物的衣服，细节不必与参考照完全一致。
商品按真实用法使用或摆放；展示时，商品的正面或功能面朝向镜头。新商品要处于剧情需要的状态，按优先级②处理。结合剧情检查的问题和回答，不推测未确认的功能。
改图只针对拼图里这一格的画面；格子信息里“后半段出现的商品”不在画面里，不要画进去。
用户需求含糊、或格子里认不出是谁时，写进 questions，不要猜。
只输出 JSON：{"summary":"一句话总结","cells":[{"cell":"c01","changes":"具体改法或空字符串","keep":"必须保持的东西"}],"remove_text":true,"questions":[]}'''
SYSTEM += '\n' + SINGLE_INSTANT_RULE

CELL_REVISION_SYSTEM = '''你是单格分镜改图师，把用户要求整理成这一格唯一的新方案。只输出 JSON，不执行生图。
用户的要求和原方案冲突时，以用户为准；删除被用户纠正的旧要求，不要把互相冲突的要求并列保留。
用户描述的是问题时，要转成具体画法：结合当前新图和原片截帧说明保留哪个主体、删除哪个多余主体、怎样改画以及怎样补齐背景，不能只复述问题。
遵守四级优先级：① 替换人物和商品准确、原片身份和旧商品不漏回、新商品外观和用法正确；② 剧情和商品状态合理；③ 动作、机位、镜头顺序和节奏符合原片；④ 新衣服细节、配饰、背景小物优先级最低，不为这些细节增加约束。
原片截帧只用来理解人数、瞬间动作、机位和构图。被替换人物的身份以人物新照片为准，不把原片人物的外貌、衣服、发饰或配饰等身份线索写进新方案；用户明确要求保留的项目除外。人物用素材标识或新照片指代，不用原片身份线索识别人。商品以商品照片和已确认描述为准。
changes 写完整、具体、可执行的新画法，keep 只写与新画法不冲突的保持项。不得要求参考原片图、推理输入的图号或旧方案；生图时只会收到当前这一格与人物、商品照片，用素材标识指代照片。why 用一句中文白话说明按用户要求改了什么。
只输出：{"changes":"这一格的新画法","keep":"不冲突的保持项","why":"按你的要求改了什么"}。'''
CELL_REVISION_SYSTEM += '\n' + SINGLE_INSTANT_RULE


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def sheet_cells(data, sid):
    if segment_boards.has_segment(data,sid):return segment_boards.sheet_cells(data,sid)
    board=data.get('storyboard') or {}
    sheet=next((s for s in board.get('sheets',[]) if s['id']==sid),None)
    if not sheet:raise ValueError('先在第 02 步生成原片分镜')
    byid={c['id']:c for c in board['cells']}
    return sheet,[byid[cid] for cid in sheet['cells']]


def board_sig(data, sid):
    try:
        if segment_boards.has_segment(data,sid):return segment_boards.board_sig(data,sid)
        _,cells=sheet_cells(data,sid)
        return digest([data['storyboard'].get('built_at'),data['storyboard'].get('source_sig'),
            [{k:c.get(k) for k in ('id','start','end','frame_at','file')} for c in cells]])
    except ValueError:return 'missing'


def state_for(data,sid):
    if segment_boards.has_segment(data,sid):return segment_boards.state_for(data,sid)
    return (data.get('storyboard_edits') or {}).get('sheets',{}).get(sid) or {}


def _states(data,sid):
    if segment_boards.has_segment(data,sid):return data.setdefault('segment_boards',{}).setdefault('segments',{})
    return data.setdefault('storyboard_edits',{}).setdefault('sheets',{})


def _folder(data,sid,directory):
    return directory/(f'analysis/segments/{sid}/edits' if segment_boards.has_segment(data,sid) else 'analysis/storyboard/edits')


def _layout(data,sid):
    return segment_boards.layout(len(segment_plan.get_segment(data,sid)['cells'])) if segment_boards.has_segment(data,sid) else (3,3)


def material_options(data,sid,directory):
    if segment_boards.has_segment(data,sid):
        return [{k:m[k] for k in ('key','name','asset_id','file','description','added','enabled')}
                for m in segment_plan.segment_photos(data,segment_plan.get_segment(data,sid),directory)]
    return _legacy_material_options(data,sid,directory)


def _legacy_material_options(data,sid,directory):
    """Historical shape, used only for old sheet flows and signature comparison."""
    _,cells=sheet_cells(data,sid)
    rep=data.get('replacements') or {}; names={c['id']:c.get('name','') for c in (data.get('breakdown') or {}).get('characters',[])}
    order=list(dict.fromkeys(cid for c in cells for cid in c.get('characters',[])))
    entries=[c for c in rep.get('characters',[]) if c.get('action') in ('replace','add')]
    entries.sort(key=lambda c:order.index(c['source_id']) if c.get('source_id') in order else len(order))
    product=rep.get('product') or {}
    if product.get('action')=='replace':entries.append({**product,'source_id':'product'})
    options=[]
    for entry in entries:
        try:path=cast._photo(data,entry,directory)
        except (ValueError,OSError):continue
        key=entry['source_id'];product=key=='product';added=entry.get('action')=='add'
        enabled=(key in segment_plan.get_segment(data,sid)['refs']) if segment_boards.has_segment(data,sid) else (any(c.get('product') for c in cells) if product else added or key in order)
        options.append(dict(key=key,name='商品' if product else (key+' '+(entry.get('name') or names.get(key,''))).strip(),
            asset_id=entry['asset_id'],file=str(path.relative_to(directory.resolve())),description=entry.get('description',''),
            added=added,enabled=bool(enabled)))
    return options


def materials_for(data,sid,directory,supplied=None):
    stored=state_for(data,sid).get('materials',{}) if supplied is None else supplied
    if supplied is None and segment_boards.has_segment(data,sid) and state_for(data,sid).get('materials_segment_sig')!=segment_plan.visual_segment_sig(data,segment_plan.get_segment(data,sid)):
        stored={}
    if not isinstance(stored,dict):raise ValueError('照片开关格式不正确')
    options=material_options(data,sid,directory)
    if supplied is None and segment_boards.has_segment(data,sid):
        state=state_for(data,sid);saved=state.get('materials_photos')
        fingerprints=photo_fingerprints(data,sid,directory)
        if saved is not None:
            stored={key:value for key,value in stored.items() if saved.get(key)==fingerprints.get(key)}
        elif state.get('settings_sig')!=_legacy_settings_sig(data,sid,directory,stored):
            stored={}
        else:
            # In V5 a False outside refs was the old automatic default, not
            # evidence of a user override. This is the reported wrong-image case.
            old_defaults=set(segment_plan.get_segment(data,sid).get('refs') or [])
            stored={key:value for key,value in stored.items() if value or key in old_defaults}
    return {m['key']:bool(stored.get(m['key'],m['enabled'])) for m in options}


def photo_fingerprints(data,sid,directory):
    return {m['key']:digest([m['asset_id'],m['file'],store.sha256_file(directory/m['file'])])
            for m in material_options(data,sid,directory)}


def settings_sig(data,sid,directory,materials,*,_legacy_fission=False):
    selected=[]
    for m in material_options(data,sid,directory):
        if segment_boards.has_segment(data,sid) and not materials.get(m['key']):continue
        selected.append({**m,'hash':store.sha256_file(directory/m['file'])})
    inputs=[selected,materials,(data.get('replacements') or {}).get('product',{}).get('story_checks',[])]
    from . import fission
    frozen=fission.snapshot(data,sid)
    if frozen:
        master=(data['derived_from'].get('master_snapshot') or {})
        old_materials=master.get('materials') or {}
        files=[frozen.get('file'),frozen.get('clean_file'),*frozen.get('cells',{}).values(),
               *[item.get('file') for item in old_materials.values()]]
        changes=fission.changes(data,directory)
        if not _legacy_fission:
            for change in changes.values():
                cameras=change['after'].get('camera_assets')
                if isinstance(cameras,dict) and not any(cameras.values()):change['after'].pop('camera_assets')
        inputs.append([frozen,old_materials,master.get('replacements'),changes,
                       [(file,segment_plan._hash(directory,file)) for file in files]])
    return digest(inputs)


def _legacy_settings_sig(data,sid,directory,materials):
    selected=[{**m,'hash':store.sha256_file(directory/m['file'])}
              for m in _legacy_material_options(data,sid,directory) if materials.get(m['key'])]
    return digest([selected,materials,(data.get('replacements') or {}).get('product',{}).get('story_checks',[])])


def settings_match(previous,data,sid,directory,materials,old_materials=None):
    if previous==settings_sig(data,sid,directory,materials):return True
    if data.get('derived_from') and previous==settings_sig(data,sid,directory,materials,_legacy_fission=True):return True
    if not segment_boards.has_segment(data,sid):return False
    old_materials=state_for(data,sid).get('materials',{}) if old_materials is None else old_materials
    return ({key for key,value in old_materials.items() if value}=={key for key,value in materials.items() if value}
            and previous==_legacy_settings_sig(data,sid,directory,old_materials))


def plan_sig(plan,materials):
    return digest([[{'cell':c['cell'],'changes':c['changes'],'keep':c['keep']} for c in plan.get('cells',[])],plan.get('remove_text'),materials])


def request_template(data,sid,materials,directory):
    texts=[]
    for m in material_options(data,sid,directory):
        if not materials.get(m['key']):continue
        if m['key']=='product':texts.append('商品换成'+(m['description'] or '照片里的商品')+'，按真实用法使用')
        elif m['added']:texts.append('加上 '+m['name']+'，外形按照片：'+m['description'])
        else:texts.append(m['name']+'换成照片里的人，默认穿参考照里的衣服；看不出衣服时换成与原片明显不同且符合场景的衣服，用户明确要求保留原衣服时照做'+('；描述：'+m['description'] if m['description'] else ''))
    return '；'.join(texts+['去掉原片画面上的字幕和表情。'])


def validate_plan(value,cells):
    if not isinstance(value,dict) or not isinstance(value.get('summary'),str) or type(value.get('remove_text')) is not bool or not isinstance(value.get('questions'),list) or any(not isinstance(q,str) for q in value['questions']):raise ValueError(INCOMPLETE)
    rows=value.get('cells');expected=[c['id'] for c in cells]
    if not isinstance(rows,list) or len(rows)!=len(expected) or any(not isinstance(c,dict) or not isinstance(c.get('cell'),str) or any(not isinstance(c.get(k),str) for k in ('changes','keep')) for c in rows):raise ValueError(INCOMPLETE)
    if sorted(c['cell'] for c in rows)!=sorted(expected):raise ValueError(INCOMPLETE)
    byid={c['cell']:c for c in rows}
    return dict(summary=value['summary'],cells=[{k:byid[cid][k] for k in ('cell','changes','keep')} for cid in expected],remove_text=value['remove_text'],questions=list(value['questions']))


def frame_context(data, cell):
    context = dict(frame_at=cell.get('frame_at'), 截帧说明='图里这一格就是 frame_at 这一刻的画面，只画一个瞬间。',
                   动作=cell.get('action_full', cell.get('action', '')),
                   动作说明='这一格时段内的动作，供理解前后，不全部画进图里')
    # Preserve raw timing evidence; never turn a sequence into an invented pose.
    at = cell.get('frame_at')
    if isinstance(at, (int, float)) and not isinstance(at, bool):
        observations = (data.get('breakdown') or {}).get('actions') or []
        actions = [r['action'] for r in observations if isinstance(r, dict)
                   and r.get('frame_at') == at and isinstance(r.get('action'), str) and r['action'].strip()]
        if actions: context['截帧这一刻的动作'] = '；'.join(dict.fromkeys(actions))
        else:
            nearby=[]
            for row in (data.get('breakdown') or {}).get('shots') or []:
                if not isinstance(row,dict) or not isinstance(row.get('action'),str) or not row['action'].strip():continue
                start,end=storyboard._interval(row)
                if start<=at<end and end-start<cell['end']-cell['start']:
                    nearby.append(dict(start=start,end=end,action=row['action']))
            if nearby:
                context['截帧这一刻的动作']=nearby
                context['瞬间动作说明']='覆盖 frame_at 的较短拆解时段原文，仍可能包含前后动作；精确姿态以截帧图为准，只画这一刻。'
    return context


def plan_messages(data,sid,request,materials,directory):
    from . import fission
    if fission.snapshot(data,sid):
        return fission.plan_messages(data,sid,request,materials,directory)
    sheet,cells=sheet_cells(data,sid);names={c['id']:c.get('name','') for c in (data.get('breakdown') or {}).get('characters',[])}
    product_name=storyboard.product_names(data.get('breakdown') or {})['name']
    context=[]
    for c in cells:
        product=storyboard.product_at(c['frame_at'],c['end'],data.get('breakdown') or {}) if 'frame_at' in c else c
        context.append(dict(格号=c['id'],时间=f"{c['start']}–{c['end']} 秒",人物=[(cid+' '+names.get(cid,'')).strip() for cid in c.get('characters',[])],
            商品=product.get('product',''),后半段出现的商品=(product_name or '有，未记录商品名') if product.get('product_later') else '',
            **frame_context(data,c),画面文字=c.get('overlay','')))
    parts=[{'type':'text','text':'图1：带格号的原片分镜\n每格信息：'+json.dumps(context,ensure_ascii=False)}]+llm.image_part(directory/sheet['file'])
    n=2
    for m in material_options(data,sid,directory):
        if not materials.get(m['key']):continue
        parts.append({'type':'text','text':f"图{n}：{m['name']}的新照片\n第 03 步描述：{m['description']}"})
        parts+=llm.image_part(directory/m['file']);n+=1
    checks=(data.get('replacements') or {}).get('product',{}).get('story_checks',[])
    primary=request_template(data,sid,materials,directory)
    parts.append({'type':'text','text':'商品的剧情检查（问题和回答）：'+json.dumps(checks,ensure_ascii=False)+'\n第 03 步的设定（主要需求）：'+primary+'\n补充要求：'+(request or '无')})
    system=SYSTEM
    if segment_boards.has_segment(data,sid):
        cols,rows=_layout(data,sid)
        system+=f'\n本段共 {len(cells)} 格，{cols} 列 × {rows} 行。只修改本段，保留空白格和格子之间的留白，不增加格子。'
    return [{'role':'system','content':system},{'role':'user','content':parts}]


def _freeze_current_settings(data,sid,directory,state,materials):
    for version in state.get('versions',[]):
        if version.get('id')==state.get('current') and 'settings_sig' not in version:
            context=(data.get('images',{}).get(version.get('image_id'),{}).get('storyboard_context') or {})
            version['settings_sig']=context.get('settings_sig') or state.get('settings_sig')
            version['settings_materials']=copy.deepcopy(state.get('materials',{}))
    if settings_match(state.get('settings_sig'),data,sid,directory,materials):
        state['settings_sig']=settings_sig(data,sid,directory,materials)


def remember_photo_overrides(data,directory):
    """Bind legacy switches before an explicit Step03 save changes the photos."""
    for segment in (data.get('segments_plan') or {}).get('segments',[]):
        sid=segment['id'];state=state_for(data,sid)
        if not state or 'materials_photos' in state:continue
        try:
            materials=materials_for(data,sid,directory)
            _freeze_current_settings(data,sid,directory,state,materials)
            state.update(materials=materials,materials_photos=photo_fingerprints(data,sid,directory))
        except (ValueError,OSError,KeyError):
            # Missing old photos cannot establish an override for a new photo.
            state.update(materials={},materials_photos={})


def save(pid,sid,body):
    with store._lock(pid):
        data=store.load(pid);directory=store.project_dir(pid);_,cells=sheet_cells(data,sid)
        state=copy.deepcopy(state_for(data,sid));materials=materials_for(data,sid,directory,body.get('materials'))
        if segment_boards.has_segment(data,sid):
            old_materials=materials_for(data,sid,directory)
            _freeze_current_settings(data,sid,directory,state,old_materials)
            state['materials_segment_sig']=segment_plan.visual_segment_sig(data,segment_plan.get_segment(data,sid))
            state['materials_photos']=photo_fingerprints(data,sid,directory)
        state['materials']=materials
        if 'request' in body:
            if not isinstance(body['request'],str):raise ValueError('请写一下想怎么改')
            state['request']=body['request']
        if 'plan' in body:
            if not state.get('plan'):raise ValueError('请先生成改图方案')
            old=state['plan'];plan=validate_plan(body['plan'],cells)
            plan.update(generated_at=old['generated_at'],edited=old.get('edited',False) or plan!=validate_plan(old,cells))
            plan['sig']=plan_sig(plan,materials);state['plan']=plan
        elif state.get('plan'):
            state['plan']['sig']=plan_sig(state['plan'],materials)
        store.update(pid,lambda d:_states(d,sid).__setitem__(sid,state))
        return store.load(pid)


def generate_plan(pid,sid,body):
    request=body.get('request','')
    if not isinstance(request,str):raise ValueError('补充要求要填写文字')
    request=request.strip()
    save(pid,sid,{**{k:v for k,v in body.items() if k=='materials'},'request':request})
    data=store.load(pid);directory=store.project_dir(pid);_,cells=sheet_cells(data,sid)
    materials=materials_for(data,sid,directory);bsig=board_sig(data,sid);ssig=settings_sig(data,sid,directory,materials)
    try:
        try:value,_=gateway.reason_checked('storyboard_plan',plan_messages(data,sid,request,materials,directory),project=pid,max_tokens=6000)
        except (ValueError,JSONResponseError) as exc:raise ValueError(INCOMPLETE) from exc
        except CheckedLLMError as exc:
            if 'JSON' in str(exc):raise ValueError(INCOMPLETE) from exc
            raise
        plan=validate_plan(value,cells);plan.update(generated_at=datetime.now().astimezone().isoformat(),edited=False)
        plan['sig']=plan_sig(plan,materials)
        def commit(d):
            current=state_for(d,sid);current_materials=materials_for(d,sid,directory)
            if board_sig(d,sid)!=bsig or settings_sig(d,sid,directory,current_materials)!=ssig or current.get('request')!=request:raise ValueError('设定或需求刚刚改过，请重新生成方案')
            current.update(storyboard_sig=bsig,settings_sig=ssig,plan=plan,materials=materials,request=request)
            if segment_boards.has_segment(d,sid):current['segment_sig']=segment_plan.visual_segment_sig(d,segment_plan.get_segment(d,sid))
        store.update(pid,commit);store.log(pid,f'改图方案 {sid} 已生成')
        return store.load(pid)
    except Exception as exc:
        store.log(pid,'改图方案没完成：'+user_message(exc),'warn');raise


def warnings(data,sid,directory):
    state=state_for(data,sid);out=[]
    if not state.get('plan'):return out
    current=next((v for v in state.get('versions',[]) if v['id']==state.get('current')),None)
    if segment_boards.has_segment(data,sid):
        sig=segment_plan.visual_segment_sig(data,segment_plan.get_segment(data,sid))
        if state.get('segment_sig')!=sig or (current and current.get('segment_sig',state.get('segment_sig'))!=sig):out.append(segment_boards.STALE_WARNING)
        source=state.get('source') or {}
        if source.get('source_sig')!=segment_boards.source_sig(data,sid,directory):out.append(SOURCE_WARNING)
    if state.get('storyboard_sig')!=board_sig(data,sid) or (current and current.get('storyboard_sig',state.get('storyboard_sig'))!=board_sig(data,sid)):out.append(SOURCE_WARNING)
    else:
        materials=materials_for(data,sid,directory)
        if not settings_match(state.get('settings_sig'),data,sid,directory,materials):out.append(SETTINGS_WARNING)
    if current and current.get('plan_sig')!=state['plan'].get('sig'):out.append(PLAN_WARNING)
    return out


def view(data,directory):
    result={}
    sheets=segment_boards.sheets(data) if data.get('segments_plan') else (data.get('storyboard') or {}).get('sheets',[])
    for sheet in sheets:
        sid=sheet['id']
        try:
            materials=materials_for(data,sid,directory)
            notices=warnings(data,sid,directory)
            result[sid]=dict(materials=material_options(data,sid,directory),active_materials=materials,warnings=notices,source_stale=SOURCE_WARNING in notices or segment_boards.STALE_WARNING in notices,plan_stale=bool(state_for(data,sid).get('plan') and (state_for(data,sid).get('storyboard_sig')!=board_sig(data,sid) or SETTINGS_WARNING in notices)),casting_stale=casting_stale(data,sid,directory),template=request_template(data,sid,materials,directory))
        except (ValueError, KeyError, OSError):
            # Step 02 can remove or renumber cells. Keep historical results
            # readable so the user can regenerate Step 04 from this page.
            result[sid]=dict(materials=[],active_materials={},warnings=[SOURCE_WARNING],
                             source_stale=True,plan_stale=True,template='')
    return result


def casting_stale(data,sid,directory):
    state=state_for(data,sid)
    current=next((v for v in state.get('versions',[]) if v.get('id')==state.get('current')),None)
    if not current:return False
    context=(data.get('images',{}).get(current.get('image_id'),{}).get('storyboard_context') or {})
    previous=current.get('settings_sig') or context.get('settings_sig') or state.get('settings_sig')
    return bool(previous and not settings_match(previous,data,sid,directory,materials_for(data,sid,directory),current.get('settings_materials')))


def cell_materials(data,sid,cell,request,materials,directory):
    options=material_options(data,sid,directory)
    product=(data.get('replacements') or {}).get('product') or {}
    product_names=[str(value).strip() for value in (product.get('name'), (product.get('look') or {}).get('category')) if value]
    product_mentioned=bool(re.search(r'商品|产品|product',request,re.I)) or any(name.casefold() in request.casefold() for name in product_names if name)
    def mentioned(m):
        return m['key'] in request or (m['name'].split(' ',1)[-1] and m['name'].split(' ',1)[-1] in request) or (m['key']=='product' and product_mentioned)
    options=[m for m in options if (materials.get(m['key']) and (bool(cell.get('product')) if m['key']=='product' else m['key'] in cell.get('characters',[]))) or mentioned(m)]
    order=cell.get('characters',[])
    return sorted(options,key=lambda m:10000 if m['key']=='product' else order.index(m['key']) if m['key'] in order else len(order))


def _cell_inputs(data,sid,body,directory):
    _,cells=sheet_cells(data,sid);state=state_for(data,sid)
    if not state.get('plan'):raise ValueError('请先生成改图方案')
    if state.get('storyboard_sig')!=board_sig(data,sid):raise ValueError(SOURCE_WARNING)
    if segment_boards.has_segment(data,sid) and (state.get('source') or {}).get('source_sig')!=segment_boards.source_sig(data,sid,directory):raise ValueError(SOURCE_WARNING)
    cell=next((c for c in cells if c['id']==body.get('cell_id')),None)
    current=next((v for v in state.get('versions',[]) if v['id']==state.get('current')),None)
    if not cell or not current or current.get('storyboard_sig',state['storyboard_sig'])!=board_sig(data,sid):raise ValueError('请先生成整张新分镜')
    if current.get('slicer')!=2:raise ValueError('先重新切上一版')
    if not isinstance(body.get('request'),str) or not body['request'].strip():raise ValueError('先写一下这一格要怎么改')
    return cell,current


def _cell_revision_inputs_sig(data,sid,body,directory):
    cell,current=_cell_inputs(data,sid,body,directory);state=state_for(data,sid)
    materials=materials_for(data,sid,directory)
    # A disabled photo can still be explicitly named by the user or the plan.
    # Bind available descriptions as well as their bytes before reasoning.
    photos=[{**m,'sha256':store.sha256_file(directory/m['file'])} for m in material_options(data,sid,directory)]
    return digest([board_sig(data,sid),state['plan'],settings_sig(data,sid,directory,materials),
                   photos,current,frame_context(data,cell),
                   store.sha256_file(directory/current['cells'][cell['id']]),
                   store.sha256_file(directory/cell['file'])])


def cell_revision(data,sid,cid,directory):
    """Keep single-cell revisions separate from the sheet's version signature."""
    state=state_for(data,sid);saved=(state.get('cell_revisions') or {}).get(cid)
    if saved and (saved.get('base_plan_sig'),saved.get('storyboard_sig'),saved.get('settings_sig')) == (
            state['plan'].get('sig'),board_sig(data,sid),settings_sig(data,sid,directory,materials_for(data,sid,directory))):
        return saved
    return next(c for c in state['plan']['cells'] if c['cell']==cid)


def cell_revision_messages(data,sid,body,directory):
    cell,current=_cell_inputs(data,sid,body,directory);cid=cell['id']
    request=body['request'].strip();previous=cell_revision(data,sid,cid,directory)
    materials=materials_for(data,sid,directory)
    historical=[m for m in material_options(data,sid,directory) if materials.get(m['key'])]
    numbering='；'.join(f'图{n}={m["key"]} 新照片' for n,m in enumerate(historical,2))
    parts=[dict(type='text',text='用户要求：'+request+'\n这一格当前方案：'+json.dumps({k:previous[k] for k in ('changes','keep')},ensure_ascii=False)+
                '\n当前方案的历史图号不代表本次推理输入的图号。整段方案中的照片对应：'+(numbering or '无启用照片')+'。本次图2始终只是原片截帧，不能当作替换人物照片。'+
                '\n这一格信息：'+json.dumps(dict(格号=cid,时间=f"{cell['start']}–{cell['end']} 秒",**frame_context(data,cell)),ensure_ascii=False)),
           dict(type='text',text='图1：当前这一格的新图；检查用户指出的问题。')]
    parts+=llm.image_part(directory/current['cells'][cid])
    parts.append(dict(type='text',text='图2：原片这一格的截帧，仅供推理理解这个瞬间；不发送给生图模型，不沿用原片人物身份。'))
    parts+=llm.image_part(directory/cell['file'])
    mentioned={m['key'] for m in cell_materials(data,sid,cell,request+'\n'+previous['changes'],materials,directory)}
    options=[m for m in material_options(data,sid,directory) if materials.get(m['key']) or m['key'] in mentioned]
    for number,m in enumerate(options,3):
        parts.append(dict(type='text',text=f"图{number}：素材 {m['key']}，{m['name']}的新照片\n第 03 步描述：{m['description']}"))
        parts+=llm.image_part(directory/m['file'])
    checks=(data.get('replacements') or {}).get('product',{}).get('story_checks',[])
    parts.append(dict(type='text',text='商品的剧情检查（问题和回答）：'+json.dumps(checks,ensure_ascii=False)))
    return [dict(role='system',content=CELL_REVISION_SYSTEM),dict(role='user',content=parts)]


def revise_cell(pid,sid,body):
    """Reason outside the project lock; commit only against unchanged inputs."""
    from . import fix_plan
    from .gateway.errors import classify_error
    directory=store.project_dir(pid)
    with store._lock(pid):
        data=store.load(pid);cell,_=_cell_inputs(data,sid,body,directory);cid=cell['id']
        state=state_for(data,sid);request=body['request'].strip()
        binding=_cell_revision_inputs_sig(data,sid,body,directory)
        previous=copy.deepcopy(cell_revision(data,sid,cid,directory))
        previous_sig=digest((state.get('cell_revisions') or {}).get(cid))
        marker=fix_plan.preview_context(data,sid,body,directory)
    if marker:
        plan=data['runs'][sid]
        run=next(r for r in plan if r['rev']==marker['rev'])
        row=next(r for r in run['fix_plan']['redraw'] if r['cell']==cid)
        value=dict(changes=request,keep='',why=row['why'])
    else:
        try:
            value,_=gateway.reason_checked('storyboard_plan',cell_revision_messages(data,sid,body,directory),project=pid,max_tokens=3500)
            if not isinstance(value,dict) or any(not isinstance(value.get(k),str) for k in ('changes','keep','why')) or not value['changes'].strip() or not value['why'].strip():
                raise ValueError('修订方案格式不完整')
        except Exception as exc:
            kind=classify_error(exc)
            reason={'transient':'网络超时或服务暂时不可用','invalid_request':'模型返回的改法不完整','empty':'模型没有返回改法',
                    'auth':'模型权限验证失败','quota':'模型额度不足','too_large':'参考图片超出模型请求上限'}.get(kind,'推理服务未能完成')
            raise ValueError('整理这一镜的改法失败：'+reason+'，请重试') from exc
    revised={k:value[k].strip() for k in ('changes','keep','why')}
    materials=materials_for(data,sid,directory)
    revised.update(revised_by_request=request,original_plan=copy.deepcopy(next(c for c in state['plan']['cells'] if c['cell']==cid)),
                   previous_plan={k:previous[k] for k in ('changes','keep')},created_at=datetime.now().astimezone().isoformat(),
                   base_plan_sig=state['plan']['sig'],storyboard_sig=board_sig(data,sid),settings_sig=settings_sig(data,sid,directory,materials),
                   inputs_sig=binding,source='fix_plan' if marker else 'user')
    revised['sig']=digest([plan_sig(dict(cells=[dict(cell=cid,changes=revised['changes'],keep=revised['keep'])],remove_text=state['plan'].get('remove_text')),materials),
                           binding,request,revised['created_at']])
    def commit(d):
        current=state_for(d,sid)
        if _cell_revision_inputs_sig(d,sid,body,directory)!=binding or digest((current.get('cell_revisions') or {}).get(cid))!=previous_sig:
            raise ValueError('这一镜的方案、图片或素材刚刚改过，请重新整理改法')
        if marker:fix_plan.preview_context(d,sid,body,directory)
        current.setdefault('cell_revisions',{})[cid]=revised
    store.update(pid,commit)
    return revised


def _references_text(data,refs,single=False):
    people=list((data.get('breakdown') or {}).get('characters') or [])
    added=[dict(id=c['source_id'],name=c.get('name','')) for c in (data.get('replacements') or {}).get('characters',[]) if c.get('action')=='add']
    roles=scene_prompt.role_names({'breakdown':{'characters':people+added}})
    texts=[];people=[r for r in refs if r['key'] not in ('source','current','original','product')]
    for n,r in enumerate(refs,1):
        key=r['key']
        if key in ('source','current','original'):continue
        description=str(r.get('description') or '').strip()
        if key=='product':
            identity=f"the product: {description}" if description else 'the new product'
            texts.append(f"- Image {n} is {identity}. Use it only for shape, color, material and details; never render its layout or label text. Follow its real way of use: use, wear or place the product correctly; when displaying it, keep its front or functional side facing the camera.")
        else:
            others=', '.join(roles.get(p['key'],'PERSON '+p['key']) for p in people if p['key']!=key)
            distinct=f' A different person from {others}; never merge their faces.' if others else ''
            clothing='Use this photo for identity and clothing unless the revised panel plan specifies otherwise; do not inherit a different identity from Image 1.' if single else 'Clothing follows Image 1 unless a panel says otherwise.'
            texts.append(f"- Image {n} is {roles.get(key,'PERSON '+key)} ({key}): identity only — face, hair, age, body. {clothing}{distinct}"+(' '+description if description else ''))
    return '\n'.join(texts)


def image_prompt(data,sid,refs,cell_id=None,request=''):
    _,cells=sheet_cells(data,sid);plan=state_for(data,sid)['plan'];landscape=(data.get('source') or {}).get('probe',{}).get('width',0)>(data.get('source') or {}).get('probe',{}).get('height',1)
    cols,rows=_layout(data,sid);segment=segment_boards.has_segment(data,sid)
    if segment:landscape=False
    orientation='horizontal' if landscape else 'vertical';reftext=_references_text(data,refs,single=bool(cell_id))
    if cell_id:
        revised=request if isinstance(request,dict) else dict(changes=request,keep='')
        return f'Edit Image 1, one panel of a {orientation} short video storyboard. Follow only the revised panel plan below; preserve unaffected framing, camera angle, setting and lighting.\nReferences:\n{reftext}\nRevised panel plan: {revised["changes"]}\nKeep, only where consistent with the revised plan: {revised.get("keep", "")}\nDepict one instant only. Each person appears only once; remove extra people or duplicates from Image 1 as specified by the revised plan and fill the removed areas with the matching background. Do not add text, captions or borders.'
    from . import fission
    frozen=fission.snapshot(data,sid)
    if frozen:
        changed=set(fission.changes(data))
        lines=reftext.splitlines()
        for index,line in enumerate(lines):
            if any(f'({key}): identity only' in line for key in changed if key!='product'):
                lines[index]=line.replace('identity only — face, hair, age, body. Clothing follows Image 1 unless a panel says otherwise.',
                    'identity and clothing — face, hair, age, body and outfit follow this new photo; replace the master identity and outfit.')
        reftext='\n'.join(lines)
    changes={c['cell']:c['changes'] for c in plan['cells']};keeps={c['cell']:c['keep'] for c in plan['cells']}
    layout_text=f'{cols} 列 × {rows} 行 ({cols} columns by {rows} rows), {len(cells)} panels, each a 9:16 vertical frame' if segment else '3x3'
    text=f'''Edit Image 1, a {layout_text} storyboard sheet of a {orientation} short video (panels separated by thin white gutters, white margins on the sides).
Keep the exact sheet layout: same number of panels, same panel positions and sizes, same white gutters and margins, same aspect ratio. Do not add, remove, merge or reorder panels. Do not draw panel numbers, captions or any new text.
In every panel keep the camera angle, framing, composition, body pose, setting and lighting of Image 1, except for the changes listed below.
References:
{reftext}
The same person must look identical in every panel.
Panel changes (row by row, left to right):\n'''
    text+='\n'.join(f"- Panel {i+1} (row {i//cols+1}, col {i%cols+1}): {changes[c['id']] or 'no change'}"+(f". Keep: {keeps[c['id']]}" if keeps[c['id']] else '') for i,c in enumerate(cells))
    text+='\n'+('Remove all on-screen captions, subtitles, stickers and emoji from every panel; fill the area with the background that should be there.' if plan['remove_text'] else 'Keep the on-screen text of Image 1 as it is.')
    if frozen:
        text='Image 1 is an already-replaced master storyboard. Only replace the specified people or product according to Panel changes; preserve everything else.\n'+text+'\nDepict one instant per panel; each person appears only once.'
    return text


def preview(pid,sid,body):
    if 'cells' in body:
        from . import storyboard_multi
        selected=storyboard_multi.selection(body)
        if len(selected)>1:return storyboard_multi.preview(pid,sid,body)
        body={**body,'cell_id':selected[0]};body.pop('cells')
    revision=revise_cell(pid,sid,body) if body.get('cell_id') else None
    with store._lock(pid):
        if any(k in body for k in ('materials','plan','request')) and not body.get('cell_id'):
            save(pid,sid,{k:v for k,v in body.items() if k in ('materials','plan','request')})
        data=store.load(pid);directory=store.project_dir(pid);sheet,cells=sheet_cells(data,sid);state=state_for(data,sid)
        from . import fix_plan
        fix_context=fix_plan.preview_context(data,sid,body,directory)
        if not state.get('plan'):raise ValueError('请先生成改图方案')
        if state.get('storyboard_sig')!=board_sig(data,sid):
            raise ValueError(segment_boards.STALE_WARNING if segment_boards.has_segment(data,sid) else SOURCE_WARNING)
        if segment_boards.has_segment(data,sid) and (state.get('source') or {}).get('source_sig')!=segment_boards.source_sig(data,sid,directory):
            raise ValueError(SOURCE_WARNING)
        materials=materials_for(data,sid,directory);cid=body.get('cell_id');current=None;request=str(body.get('request') or '').strip()
        if revision and ((state.get('cell_revisions') or {}).get(cid,{}).get('sig')!=revision['sig'] or _cell_revision_inputs_sig(data,sid,body,directory)!=revision['inputs_sig']):
            raise ValueError('这一镜的方案、图片或素材刚刚改过，请重新整理改法')
        if cid:
            cell=next((c for c in cells if c['id']==cid),None)
            current=next((v for v in state.get('versions',[]) if v['id']==state.get('current')),None)
            if not cell or not current or current.get('storyboard_sig',state['storyboard_sig'])!=board_sig(data,sid):raise ValueError('请先生成整张新分镜')
            if current.get('slicer')!=2:raise ValueError('先重新切上一版')
            if not request:raise ValueError('先写一下这一格要怎么改')
        options=material_options(data,sid,directory)
        if cid:
            options=cell_materials(data,sid,cell,request+'\n'+revision['changes'],materials,directory)
        else:
            options=[m for m in options if materials.get(m['key'])]
        folder=_folder(data,sid,directory);folder.mkdir(parents=True,exist_ok=True)
        token=store.new_id('input');first=folder/f'{token}.png'
        with Image.open(directory/cells[0]['file']) as img:size=list(img.size)
        segment=segment_boards.has_segment(data,sid);cols,rows=_layout(data,sid)
        landscape=False if segment else size[0]>size[1];refs=[]
        if cid:
            with Image.open(directory/current['cells'][cid]) as img:padding=storyboard_images.padded(img,first,landscape)
            refs.append(dict(key='current',name='当前版本的这一格',path=str(first)))
        else:
            # Compose from raw cells so even v1 sheets never send panel labels to generation.
            clean=folder/f'{token}-clean.png';labeled=folder/f'{token}-labeled.jpg'
            from . import fission
            frozen=fission.snapshot(data,sid)
            base_cells=frozen['cells'] if frozen else {c['id']:c['file'] for c in cells}
            storyboard_images.compose(cells,base_cells,directory,clean,labeled,size,layout=(cols,rows))
            with Image.open(clean) as img:padding=storyboard_images.padded(img,first,landscape)
            labeled.unlink()
            refs.append(dict(key='source',name='不带格号的母版新分镜（已补白）' if frozen else '不带格号的原片拼图（已补白）',path=str(first)))
        refs += [dict(key=m['key'],name=m['name'],description=m['description'],path=str(directory/m['file']),asset_id=m['asset_id']) for m in options]
        prompt=image_prompt(data,sid,refs,cid,revision if cid else request)
        # Freeze every reference locally for a reviewable, immutable preview.
        assets=[];ids=[]
        for i,ref in enumerate(refs):
            frozen=folder/f'{token}-ref-{i+1}{Path(ref["path"]).suffix}';shutil_copy(Path(ref['path']),frozen);ref['path']=str(frozen);ref['file']=store.rel(pid,frozen)
            aid=f'{token}-{i+1}';ids.append(aid)
            assets.append(dict(id=aid,type='image',roles=['storyboard'],label=ref['name'],internal=True,file=store.rel(pid,frozen),sha256=store.sha256_file(frozen)))
        store.update(pid,lambda d:d['assets'].extend(assets))
        context=dict(sheet_id=sid,cell_id=cid,storyboard_sig=board_sig(data,sid),plan_sig=state['plan']['sig'],settings_sig=settings_sig(data,sid,directory,materials),
            cells=copy.deepcopy(cells),cell_size=size,sheet_size=[size[0]*cols+storyboard.GAP*(cols-1),size[1]*rows+storyboard.GAP*(rows-1)],padding=padding,references=refs,
            base_current=state.get('current') if cid else None,base_cells=copy.deepcopy(current['cells']) if cid else {},
            base_resliced_at=current.get('resliced_at') if cid else None)
        if segment:context.update(segment_id=sid,layout=[cols,rows],segment_sig=segment_plan.visual_segment_sig(data,segment_plan.get_segment(data,sid)))
        if revision:context.update(cell_revision=copy.deepcopy(revision),cell_revision_sig=revision['sig'])
        if fix_context:context['fix_plan']=fix_context
        rec=gateway.image_preview('storyboard_image',pid,prompt,ids,'3:2' if landscape else '2:3',None,'2k',f'storyboard-cell:{cid}' if cid else f'storyboard:{sid}',
                                  **({'override':'third_party:gpt-image-2'} if body.get('channel')=='third_party' else {}))
        rec['storyboard_context']=context;rec['warnings']=['提示词超过 4000 字，请检查完整内容后再确认'] if len(prompt)>4000 else []
        store.update(pid,lambda d:d['images'][rec['id']].update(storyboard_context=context,warnings=rec['warnings']))
        if fix_context:store.update(pid,lambda d:fix_plan.register_preview(d,sid,fix_context,rec['id'],cid))
        from . import fission
        store.log(pid,f'新分镜 {sid} '+('单镜改法已整理，生图预览已准备好，等待确认' if cid else '生图预览已准备好，等待确认' if fission.snapshot(data,sid) else '免费预览已准备好，等待确认'))
        return rec


def shutil_copy(source,target):
    import shutil
    shutil.copyfile(source,target)


def verify_preview(pid,rec):
    ctx=rec.get('storyboard_context')
    if not ctx:return
    if ctx.get('selected_cells'):
        from . import storyboard_multi
        storyboard_multi.verify(pid,rec)
    data=store.load(pid);directory=store.project_dir(pid);sid=ctx['sheet_id'];state=state_for(data,sid)
    retry='重新整理改法并生成预览' if ctx.get('cell_id') else '重新生成免费预览'
    if ctx.get('fix_plan'):
        from . import fix_plan
        marker=ctx['fix_plan']
        _,plan=fix_plan._current(data,sid,marker['rev'],{'plan_id':marker['id']},directory)
        if plan.get('active_image')!=rec.get('id'):
            raise ValueError('修改方案的重画预览已被替换，请刷新页面')
    if ctx.get('cell_id'):
        revision=(state.get('cell_revisions') or {}).get(ctx['cell_id'])
        if not ctx.get('cell_revision_sig') or ctx['cell_revision_sig']!=(revision or {}).get('sig'):
            raise ValueError('这一镜的改法改过，请重新整理并生成预览')
        base=next((v for v in state.get('versions',[]) if v['id']==ctx.get('base_current')),None)
        if not base or base.get('slicer')!=2 or base.get('resliced_at')!=ctx.get('base_resliced_at'):
            raise ValueError('分镜重新切过，请'+retry)
        if revision.get('inputs_sig')!=_cell_revision_inputs_sig(data,sid,dict(cell_id=ctx['cell_id'],request=revision['revised_by_request']),directory):
            raise ValueError('这一镜的方案、图片或素材改过，请'+retry)
    if any(other.get('id')!=rec.get('id') and other.get('storyboard_context',{}).get('sheet_id')==sid
           and other.get('state') in ('running','query_pending','submitting') for other in data.get('images',{}).values()):
        raise ValueError('这张分镜正在生成，完成后再确认下一张')
    if ctx['storyboard_sig']!=board_sig(data,sid) or ctx['plan_sig']!=(state.get('plan') or {}).get('sig') or ctx['settings_sig']!=settings_sig(data,sid,directory,materials_for(data,sid,directory)) or (ctx.get('cell_id') and ctx['base_current']!=state.get('current')):
        raise ValueError('分镜、方案或素材改过，请'+retry)
    for r in ctx['references']:
        path=Path(r['path']);asset=next((a for a in data['assets'] if a.get('file')==store.rel(pid,path)),None)
        if not path.is_file() or not asset or store.sha256_file(path)!=asset['sha256']:raise ValueError('参考图改过，请'+retry)


def use_version(pid,sid,vid):
    with store._lock(pid):
        data=store.load(pid);state=state_for(data,sid)
        version=next((v for v in state.get('versions',[]) if v['id']==vid),None)
        if not version:raise ValueError('这一版找不到了，请刷新页面')
        directory=store.project_dir(pid)
        for file in [version['file'],version['clean_file'],*version['cells'].values()]:
            path=(directory/file).resolve()
            if not path.is_relative_to(directory.resolve()) or not path.is_file():raise ValueError('这一版的图片找不到了')
        return store.update(pid,lambda d:_states(d,sid)[sid].update(current=vid))
