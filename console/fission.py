"""Create independent projects from approved masters and prepare unpaid board previews."""
from __future__ import annotations

import base64
import copy
from itertools import product as combinations
from datetime import datetime
import json
from pathlib import Path
import shutil
import threading
import uuid

from . import cast, config, final_preview, gateway, jobs, library, llm, segment_boards, segment_plan, store, storyboard
from .channel_text import user_message

FISSION_PLAN_SYSTEM = '''你是短视频母版分镜的局部替换师。输入的底图是已经替换好并修订过的母版新分镜，不能恢复原片的人物或商品。
只描述“换掉的项”在每格里怎么换：人物长相和穿着以新照片为准，新商品的外观以新照片为准，拿法和用法以剧情检查的问答为准。
构图、机位、动作、场景、光线和“沿用的项”都保持母版的样子。母版改法用于理解现状，里面历史图号不是本次图号，不重复执行母版已经完成的替换。
某一格里没有换掉的项时，changes 写“不变”；keep 写必须保持的母版画面内容。不增加原本没有出现的人或商品。
每格只画 frame_at 截帧时刻的一个瞬间，同一个人只出现一次，不把先后动作画成分身，人数以这一格母版实际可见的画面为准。
新商品的体积或拿法与母版商品差别大时，只按已经回答的用法调整手势。回答“不确定”时，保持母版的状态，不猜测功能或新用法；回答“不能”时不得表现该功能。
拼图每格都要列出，changes 和 keep 都是字符串。保留格子版式、空白格和留白，不添加画面文字；用户没有要求时不改变母版的文字状态。
只输出 JSON：{"summary":"一句话总结","cells":[{"cell":"格号","changes":"替换改法或不变","keep":"保持项"}],"remove_text":false,"questions":[]}'''

COPY_FIELDS = ('source','analysis','breakdown','breakdown_versions','breakdown_provider','storyboard',
               'segments_plan','speech_lines','speech_lines_state')
_reason_slots = threading.BoundedSemaphore(2)
_batch_lock = threading.RLock()


def _now():
    return datetime.now().astimezone().isoformat()


def eligibility(data, directory):
    clips, _, warnings = final_preview.selections(data,directory)
    return {'ready':bool(clips) and not warnings,'warnings':warnings}


def _asset(data, entry, directory):
    asset = next((a for a in data.get('assets',[]) if a['id']==entry.get('asset_id')),{})
    path = (directory / asset.get('file','')).resolve()
    sha = store.sha256_file(path) if path.is_relative_to(directory.resolve()) and path.is_file() else None
    return dict(asset_id=asset.get('id'),file=asset.get('file'),sha256=sha,
                master_name=asset.get('label') or asset.get('name') or '母版的照片')


def _cell_presence(cell, key):
    """None means the existing breakdown has no usable evidence for this slot."""
    if key == 'product':
        if 'product' not in cell and 'product_later' not in cell: return None
        return bool(cell.get('product') or cell.get('product_later'))
    if not isinstance(cell.get('characters'), list): return None
    return key in cell['characters']


def _original_frame(data, key, directory):
    for cell in (data.get('storyboard') or {}).get('cells', []):
        # product_later describes the rest of the cell interval, not this still.
        visible=bool(cell.get('product')) if key=='product' else _cell_presence(cell,key)
        if visible is not True: continue
        relative = cell.get('file')
        if not relative: continue
        path = (directory / relative).resolve()
        if path.is_relative_to(directory.resolve()) and path.is_file(): return relative
    return None


def columns(data, directory):
    rep = data.get('replacements') or {}
    roles = {c['id']:c for c in (data.get('breakdown') or {}).get('characters',[])}
    names = {key:c.get('name',key) for key,c in roles.items()}
    result = []
    replacements={c.get('source_id'):c for c in rep.get('characters',[])}
    entries=[(key,'person',replacements.get(key) or dict(source_id=key,action='keep')) for key in roles]
    entries += [(c.get('source_id'),'person',c) for c in rep.get('characters',[]) if c.get('action')=='add' and c.get('source_id') not in roles]
    product = rep.get('product') or {}
    has_product=bool((data.get('breakdown') or {}).get('productMoments')) or any(
        _cell_presence(cell,'product') for cell in (data.get('storyboard') or {}).get('cells',[]))
    has_product=has_product or product.get('action')=='replace' and bool(product.get('asset_id'))
    if has_product and product.get('action') in ('replace','keep'): entries.append(('product','product',product))
    for key,kind,entry in entries:
        name = '商品' if kind=='product' else names.get(key) or entry.get('name') or key
        row = dict(key=key,kind=kind,name=name,action=entry['action'],description=entry.get('description',''),**_asset(data,entry,directory))
        if entry['action']=='keep':
            row.update(asset_id=None,sha256=None,file=_original_frame(data,key,directory),original_frame=True,
                       master_name='原片商品' if kind=='product' else '原片里的'+name)
        source_name=(storyboard.product_names(data.get('breakdown') or {}).get('name') or '原片商品') if kind=='product' else (key+' '+name).strip()
        row.update(source_name=source_name,source_description=str((roles.get(key) or {}).get('description') or (roles.get(key) or {}).get('role') or ''),
                   title=('商品 · ' if kind=='product' else '人物 · ')+source_name,
                   master_status=('母版没换，用的是原片里的商品' if kind=='product' else '母版没换，用的是原片里的人') if entry['action']=='keep' else '母版已换成：'+row['master_name'])
        result.append(row)
    return result, [row for row in result if row['action']=='keep']


