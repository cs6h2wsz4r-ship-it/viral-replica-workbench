"""Synthetic V2 Step04/05 prerequisites shared by the historical director tests."""
import copy

from console import segment_plan, segment_boards, storyboard_edits, storyboard_images, store


CONTENT_KEYS = ('id','what','check','prompt')


def default_groups(data):
    groups=[];current=[];start=0
    for cell in data['storyboard']['cells']:
        if current and (len(current)==6 or cell['end']-start>15):
            groups.append(current);current=[]
        if not current:start=cell['start']
        current.append(cell['id'])
    if current:groups.append(current)
    return groups


def configure(data, directory, groups=None, choices=None, skip_cells=None):
    """Save an explicit Step04 choice and synthesize corresponding approved boards."""
    groups=default_groups(data) if groups is None else groups
    raw=dict(summary='按完整求助事件复刻',why_segments='按完整事件、地点和商品状态分段',
             skip_cells=skip_cells or [],questions=['呼救能力不确定'],segments=[])
    for i,ids in enumerate(groups,1):
        sid=f'd{i}'
        raw['segments'].append(dict(id=sid,cells=ids,title='父子求助',what='父亲举腕，儿子扶稳父亲。',
                                   mode='video',mode_reason='保留动作节奏',refs=['C1','C2','product']))
        raw['segments'][-1].update((choices or {}).get(sid,{}))
        raw['segments'][-1].setdefault('gen_images',[ids[0],*raw['segments'][-1]['refs'][:3]])
    data['segments_plan']=segment_plan.validate_plan(raw,data,directory)
    data['segments_plan']['inputs_sig']=segment_plan.inputs_sig(data,directory)
    data['segment_boards']={'segments':segment_boards.build_sources(data,directory)}
    for source in segment_boards.sheets(data):
        sid=source['id'];state=segment_boards.state_for(data,sid)
        _,cells=segment_boards.sheet_cells(data,sid)
        files={c['id']:f'analysis/storyboard/new-{c["id"]}.png' for c in cells}
        clean=directory/f'analysis/segments/{sid}/fixture-clean.png'
        labeled=directory/f'analysis/segments/{sid}/fixture.jpg'
        storyboard_images.compose(cells,files,directory,clean,labeled,source['cell_size'],layout=source['layout'])
        bsig=storyboard_edits.board_sig(data,sid);segment=segment_plan.get_segment(data,sid)
        materials={m['key']:m['enabled'] for m in storyboard_edits.material_options(data,sid,directory)}
        ssig=segment_plan.segment_sig(segment)
        state.update(current='v1',storyboard_sig=bsig,segment_sig=ssig,materials_segment_sig=ssig,
                     materials=materials,settings_sig=storyboard_edits.settings_sig(data,sid,directory,materials),
                     plan={'cells':[dict(cell=c['id'],changes='替换父子和手表',keep='场景光线') for c in cells],
                           'sig':'plan','remove_text':True},versions=[dict(id='v1',file=str(labeled.relative_to(directory)),
                           clean_file=str(clean.relative_to(directory)),cells=files,storyboard_sig=bsig,
                           plan_sig='plan',segment_sig=ssig,slicer=2,layout=source['layout'])])
    store.update(data['id'],lambda d:d.update(copy.deepcopy(data)))
    return data


def choose(data,directory,sid,**changes):
    groups=[s['cells'] for s in data['segments_plan']['segments']]
    choices={s['id']:{k:s[k] for k in ('title','what','mode','mode_reason','refs','gen_images')} for s in data['segments_plan']['segments']}
    choices[sid].update(changes)
    if 'refs' in changes and 'gen_images' not in changes:
        choices[sid]['gen_images']=[next(s['cells'][0] for s in data['segments_plan']['segments'] if s['id']==sid),*changes['refs'][:3]]
    return configure(data,directory,groups,choices,data['segments_plan'].get('skip_cells'))
