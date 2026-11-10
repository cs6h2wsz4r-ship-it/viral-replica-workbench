"""Multi-panel revision using one reasoning call and one unpaid image preview."""
from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path

from PIL import Image
from . import gateway, llm, segment_boards, segment_plan, store, storyboard, storyboard_images
from . import storyboard_edits as edits


SYSTEM = '''You revise selected panels of an existing storyboard. Only the listed panels change; keep the others exactly as they are.
Use the current target storyboard as the editing base. Turn the user's request into complete, consistent changes for each selected panel. Preserve unaffected framing, subjects, setting and lighting. References describe the available replacement materials; refer to them by their material keys, never historical image numbers.
Each panel depicts one instant. Preserve the panel layout and order. Do not add text unless explicitly requested. Do not change unselected panels.
Return only JSON: {"why":"Brief explanation","cells":[{"cell":"selected panel ID","changes":"Complete revised changes","keep":"Consistent elements to preserve"}]}. Include each selected panel exactly once.'''


def selection(body):
    cells=body.get('cells')
    if not isinstance(cells,list) or not cells or any(not isinstance(cid,str) or not cid for cid in cells) or len(set(cells))!=len(cells):
        raise ValueError('请选择同一段里要重画的镜头')
    if body.get('cell_id') and cells!=[body['cell_id']]:raise ValueError('单镜和多镜选择不一致，请重新选择')
    return cells


def _inputs(data,sid,body,directory,exclude_image=None):
    selected=selection(body)
    if not segment_boards.has_segment(data,sid):raise ValueError('一次只能选同一段里的镜头')
    _,cells=edits.sheet_cells(data,sid);ids=[c['id'] for c in cells]
    if len(selected)<2 or any(cid not in ids for cid in selected):raise ValueError('一次只能选同一段里的镜头')
    if set(selected)&set((data.get('segments_plan') or {}).get('skip_cells',[])):raise ValueError('已跳过的镜头不能重画，请重新选择')
    selected=[cid for cid in ids if cid in selected]
    _,current=edits._cell_inputs(data,sid,{'cell_id':selected[0],'request':body.get('request')},directory)
    for cid in ids:
        path=(directory/current.get('cells',{}).get(cid,'')).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file():raise ValueError('请先生成整张新分镜')
    for record in data.get('images',{}).values():
        context=record.get('storyboard_context') or {}
        if record.get('id')==exclude_image or context.get('sheet_id')!=sid:continue
        if record.get('state') in ('running','query_pending','submitting'):
            raise ValueError('这段分镜正在生成，完成后再选择镜头')
        # Closing a confirmation is local UI state. A new unpaid preview may
        # replace it; revision signatures prevent paying for the older one.
    return cells,selected,current


def _binding(data,sid,selected,current,directory):
    materials=edits.materials_for(data,sid,directory)
    photos=[{**m,'sha256':store.sha256_file(directory/m['file'])} for m in edits.material_options(data,sid,directory)]
    return edits.digest([edits.board_sig(data,sid),edits.state_for(data,sid)['plan'],
                         edits.settings_sig(data,sid,directory,materials),photos,selected,current,
                         [(cid,store.sha256_file(directory/file)) for cid,file in current['cells'].items()]])


def reasoning_messages(data,sid,selected,current,request,directory):
    plans=[dict(cell=cid,**{k:edits.cell_revision(data,sid,cid,directory)[k] for k in ('changes','keep')}) for cid in selected]
    parts=[dict(type='text',text='User request: '+request+'\nSelected panels: '+', '.join(selected)+
                '\nCurrent plans: '+json.dumps(plans,ensure_ascii=False)+
                '\nImage 1: the complete current target storyboard. Only the selected panels may change.')]
    parts+=llm.image_part(directory/current['clean_file'])
    materials=edits.materials_for(data,sid,directory)
    enabled=[m for m in edits.material_options(data,sid,directory) if materials.get(m['key'])]
    for number,m in enumerate(enabled,2):
        parts.append(dict(type='text',text=f'Image {number}: material {m["key"]}; {m["name"]}; description: {m["description"]}'))
        parts+=llm.image_part(directory/m['file'])
    checks=(data.get('replacements') or {}).get('product',{}).get('story_checks',[])
    parts.append(dict(type='text',text='Confirmed product information: '+json.dumps(checks,ensure_ascii=False)))
    return [dict(role='system',content=SYSTEM),dict(role='user',content=parts)]


