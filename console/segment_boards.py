"""Per-segment source boards and independent edit state, without frame extraction."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from PIL import Image

from . import segment_plan, store

STALE_WARNING = '第 04 步（对照和修改）的分段方案改过这一段，请重新生成这一段的新分镜。'


def layout(count):
    if not isinstance(count,int) or isinstance(count,bool) or not 1<=count<=6:
        raise ValueError('每段需要 1–6 格')
    return (2,1) if count<=2 else (2,2) if count<=4 else (3,2)


def has_segment(data, sid):
    return any(s.get('id')==sid for s in (data.get('segments_plan') or {}).get('segments',[]))


def state_for(data, sid):
    return (data.get('segment_boards') or {}).get('segments',{}).get(sid) or {}


def current(data, sid):
    state=state_for(data,sid)
    return next((v for v in state.get('versions',[]) if v.get('id')==state.get('current')),None)


def sheets(data):
    return [state_for(data,s['id'])['source'] for s in (data.get('segments_plan') or {}).get('segments',[])
            if state_for(data,s['id']).get('source')]


def sheet_cells(data, sid):
    segment=segment_plan.get_segment(data,sid);byid={c['id']:c for c in (data.get('storyboard') or {}).get('cells',[])}
    source=state_for(data,sid).get('source')
    if not source or any(cid not in byid for cid in segment['cells']):raise ValueError('先在第 04 步（对照和修改）生成分段方案')
    return source,[byid[cid] for cid in segment['cells']]


def source_sig(data, sid, directory):
    segment=segment_plan.get_segment(data,sid);board=data.get('storyboard') or {};byid={c['id']:c for c in board.get('cells',[])}
    cells=[byid.get(cid,{'id':cid}) for cid in segment['cells']]
    return segment_plan.digest([board.get('built_at'),board.get('source_sig'),cells,
                                [segment_plan._hash(directory,c.get('file')) for c in cells]])


def board_sig(data, sid):
    segment=segment_plan.get_segment(data,sid);board=data.get('storyboard') or {};byid={c['id']:c for c in board.get('cells',[])}
    cells=[byid.get(cid,{'id':cid}) for cid in segment['cells']]
    return segment_plan.digest([segment_plan.visual_segment_sig(data,segment),board.get('built_at'),board.get('source_sig'),cells])


def _latest_generation(state):
    times=[]
    for version in state.get('versions',[]):
        try:
            stamp=datetime.fromisoformat(version.get('created_at','').replace('Z','+00:00'))
            times.append((stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)).timestamp())
        except (ValueError, TypeError, AttributeError, OverflowError):
            continue
    return max(times,default=float('-inf'))


def reuse_for_plan(data, plan, directory):
    """Rebind Step05 only during an explicit Step04 generation, never on reads.

    Historical sources carry a signature including their old ID. Match all
    visual fields with that ID, preserving the existing anchor across mode-only
    changes without guessing history or invalidating paid versions.
    """
    previous=(data.get('segment_boards') or {}).get('segments') or {}
    states=copy.deepcopy(previous)
    updated={**data,'segments_plan':plan}
    active={s['id'] for s in (data.get('segments_plan') or {}).get('segments',[])}
    for segment in plan['segments']:
        sid=segment['id'];matches=[]
        for old_id,state in previous.items():
            old_segment={**segment,'id':old_id}
            if (state.get('source') or {}).get('segment_sig')==segment_plan.visual_segment_sig(data,old_segment):
                matches.append((old_id,state))
        if not matches:
            # A reused sequential key must not carry a different segment's board.
            states[sid]={}
            continue
        old_id,original=max(matches,key=lambda item:(_latest_generation(item[1]),
            bool(item[1].get('versions')),item[0] in active,item[0]==sid))
        state=copy.deepcopy(original);old_segment={**segment,'id':old_id}
        old_data={**data,'segments_plan':{'segments':[old_segment]}}
        old_sig=segment_plan.visual_segment_sig(data,old_segment);new_sig=segment_plan.visual_segment_sig(updated,segment)
        old_board=board_sig(old_data,old_id);new_board=board_sig(updated,sid)
        source=state['source']
        source_matches=source.get('source_sig')==source_sig(updated,sid,directory)
        for item in [state,*state.get('versions',[])]:
            for key in ('segment_sig','materials_segment_sig'):
                if item.get(key)==old_sig:item[key]=new_sig
            if item.get('storyboard_sig')==old_board:
                # Rebuilding the source must not make an old paid board fresh
                # after frame bytes change without a metadata change.
                item['storyboard_sig']=(new_board if source_matches else
                    segment_plan.digest(['stale-source',old_board,source.get('source_sig')]))
        if old_id!=sid:
            for version in state.get('versions',[]):
                version.setdefault('source_sheet_id',old_id)
        source.update(id=sid,segment_sig=new_sig)
        state.pop('reused_after_replan',None)
        if state.get('versions'):
            state['reused_after_replan']=dict(from_id=old_id,generated_at=plan.get('generated_at'))
        states[sid]=state
    return states


def require_idle(data):
    for record in (data.get('images') or {}).values():
        context=record.get('storyboard_context') or {}
        if context.get('segment_id') and record.get('state') in ('running','query_pending','submitting'):
            raise ValueError('第 04 步（对照和修改）还有新分镜正在生成，请等生成完成后再重新生成分段方案。')


def build_sources(data, directory):
    from . import storyboard_images
    states=copy.deepcopy((data.get('segment_boards') or {}).get('segments') or {})
    byid={c['id']:c for c in (data.get('storyboard') or {}).get('cells',[])}
    for segment in (data.get('segments_plan') or {}).get('segments',[]):
        sid=segment['id'];cells=[byid[cid] for cid in segment['cells']];cols,rows=layout(len(cells))
        if not store.SAFE_ID.fullmatch(sid):raise ValueError('段落 ID 不安全')
        sig=source_sig(data,sid,directory);state=states.setdefault(sid,{})
        existing=state.get('source') or {}
        visual_sig=segment_plan.visual_segment_sig(data,segment)
        if existing.get('segment_sig')!=visual_sig:
            state.pop('reused_after_replan',None)
        if existing.get('source_sig')==sig and all(segment_plan._hash(directory,existing.get(k))!='missing' for k in ('file','clean_file')):
            state['source']={**existing,'segment_sig':visual_sig}
            continue
        folder=directory/'analysis/segments'/sid;folder.mkdir(parents=True,exist_ok=True)
        # Content-keyed filenames keep a rejected save or an in-flight preview from replacing its inputs.
        clean=folder/f'source-{sig[:16]}-clean.png';labeled=folder/f'source-{sig[:16]}.jpg'
        with Image.open(segment_plan._file(directory,cells[0]['file'])) as first:size=list(first.size)
        files={c['id']:str(segment_plan._file(directory,c['file'])) for c in cells}
        storyboard_images.compose(cells,files,directory,clean,labeled,size,layout=(cols,rows))
        state['source']=dict(id=sid,cells=list(segment['cells']),file=labeled.relative_to(directory).as_posix(),
            clean_file=clean.relative_to(directory).as_posix(),cols=cols,rows=rows,layout=[cols,rows],cell_size=size,
            source_sig=sig,segment_sig=visual_sig)
    return states


def build(pid):
    directory=store.project_dir(pid)
    return store.update(pid,lambda data:data.update(segment_boards={'segments':build_sources(data,directory)}))


def warnings(data,sid,directory):
    from . import storyboard_edits
    return storyboard_edits.warnings(data,sid,directory)