def _check_context(data, item):
    return segment_plan.digest([data.get('breakdown'),item.get('description',''),item['sha256']])


def _cache_check(data, item):
    check = ((data.get('fission_draft') or {}).get('story_cache') or {}).get(item['sha256'])
    if check and check.get('context_sig')==_check_context(data,item): return copy.deepcopy(check)
    return None


def combination_key(selections):
    # Reversible stable keys let a cleared slot forget old removals without persisting rows.
    raw=json.dumps(selections,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    return 'c'+base64.urlsafe_b64encode(raw).decode().rstrip('=')


def _combination_selection(key):
    if not isinstance(key,str) or not key.startswith('c'): raise ValueError('新视频组合编号无效，请重新选择照片')
    try:
        value=json.loads(base64.urlsafe_b64decode(key[1:]+'='*(-len(key[1:])%4)))
    except (ValueError,UnicodeError): raise ValueError('新视频组合编号无效，请重新选择照片') from None
    if not isinstance(value,dict) or any(not isinstance(k,str) or v is not None and not isinstance(v,str) for k,v in value.items()):
        raise ValueError('新视频组合编号无效，请重新选择照片')
    if combination_key(value)!=key: raise ValueError('新视频组合编号无效，请重新选择照片')
    return value


def _draft(data, supplied, directory):
    if not isinstance(supplied,dict): raise ValueError('照片选择格式不正确，请重新打开面板')
    cols,_=columns(data,directory);keys={c['key'] for c in cols}
    if 'choices' not in supplied and 'rows' in supplied:
        # Old drafts are migrated in memory only; row order defines selection order.
        choices={}
        for row in supplied.get('rows') or []:
            for key,value in (row.get('selections') or {}).items():
                if key in keys and value and value not in choices.setdefault(key,[]): choices[key].append(value)
        removed=[]
    else:
        choices=supplied.get('choices',{});removed=supplied.get('removed',[])
        if supplied is data.get('fission_draft') and isinstance(choices,dict):
            choices={key:value for key,value in choices.items() if key in keys}
    if not isinstance(choices,dict) or set(choices)-keys: raise ValueError('母版的选角已改变，请重新选择照片')
    clean={}
    for key,values in choices.items():
        if not isinstance(values,list) or any(not isinstance(v,str) or not v for v in values): raise ValueError('照片选择格式不正确')
        if values: clean[key]=list(dict.fromkeys(values))
    if not isinstance(removed,list) or any(not isinstance(key,str) for key in removed): raise ValueError('去掉的新视频记录格式不正确')
    kept=[]
    for key in dict.fromkeys(removed):
        selections=_combination_selection(key)
        if set(selections)!=keys: continue
        if any(value is not None and not clean.get(slot) for slot,value in selections.items()): continue
        kept.append(key)
    return dict(choices=clean,removed=kept,story_cache=copy.deepcopy((data.get('fission_draft') or {}).get('story_cache') or {}),revision=(data.get('fission_draft') or {}).get('revision',0))


def _rows(data, draft, directory):
    cols,_=columns(data,directory)
    selected={};issues={};product_checks={}
    for col in cols:
        for lid in draft['choices'].get(col['key'],[]):
            problem=None
            try:
                item=library.get(lid);selected[lid]=item
                if item['kind']!=col['kind']: raise ValueError('“'+item['name']+'”不是这个位置需要的照片类型，请重新选择。')
                library.file_path(item)
                if col['sha256'] and item['sha256']==col['sha256']:
                    raise ValueError('“'+item['name']+'”是母版正在用的照片，请重新选择。')
                if col['kind']=='product':
                    check=_cache_check(data,item);product_checks[lid]=check
                    if not check or check.get('status')!='ready' or any(c.get('answer') not in ('yes','no','unsure') for c in check.get('checks',[])):
                        problem='“'+item['name']+'”的用法问题还没回答，点上面的“回答问题”。'
            except (ValueError,OSError) as exc:
                item=selected.get(lid)
                if item and ('类型' in str(exc) or '母版正在用' in str(exc)): problem=str(exc)
                else: problem='“'+(item or {}).get('name','所选照片')+'”这张照片已从素材库删除，点它右上角的 × 去掉，或重新选一张。'
            if problem: issues[(col['key'],lid)]=problem
    rows=[];errors=[];removed=set(draft['removed'])
    for values in combinations(*(draft['choices'].get(c['key']) or [None] for c in cols)):
        if not any(values): continue
        selections=dict(zip((c['key'] for c in cols),values));rid=combination_key(selections)
        if rid in removed: continue
        row=dict(id=rid,selections=selections)
        if selections.get('product') and product_checks.get(selections['product']):row['story_check']=product_checks[selections['product']]
        rows.append(row);problems=[issues[(key,lid)] for key,lid in selections.items() if (key,lid) in issues]
        problems.extend(c['name']+'的母版照片找不到了，回第 03 步重新选择。' for c in cols
                        if c['action']!='keep' and not selections[c['key']] and not c['sha256'])
        if problems:errors.append(dict(row=len(rows),row_id=rid,errors=problems,error='；'.join(problems)))
    return rows,errors,product_checks


def view(pid):
    data=store.load(pid);directory=store.project_dir(pid);cols,kept=columns(data,directory)
    draft=_draft(data,data.get('fission_draft') or {},directory)
    rows,errors,checks=_rows(data,draft,directory)
    batches=list_batches(pid)
    recent=batches[0] if batches and any(item.get('status') in ('preparing','pending_confirmation') for item in batches[0]['items']) else None
    return {**eligibility(data,directory),'columns':cols,'kept':kept,'draft':draft,'rows':rows,'row_errors':errors,
            'product_checks':checks,'batches':batches,'recent_batch':recent}


def save_draft(pid, body):
    if 'choices' not in body: raise ValueError('请按位置提交 choices 照片选择')
    def commit(data):
        _check_draft_revision(data,body)
        data['fission_draft']={**_draft(data,body,store.project_dir(pid)),'updated_at':_now(),'revision':(data.get('fission_draft') or {}).get('revision',0)+1}
    store.update(pid,commit)
    return view(pid)


def _selected_product(data, lid):
    draft=_draft(data,data.get('fission_draft') or {},store.project_dir(data['id']))
    if not lid or lid not in draft['choices'].get('product',[]):raise ValueError('这个商品尚未选中，请重新选择照片')
    item=library.get(lid)
    if item['kind']!='product':raise ValueError('请选择商品照片')
    library.file_path(item)
    product_col=next((c for c in columns(data,store.project_dir(data['id']))[0] if c['key']=='product'),None)
    if product_col and product_col['sha256']==item['sha256']:raise ValueError('母版正在用这张照片，请选择新商品')
    return item


def check_story(pid, body):
    with store._lock('fission-check-'+pid):
        data=store.load(pid);item=_selected_product(data,body.get('library_id'));cached=_cache_check(data,item)
        if cached:return view(pid)
        photo=library.file_path(item);sig=_check_context(data,item)
        with _reason_slots:
            value,_=gateway.reason_checked('cast',cast.story_messages(data,item,photo),project=pid,max_tokens=2200)
        checks=cast.validate_story(value)
        def commit(current):
            now_item=_selected_product(current,body.get('library_id'))
            if now_item['sha256']!=item['sha256'] or _check_context(current,now_item)!=sig:
                raise ValueError('商品或母版拆解改过，请重新点“回答问题”')
            current.setdefault('fission_draft',{}).setdefault('story_cache',{})[item['sha256']]=dict(
                sha256=item['sha256'],context_sig=sig,status='ready',checks=checks,generated_at=_now())
        store.update(pid,commit)
        return view(pid)


def answer_story(pid, body):
    answers=body.get('answers')
    if not isinstance(answers,list):raise ValueError('请逐题选择能、不能或不确定')
    def commit(data):
        item=_selected_product(data,body.get('library_id'));check=_cache_check(data,item)
        if not check:raise ValueError('商品或母版拆解改过，请重新点“回答问题”')
        byid={c['id']:c for c in check['checks']};seen=set()
        for answer in answers:
            if not isinstance(answer,dict) or answer.get('id') not in byid or answer['id'] in seen or answer.get('answer') not in ('yes','no','unsure','') or not isinstance(answer.get('note',''),str):
                raise ValueError('回答格式不正确，请逐题选择能、不能或不确定')
            seen.add(answer['id']);byid[answer['id']].update(answer=answer['answer'],note=answer.get('note',''))
        data['fission_draft']['story_cache'][item['sha256']]=check
    store.update(pid,commit)
    return view(pid)


def _file(directory, relative):
    path=(directory / str(relative or '')).resolve()
    if not path.is_relative_to(directory.resolve()) or not path.is_file():
        raise ValueError('母版素材文件找不到或路径无效：'+str(relative or '未选择'))
    return path


def _copy(source, destination, relative, target=None):
    source_file=_file(source,relative);output=destination/(target or relative)
    if not output.resolve().is_relative_to(destination.resolve()):raise ValueError('文件路径不安全')
    output.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source_file,output)
    return output.relative_to(destination).as_posix()