def image_prompt(data,sid,refs,revision):
    _,cells=edits.sheet_cells(data,sid);cols,rows=edits._layout(data,sid)
    changes={c['cell']:c for c in revision['cells']}
    text=f'''Edit Image 1, the complete current target storyboard, with {cols} columns and {rows} rows.
Only the listed panels change; keep the others exactly as they are. Preserve the exact number, order, positions, sizes, gutters and margins of all panels. Do not merge, add or remove panels.
Use the reference materials only for the changes explicitly requested. Preserve all unaffected subjects, framing, camera angle, setting and lighting. Each panel depicts one instant.
References:
{edits._references_text(data,refs,single=True)}
Panel changes (row by row, left to right):'''
    for index,cell in enumerate(cells):
        change=changes.get(cell['id'])
        line=(change['changes']+'. Keep: '+change['keep']) if change else 'No change. Keep this panel exactly as it is.'
        text+=f'\n- Panel {index+1} ({cell["id"]}; row {index//cols+1}, col {index%cols+1}): {line}'
    return text+'\nPreserve existing text unless a selected panel explicitly requests a change. Do not add panel labels, captions or borders.'


def preview(pid,sid,body):
    directory=store.project_dir(pid)
    with store._lock(pid):
        data=store.load(pid);cells,selected,current=_inputs(data,sid,body,directory)
        request=body['request'].strip();binding=_binding(data,sid,selected,current,directory)
        previous=edits.digest(edits.state_for(data,sid).get('multi_revision'))
        previous_cells=edits.digest([edits.cell_revision(data,sid,cid,directory) for cid in selected])
    value,_=gateway.reason_checked('storyboard_plan',reasoning_messages(data,sid,selected,current,request,directory),project=pid,max_tokens=5000)
    rows=value.get('cells') if isinstance(value,dict) else None
    if (not isinstance(value,dict) or not isinstance(value.get('why'),str) or not value['why'].strip()
            or not isinstance(rows,list) or len(rows)!=len(selected)
            or any(not isinstance(row,dict) or any(not isinstance(row.get(key),str) for key in ('cell','changes','keep')) or not row['changes'].strip() for row in rows)
            or sorted(row['cell'] for row in rows)!=sorted(selected)):
        raise ValueError('整理这些镜头的改法失败：模型返回的改法不完整，请重试')
    byid={row['cell']:row for row in rows}
    revision=dict(why=value['why'].strip(),cells=[{key:byid[cid][key].strip() for key in ('cell','changes','keep')} for cid in selected],
                  revised_by_request=request,inputs_sig=binding,created_at=datetime.now().astimezone().isoformat())
    revision['sig']=edits.digest(revision)
    with store._lock(pid):
        data=store.load(pid);cells,selected,current=_inputs(data,sid,body,directory);state=edits.state_for(data,sid)
        if (_binding(data,sid,selected,current,directory)!=binding or edits.digest(state.get('multi_revision'))!=previous
                or edits.digest([edits.cell_revision(data,sid,cid,directory) for cid in selected])!=previous_cells):
            raise ValueError('这段的方案、图片或素材刚刚改过，请重新整理改法')
        materials=edits.materials_for(data,sid,directory);options=edits.material_options(data,sid,directory)
        mentioned={m['key'] for cell in cells if cell['id'] in selected
                   for m in edits.cell_materials(data,sid,cell,request+'\n'+byid[cell['id']]['changes'],materials,directory)}
        options=[m for m in options if materials.get(m['key']) or m['key'] in mentioned]
        folder=edits._folder(data,sid,directory);folder.mkdir(parents=True,exist_ok=True)
        token=store.new_id('input');first=folder/f'{token}.png';clean=folder/f'{token}-clean.png';labeled=folder/f'{token}-labeled.jpg'
        with Image.open(directory/current['cells'][cells[0]['id']]) as picture:size=list(picture.size)
        cols,rows=edits._layout(data,sid)
        storyboard_images.compose(cells,current['cells'],directory,clean,labeled,size,layout=(cols,rows))
        with Image.open(clean) as picture:padding=storyboard_images.padded(picture,first,False)
        labeled.unlink()
        refs=[dict(key='current',name='本段当前新分镜整张图（已补白）',path=str(first))]
        refs += [dict(key=m['key'],name=m['name'],description=m['description'],asset_id=m['asset_id'],path=str(directory/m['file'])) for m in options]
        prompt=image_prompt(data,sid,refs,revision);assets=[];ids=[]
        for number,ref in enumerate(refs,1):
            frozen=folder/f'{token}-ref-{number}{Path(ref["path"]).suffix}'
            edits.shutil_copy(Path(ref['path']),frozen);ref.update(path=str(frozen),file=store.rel(pid,frozen));aid=f'{token}-{number}';ids.append(aid)
            assets.append(dict(id=aid,type='image',roles=['storyboard'],label=ref['name'],internal=True,file=ref['file'],sha256=store.sha256_file(frozen)))
        context=dict(sheet_id=sid,segment_id=sid,cell_id=None,selected_cells=selected,cells=copy.deepcopy(cells),
                     cell_size=size,sheet_size=[size[0]*cols+storyboard.GAP*(cols-1),size[1]*rows+storyboard.GAP*(rows-1)],layout=[cols,rows],
                     padding=padding,references=refs,storyboard_sig=edits.board_sig(data,sid),plan_sig=state['plan']['sig'],
                     settings_sig=edits.settings_sig(data,sid,directory,materials),segment_sig=segment_plan.visual_segment_sig(data,segment_plan.get_segment(data,sid)),
                     base_current=current['id'],base_cells=copy.deepcopy(current['cells']),base_resliced_at=current.get('resliced_at'),
                     cells_revision=copy.deepcopy(revision),cells_revision_sig=revision['sig'])
        def commit(d):
            d['assets'].extend(assets);entry=edits.state_for(d,sid);entry['multi_revision']=copy.deepcopy(revision)
            for row in revision['cells']:
                entry.setdefault('cell_revisions',{})[row['cell']]={**copy.deepcopy(row),**{k:revision[k] for k in ('why','revised_by_request','created_at','sig')},
                    'base_plan_sig':state['plan']['sig'],'storyboard_sig':context['storyboard_sig'],'settings_sig':context['settings_sig']}
        store.update(pid,commit)
        rec=gateway.image_preview('storyboard_image',pid,prompt,ids,'2:3',None,'2k',f'storyboard-cells:{sid}',
                                  **({'override':'third_party:gpt-image-2'} if body.get('channel')=='third_party' else {}))
        rec.update(storyboard_context=context,warnings=['提示词超过 4000 字，请检查完整内容后再确认'] if len(prompt)>4000 else [])
        store.update(pid,lambda d:d['images'][rec['id']].update(storyboard_context=context,warnings=rec['warnings']))
        store.log(pid,f'新分镜 {sid} 的 {len(selected)} 镜改法已整理，1 张生图预览已准备好，等待确认')
        return rec


def verify(pid,record):
    data=store.load(pid);directory=store.project_dir(pid);ctx=record['storyboard_context'];sid=ctx['sheet_id']
    revision=edits.state_for(data,sid).get('multi_revision') or {}
    if revision.get('sig')!=ctx.get('cells_revision_sig'):raise ValueError('这些镜头的改法改过，请重新整理并生成预览')
    revisions=edits.state_for(data,sid).get('cell_revisions') or {}
    if any(revisions.get(cid,{}).get('sig')!=revision.get('sig') for cid in ctx['selected_cells']):
        raise ValueError('其中一镜的改法已更新，请重新整理并生成预览')
    _,selected,current=_inputs(data,sid,dict(cells=ctx['selected_cells'],request=revision.get('revised_by_request')),directory,record['id'])
    if current['id']!=ctx.get('base_current') or current.get('resliced_at')!=ctx.get('base_resliced_at') or _binding(data,sid,selected,current,directory)!=revision.get('inputs_sig'):
        raise ValueError('这段的方案、图片或素材改过，请重新整理改法并生成预览')