def clone_row(master, row, index, *, fission_number=None):
    """Publish one fully assembled directory by rename; failure removes its staging tree."""
    origin=store.project_dir(master['id']);pid=store.new_id('p');destination=store.project_dir(pid)
    staging=config.PROJECTS_DIR/('.fission-'+uuid.uuid4().hex);staging.mkdir(parents=True)
    try:
        data={key:copy.deepcopy(master[key]) for key in COPY_FIELDS if key in master}
        data.update(id=pid,created_at=_now(),assets=[],runs={},images={},replacements=copy.deepcopy(master.get('replacements') or {}))
        for folder in ('source','assets','analysis/frames','packages','jimmy-runs','image-runs','clips','final'):(staging/folder).mkdir(parents=True,exist_ok=True)
        _copy(origin,staging,(data.get('source') or {}).get('file'))
        # Source analysis includes transcripts, raw captions, frame grids and extraction evidence.
        # Generated boards, prompts, reviews and provider responses are deliberately not copied.
        excluded={'segments','director','final-checks','storyboard-edits'}
        for file in (origin/'analysis').rglob('*'):
            rel=file.relative_to(origin)
            if file.is_file() and not any(part in excluded for part in rel.parts) and 'edits' not in rel.parts:
                _copy(origin,staging,rel.as_posix())
        rep=data['replacements']
        present={c.get('source_id') for c in rep.get('characters',[])}
        rep.setdefault('characters',[]).extend(dict(source_id=c['id'],action='keep') for c in (data.get('breakdown') or {}).get('characters',[]) if c['id'] not in present)
        entries=list(rep.get('characters',[]))+([rep['product']] if rep.get('product') else [])
        assets={a['id']:a for a in master.get('assets',[])}
        # Copy all ordinary assets: keep settings may refer to existing camera/photo variants.
        for asset in master.get('assets',[]):
            if not asset.get('internal'):
                _copy(origin,staging,asset.get('file'));data['assets'].append(copy.deepcopy(asset))
        changed=[];cols,_=columns(master,origin);bykey={c['key']:c for c in cols}
        for entry in entries:
            key=entry.get('source_id','product');selected=row['selections'].get(key)
            if key not in bykey:continue
            if not selected:
                if entry.get('asset_id') and not any(a['id']==entry['asset_id'] for a in data['assets']):
                    asset=assets.get(entry['asset_id'])
                    if not asset:raise ValueError('母版照片已删除，请回第 03 步重新选择')
                    _copy(origin,staging,asset['file']);data['assets'].append(copy.deepcopy(asset))
                continue
            item=library.get(selected);photo=library.file_path(item);aid='a'+uuid.uuid4().hex[:20]
            relative='assets/'+aid+photo.suffix;shutil.copyfile(photo,staging/relative)
            asset=dict(id=aid,type='image',label=item['name'],roles=['product_truth' if key=='product' else 'person_identity'],
                       file=relative,sha256=item['sha256'],description=item.get('description',''))
            data['assets'].append(asset)
            before=copy.deepcopy(entry)
            entry.update(asset_id=aid,description=copy.deepcopy(item.get('description','')),look=copy.deepcopy(item.get('look',{})))
            if before.get('action')=='keep': entry['action']='replace'
            entry.pop('camera_assets',None)
            if key=='product':
                check=_cache_check(master,item)
                if not check or any(c.get('answer') not in ('yes','no','unsure') for c in check['checks']):raise ValueError('新商品用法问题还没有回答完')
                entry['story_checks']=copy.deepcopy(check['checks'])
                entry['story_checks_sig']=cast.product_sig(data,staging,entry)
            changed.append(dict(key=key,from_name=bykey[key]['master_name'],to_name=item['name'],
                                before=before,after=copy.deepcopy(entry),sha256=item['sha256']))
        boards={};states={};master_materials={}
        for col in cols:
            entry=next((c for c in (master.get('replacements') or {}).get('characters',[]) if c.get('source_id')==col['key']),{}) if col['key']!='product' else (master.get('replacements') or {}).get('product',{})
            master_materials[col['key']]={**copy.deepcopy(col),'look':copy.deepcopy(entry.get('look',{})),
                'file':_copy(origin,staging,col['file'],f'analysis/master-snapshot/materials/{col["key"]}{Path(col["file"]).suffix}') if col.get('file') else None}
        for segment in data['segments_plan']['segments']:
            sid=segment['id'];state=segment_boards.state_for(master,sid);current=segment_boards.current(master,sid)
            if not current:raise ValueError(f'{sid} 的母版新分镜还没有选定，请回第 04 步选一版新分镜')
            source=copy.deepcopy(state.get('source') or {})
            for field in ('file','clean_file'):_copy(origin,staging,source.get(field))
            states[sid]={'source':source}
            frozen={key:copy.deepcopy(current[key]) for key in ('id','slicer','layout','cell_size','created_at') if key in current}
            for field in ('file','clean_file'):
                frozen[field]=_copy(origin,staging,current.get(field),f'analysis/master-snapshot/{sid}/{field}{Path(current.get(field,"")).suffix}')
            frozen['cells']={cid:_copy(origin,staging,current['cells'].get(cid),f'analysis/master-snapshot/{sid}/{cid}{Path(current["cells"].get(cid,"")).suffix}') for cid in segment['cells']}
            frozen['plan']=copy.deepcopy(state.get('plan') or {})
            context=(master.get('images',{}).get(current.get('image_id'),{}).get('storyboard_context') or {})
            frozen['material_keys']=list(dict.fromkeys([key for key,enabled in state.get('materials',{}).items() if enabled]+
                [ref['key'] for ref in context.get('references',[]) if ref.get('key') not in ('source','current','original')]))
            frozen['segment']=copy.deepcopy(segment);boards[sid]=frozen
        data['segment_boards']={'segments':states}
        number=index if fission_number is None else fission_number
        data['name']=f'{master["name"]} · 裂变 {number}'
        data['derived_from']=dict(project_id=master['id'],row=index,row_id=row['id'],created_at=_now(),changes=changed,
                                  fission_number=number,
                                  master_snapshot={'name':master['name'],'boards':boards,'materials':master_materials,'replacements':copy.deepcopy(master.get('replacements') or {})})
        _apply_kept_replacements(data,changed)
        data['fission_preparation']={'segments':{s['id']:{'status':'queued','error':None} for s in data['segments_plan']['segments']}}
        store.atomic_write_json(staging/'project.json',data)
        staging.rename(destination)
        return data
    except Exception:
        shutil.rmtree(staging,ignore_errors=True)
        raise


def _apply_kept_replacements(data, changed):
    cells={c['id']:c for c in (data.get('storyboard') or {}).get('cells',[])}
    segments=(data.get('segments_plan') or {}).get('segments',[])
    kept_changes=[change for change in changed if change['before'].get('action')=='keep']
    for change in kept_changes:
        key=change['key']
        unknown=any(_cell_presence(cells.get(cid,{}),key) is None for s in segments for cid in s.get('cells',[]))
        for segment in segments:
            present=unknown or any(_cell_presence(cells.get(cid,{}),key) for cid in segment.get('cells',[]))
            if not present: continue
            if key not in segment.setdefault('refs',[]):segment['refs'].append(key)
    if kept_changes:
        name='、'.join('商品' if c['key']=='product' else c['from_name'].removeprefix('原片里的') for c in kept_changes)
        for segment in segments:
            if segment.get('mode')=='video':
                segment.update(mode='image',mode_suggested='image',mode_reason=
                    f'母版这段带原片视频；这次换掉了母版没换的{name}，带原片视频容易把原片里的{name}带回来，所以改成不带。')


def _batch_path(bid):
    if not isinstance(bid,str) or not store.SAFE_ID.fullmatch(bid):raise ValueError('批次编号无效')
    return config.BATCHES_DIR/(bid+'.json')


def _save_batch(batch):
    with _batch_lock:store.atomic_write_json(_batch_path(batch['id']),batch)


def _created_snapshot(data, selections):
    """Names and thumbnails are frozen to project files, never live library rows."""
    master=(data.get('derived_from') or {}).get('master_snapshot') or {}
    changes={c['key']:c for c in (data.get('derived_from') or {}).get('changes',[])}
    roles={c['id'] for c in (data.get('breakdown') or {}).get('characters',[])}
    roles.update(c.get('source_id') for c in (data.get('replacements') or {}).get('characters',[]) if c.get('action')=='add')
    slots=[]
    for key,col in (master.get('materials') or {}).items():
        if key!='product' and key not in roles:continue
        change=changes.get(key) if selections.get(key) else None
        asset=next((a for a in data.get('assets',[]) if a['id']==(change or {}).get('after',{}).get('asset_id')), {})
        slots.append(dict(key=key,kind=col.get('kind','product' if key=='product' else 'person'),source_name=col.get('source_name') or col.get('name') or key,
                          name=change['to_name'] if change else col.get('master_name',''),file=asset.get('file') if change else col.get('file'),
                          changed=bool(change),library_id=selections.get(key)))
    derived=data.get('derived_from') or {}
    return dict(project_name=data['name'],slots=slots,changes=copy.deepcopy(list(changes.values())),
                fission_number=derived.get('fission_number',derived.get('row')))


def batch_view(bid):
    with _batch_lock:batch=json.loads(_batch_path(bid).read_text(encoding='utf-8'))
    for item in batch['items']:
        if not item.get('project_id'):
            item.update(status='failed',segments=[]);continue
        try:
            data=store.load(item['project_id']);states=(data.get('fission_preparation') or {}).get('segments') or {}
            if 'slots' not in item:item.update(_created_snapshot(data,item.get('selections') or {}))
            item['segments']=[dict(id=sid,**state) for sid,state in states.items()]
            running=jobs.snapshot(item['project_id']).get('fission-prepare',{}).get('state')=='running'
            if not running:
                for state in item['segments']:
                    if state['status'] in ('queued','preparing'):
                        state.update(status='failed',error='准备任务已中断，已有项目和分镜预览保留。点“重试”继续准备。')
            statuses=[s['status'] for s in item['segments']]
            item['status']='failed' if 'failed' in statuses else 'pending_confirmation' if statuses and all(s=='pending_confirmation' for s in statuses) else 'preparing'
            image_error=None
            if statuses and all(s=='pending_confirmation' for s in statuses):
                images=[data.get('images',{}).get(s.get('image_id'),{}) for s in item['segments']]
                if all(image.get('state')=='completed' for image in images):item['status']='completed'
                elif any(image.get('state') in ('failed','error','cancelled') for image in images):
                    item['status']='image_failed'
                    image_error='；'.join(str(image.get('error') or '生图未完成，打开第 04 步重试') for image in images if image.get('state') in ('failed','error','cancelled'))
                elif any(image.get('state') in ('running','query_pending','submitting') for image in images):item['status']='generating'
            item['error']=image_error or '；'.join(s.get('error') or '' for s in item['segments'] if s['status']=='failed') or None
            item['can_retry']=not running and any(s['status']=='failed' for s in item['segments'])
        except FileNotFoundError:item.update(status='failed',error='裂变项目已删除',segments=[],can_retry=False)
    return batch


def list_batches(master_id):
    if not config.BATCHES_DIR.exists():return []
    result=[]
    for path in sorted(config.BATCHES_DIR.glob('*.json'),reverse=True):
        try:
            batch=json.loads(path.read_text(encoding='utf-8'))
            if batch.get('master_id')==master_id:result.append(batch_view(batch['id']))
        except (ValueError,OSError):continue
    return sorted(result,key=lambda b:(b.get('created_at',''),b['id']),reverse=True)


def _check_draft_revision(data,body):
    if 'revision' in body and body['revision']!=(data.get('fission_draft') or {}).get('revision',0):
        raise ValueError('另一个窗口已修改照片选择，请重新打开裂变面板后再选。')


def _draft_sig(draft):
    return segment_plan.digest({'choices':draft['choices'],'removed':sorted(draft['removed'])})


def _clear_created_draft(pid, batch):
    """A retry may restore its submitted draft; never erase a newer different batch."""
    if not batch.get('draft_sig') or not any(item.get('project_id') for item in batch['items']):return
    data=store.load(pid)
    try:draft=_draft(data,data.get('fission_draft') or {},store.project_dir(pid))
    except ValueError:return
    if _draft_sig(draft)!=batch['draft_sig']:return
    saved=store.update(pid,lambda current:current.update(fission_draft={
        'choices':{},'removed':[],'story_cache':copy.deepcopy((current.get('fission_draft') or {}).get('story_cache') or {}),'updated_at':_now(),
        'revision':(current.get('fission_draft') or {}).get('revision',0)+1}))
    revision=saved['fission_draft']['revision']
    with _batch_lock:
        stored=json.loads(_batch_path(batch['id']).read_text(encoding='utf-8'))
        stored['draft_revision']=revision;_save_batch(stored)
    batch['draft_revision']=revision


def _created_response(pid,batch):
    draft=store.load(pid).get('fission_draft') or {}
    # A lost-response retry must not borrow the revision of another tab's draft.
    revision=batch.get('draft_revision',-1 if draft.get('choices') else draft.get('revision',0))
    return {'batch':batch,'draft_revision':revision}


def _next_fission_number(master_id):
    """Called under the master lock; atomic JSON snapshots need no other locks."""
    highest=0
    def number(value):
        return value if type(value) is int and value>0 else 0
    for path in config.BATCHES_DIR.glob('*.json'):
        try:
            batch=json.loads(path.read_text(encoding='utf-8'))
            if batch.get('master_id')==master_id:
                highest=max([highest]+[number(item.get('fission_number',item.get('row'))) for item in batch.get('items',[])])
        except (OSError,ValueError,TypeError):continue
    for path in config.PROJECTS_DIR.glob('*/project.json'):
        if not store.SAFE_ID.fullmatch(path.parent.name):continue
        try:
            derived=json.loads(path.read_text(encoding='utf-8')).get('derived_from') or {}
            if derived.get('project_id')==master_id:highest=max(highest,number(derived.get('fission_number',derived.get('row'))))
        except (OSError,ValueError,TypeError):continue
    return highest+1


def create(pid, body):
    with store._lock(pid):
        master=store.load(pid);directory=store.project_dir(pid);eligible=eligibility(master,directory)
        request_id=body.get('request_id')
        if request_id is not None and (not isinstance(request_id,str) or not store.SAFE_ID.fullmatch(request_id)):
            raise ValueError('创建请求编号无效，请重新打开面板')
        if request_id:
            previous=next((b for b in list_batches(pid) if b.get('request_id')==request_id),None)
            if previous:
                _clear_created_draft(pid,previous)
                return _created_response(pid,previous)
        if not eligible['ready']:raise ValueError('；'.join(eligible['warnings']))
        _check_draft_revision(master,body)
        draft=_draft(master,body if 'choices' in body else master.get('fission_draft') or {},directory)
        rows,errors,_=_rows(master,draft,directory)
        if not rows:raise ValueError('先选新照片')
        if len(rows)>20:raise ValueError(f'一次最多 20 条，现在是 {len(rows)} 条。少选几张，或点 × 去掉一些。')
        if errors:raise ValueError('；'.join(f'第 {e["row"]} 条：{e["error"]}' for e in errors))
        request_sig=segment_plan.digest([pid,[{'id':r['id'],'selections':r['selections']} for r in rows]])
        previous=next((b for b in list_batches(pid) if not request_id and b.get('request_sig')==request_sig),None)
        if previous:
            _clear_created_draft(pid,previous)
            return _created_response(pid,previous)
        first_number=_next_fission_number(pid)
        batch=dict(id='b'+uuid.uuid4().hex[:20],name=master['name']+' · 裂变',master_id=pid,created_at=_now(),items=[])
        batch['request_sig']=request_sig
        batch['draft_sig']=_draft_sig(draft)
        if request_id:batch['request_id']=request_id
        _save_batch(batch)
        for index,row in enumerate(rows,1):
            item=dict(row=index,fission_number=first_number+index-1,row_id=row['id'],selections=copy.deepcopy(row['selections']),project_id=None,error=None)
            try:
                child=clone_row(master,row,index,fission_number=item['fission_number']);item['project_id']=child['id'];item.update(_created_snapshot(child,row['selections']))
            except Exception as exc:item['error']=user_message(exc)
            batch['items'].append(item);_save_batch(batch)
        _clear_created_draft(pid,batch)
        created=_created_response(pid,batch)
    for item in batch['items']:
        if item['project_id']:
            try:schedule_prepare(item['project_id'])
            except Exception as exc:
                def fail(data):
                    for state in data['fission_preparation']['segments'].values():state.update(status='failed',error=user_message(exc))
                store.update(item['project_id'],fail)
    return {**created,'batch':batch_view(batch['id'])}


def snapshot(data, sid):
    return ((data.get('derived_from') or {}).get('master_snapshot') or {}).get('boards',{}).get(sid)


def changes(data, directory=None):
    """Compare current Step03 photos to the frozen master, including edits made later."""
    if not data.get('derived_from'):return {}
    directory=directory or store.project_dir(data['id'])
    frozen=data['derived_from'].get('master_snapshot') or {};materials=frozen.get('materials') or {}
    original={c['key']:c for c in data['derived_from'].get('changes',[])}
    prior=frozen.get('replacements') or {}
    prior_entries={r.get('source_id'):r for r in prior.get('characters',[])}
    if prior.get('product'):prior_entries['product']=prior['product']
    role_names={c['id']:c.get('name',c['id']) for c in (data.get('breakdown') or {}).get('characters',[])}
    rep=data.get('replacements') or {};entries=list(rep.get('characters',[]))+([rep['product']] if rep.get('product') else [])
    result={}
    for entry in entries:
        if entry.get('action') not in ('replace','add'):continue
        key=entry.get('source_id','product');before=materials.get(key)
        if before is None and prior_entries.get(key,{}).get('action')=='keep':
            before={**copy.deepcopy(prior_entries[key]),'original_frame':True,'file':_original_frame(data,key,directory),
                    'master_name':'原片商品' if key=='product' else '原片里的'+role_names.get(key,key)}
        asset=_asset(data,entry,directory)
        if before is not None and before.get('action')!='keep' and before.get('sha256')==asset.get('sha256'):continue
        if before is None and key not in original and not materials:continue
        result[key]=dict(key=key,from_name=(before or {}).get('master_name') or original.get(key,{}).get('from_name','母版中没有此项'),
                         to_name=asset['master_name'],before=before or original.get(key,{}).get('before',{}),after=copy.deepcopy(entry),sha256=asset.get('sha256'))
    return result


def plan_messages(data,sid,request,materials,directory):
    from . import storyboard_edits as edits
    frozen=snapshot(data,sid);_,cells=edits.sheet_cells(data,sid)
    changed=changes(data,directory)
    options=[m for m in edits.material_options(data,sid,directory) if materials.get(m['key'])]
    parts=[dict(type='text',text='母版改法（仅解释母版现状）：'+json.dumps(frozen.get('plan') or {},ensure_ascii=False))]
    for cell in cells:
        parts.append(dict(type='text',text='母版当前新分镜 '+cell['id']+'；'+json.dumps(edits.frame_context(data,cell),ensure_ascii=False)))
        parts.extend(llm.image_part(_file(directory,frozen['cells'][cell['id']])))
    changed_text=[];kept_text=[]
    for material in options:
        key=material['key'];change=changed.get(key)
        if change:
            from_name='原片里的商品' if key=='product' and change['before'].get('action')=='keep' else change['from_name']
            changed_text.append(dict(key=key,从=from_name,换成=change['to_name'],母版说明=change['before'].get('description',''),新说明=material['description']))
            parts.append(dict(type='text',text='换掉的项 '+key+'；从：'+from_name+'；换成：'+change['to_name']))
            if change['before'].get('file'):
                label='换掉的项原片截帧（只用来说明替换前是谁或是什么，不作为新身份）' if change['before'].get('original_frame') else '换掉的项原来用的母版照片（只说明替换前身份，不作为新身份）'
                parts.append(dict(type='text',text=label+'：'+key+' '+change['from_name']))
                parts.extend(llm.image_part(_file(directory,change['before']['file'])))
        else:kept_text.append(dict(key=key,name=material['name'],description=material['description']))
        parts.append(dict(type='text',text=('换掉的项' if change else '沿用的项')+'：'+key+' '+material['name']+'；照片与说明：'+material['description']))
        parts.extend(llm.image_part(_file(directory,material['file'])))
    kept=[c for c in (data.get('replacements') or {}).get('characters',[]) if c.get('action')=='keep']
    parts.append(dict(type='text',text='换掉的项：'+json.dumps(changed_text,ensure_ascii=False)+'\n沿用的项：'+json.dumps(kept_text+kept,ensure_ascii=False)+
                      '\n商品的剧情检查（问题和回答）：'+json.dumps((data.get('replacements') or {}).get('product',{}).get('story_checks',[]),ensure_ascii=False)+'\n补充要求：'+(request or '无')))
    cols,rows=edits._layout(data,sid)
    return [dict(role='system',content=FISSION_PLAN_SYSTEM+f'\n本段共 {len(cells)} 格，{cols} 列 × {rows} 行。'),dict(role='user',content=parts)]


def prepare_segment(pid,sid,*,preserve_plan=False):
    from . import storyboard_edits as edits
    def status(**fields):
        store.update(pid,lambda d:d['fission_preparation']['segments'].setdefault(sid,{}).update(**fields))
    try:
        with _reason_slots:
            status(status='preparing',phase='plan',error=None)
            data=store.load(pid);directory=store.project_dir(pid);state=edits.state_for(data,sid)
            reusable=(preserve_plan and state.get('plan') and state.get('storyboard_sig')==edits.board_sig(data,sid)
                      and edits.settings_match(state.get('settings_sig'),data,sid,directory,edits.materials_for(data,sid,directory)))
            if not reusable:
                edits.generate_plan(pid,sid,{'request':state.get('request','')} if preserve_plan else {})
            status(status='preparing',phase='preview',error=None)
            record=edits.preview(pid,sid,{})
        if record.get('state')!='pending_confirmation':raise ValueError('新分镜预览没有停在等付费确认，请重试')
        status(status='pending_confirmation',image_id=record['id'],error=None)
    except Exception as exc:
        try:status(status='failed',error=user_message(exc))
        except store.ProjectDeletedError:pass


def schedule_prepare(pid, segment_ids=None):
    data=store.load(pid);states=(data.get('fission_preparation') or {}).get('segments') or {}
    selected=list(segment_ids if segment_ids is not None else states)
    def work():
        for sid in selected:prepare_segment(pid,sid)
    return jobs.start(pid,'fission-prepare',work,'准备裂变新分镜')


def retry(pid, body):
    with store._lock(pid):
        data=store.load(pid)
        if not data.get('derived_from'):raise ValueError('这不是裂变项目')
        states=(data.get('fission_preparation') or {}).get('segments') or {};sid=body.get('segment_id')
        if sid and sid not in states:raise ValueError('找不到这一段，请刷新页面')
        if sid and states[sid].get('status')=='pending_confirmation':
            raise ValueError('这一段已准备好，请到第 04 步确认生图')
        selected=[sid] if sid else [key for key,value in states.items() if value.get('status')!='pending_confirmation']
        if not selected:raise ValueError('新分镜已准备好，请到第 04 步确认生图')
        schedule_prepare(pid,selected)
    return {'project_id':pid,'status':'preparing'}
