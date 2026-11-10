"""Local padding, result-based slicing, and free storyboard version repair."""
from __future__ import annotations

import math
import copy
import tempfile
from itertools import combinations
import shutil
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageChops
from . import store, storyboard


def padded(image, target, landscape=False, *, layout=None):
    image = image.convert('RGB')
    rw,rh = (3,2) if landscape else (2,3)
    unit = math.ceil(max(image.width/rw,image.height/rh))
    size = (rw*unit,rh*unit)
    left,top = (size[0]-image.width)//2,(size[1]-image.height)//2
    canvas = Image.new('RGB',size,'white'); canvas.paste(image,(left,top)); canvas.save(target)
    return dict(size=list(size),left=left,top=top,width=image.width,height=image.height)


def compose(cells, files, directory, clean_path, labeled_path, size, *, layout=None):
    width,height = size
    cols,rows = layout or (3,3)
    if cols<1 or rows<1 or len(cells)>cols*rows:raise ValueError('分镜版式容纳不下这些格子')
    clean = Image.new('RGB',(width*cols+storyboard.GAP*(cols-1),height*rows+storyboard.GAP*(rows-1)),'white')
    labeled = clean.copy()
    for i,cell in enumerate(cells):
        with Image.open(directory/files[cell['id']]) as img:
            tile = img.convert('RGB').resize((width,height),Image.Resampling.LANCZOS)
        xy = ((i%cols)*(width+storyboard.GAP),(i//cols)*(height+storyboard.GAP))
        clean.paste(tile,xy);labeled.paste(storyboard.label_tile(tile,cell),xy)
    clean.save(clean_path);labeled.save(labeled_path,quality=90)


def _white_bands(image):
    """Inspect every pixel of the result; bands use half-open source coordinates."""
    r,g,b = image.convert('RGB').split()
    white = ImageChops.darker(ImageChops.darker(r,g),b).point(lambda v: int(v>=235)).tobytes()
    w,h = image.size
    def runs(counts, length):
        bands=[]
        for n,count in enumerate(counts):
            if count*10 < length*9: continue
            if bands and bands[-1][1]==n: bands[-1][1]=n+1
            else: bands.append([n,n+1])
        return bands
    return (runs((sum(white[x::w]) for x in range(w)),h),
            runs((sum(white[y*w:(y+1)*w]) for y in range(h)),w))


def _axis(bands, extent, divisions):
    lo = bands[0][1] if bands and bands[0][0]==0 else 0
    hi = bands[-1][0] if bands and bands[-1][1]==extent else extent
    if hi<=lo: raise ValueError('模型原图是空白的，没法重新切格')
    internal=[b for b in bands if lo<b[0] and b[1]<hi]
    separators=[]
    if divisions>1 and len(internal)>=divisions-1:
        targets=[lo+(hi-lo)*i/divisions for i in range(1,divisions)]
        separators=list(min(combinations(internal,divisions-1),key=lambda pair:sum(abs(sum(b)/2-t) for b,t in zip(pair,targets))))
        spans=list(zip([lo]+[b[1] for b in separators],[b[0] for b in separators]+[hi]))
    elif divisions>1:
        cuts=[round(lo+(hi-lo)*i/divisions) for i in range(divisions+1)]
        spans=list(zip(cuts,cuts[1:]))
    else: spans=[(lo,hi)]
    return lo,hi,separators,spans


def _center_crop(box, size):
    x0,y0,x1,y1=box;w,h=x1-x0,y1-y0
    if w<=0 or h<=0: raise ValueError('模型原图太小，没法切出完整的一格')
    ratio=size[0]/size[1]
    cw,ch=min(w,h*ratio),min(h,w/ratio)
    return [x0+(w-cw)/2,y0+(h-ch)/2,x1-(w-cw)/2,y1-(h-ch)/2]


def slice_result(image, size, cells, cell_id=None, *, layout=None, selected_cells=None):
    """Detect result margins/gutters before cropping; input padding is irrelevant."""
    image=image.convert('RGB');xb,yb=_white_bands(image)
    # Entire unused rows/columns can join the trailing outer white band. Only
    # account for those blank slots if a sufficiently broad band was detected;
    # otherwise preserve the specified full 3x3 fallback, even on sparse sheets.
    grid_cols,grid_rows=layout or (3,3)
    if grid_cols<1 or grid_rows<1 or len(cells)>grid_cols*grid_rows:raise ValueError('分镜版式容纳不下这些格子')
    def divisions(bands, extent, occupied, maximum):
        blank_tail=(bands and bands[-1][1]==extent and
                    extent-bands[-1][0]>=extent*(maximum-occupied)/(maximum*2))
        return occupied if occupied<maximum and blank_tail else maximum
    columns=1 if cell_id else divisions(xb,image.width,min(grid_cols,len(cells)),grid_cols)
    rows=1 if cell_id else divisions(yb,image.height,math.ceil(len(cells)/grid_cols),grid_rows)
    left,right,xg,xs=_axis(xb,image.width,columns)
    top,bottom,yg,ys=_axis(yb,image.height,rows)
    info=dict(source_size=list(image.size),margins=dict(left=left,top=top,right=image.width-right,bottom=image.height-bottom),
              separators=dict(vertical=xg,horizontal=yg),white_bands=dict(vertical=xb,horizontal=yb),
              method=dict(x='separators' if xg else 'equal' if not cell_id else 'single',
                          y='separators' if yg else 'equal' if not cell_id else 'single'),cells={})
    tiles={}
    for i,cell in enumerate(cells):
        cid=cell['id']
        if cell_id and cid!=cell_id: continue
        if selected_cells is not None and cid not in selected_cells:continue
        x,y=(xs[0],ys[0]) if cell_id else (xs[i%grid_cols],ys[i//grid_cols])
        bounds=[x[0],y[0],x[1],y[1]];crop=_center_crop(bounds,size)
        tiles[cid]=image.resize(tuple(size),Image.Resampling.LANCZOS,box=tuple(crop))
        info['cells'][cid]=dict(bounds=bounds,crop=crop)
    return tiles,info


def split_sheet(image, size, count, *, layout=None):
    tiles,_=slice_result(image,size,[dict(id=f'c{i+1:02d}') for i in range(count)],layout=layout)
    return list(tiles.values())


def _render(directory, folder, prefix, context, output):
    with Image.open(output) as image:
        tiles,info=slice_result(image,context['cell_size'],context['cells'],context.get('cell_id'),layout=context.get('layout'),
                               **({'selected_cells':context['selected_cells']} if context.get('selected_cells') else {}))
    files={}
    for cell in context['cells']:
        cid=cell['id'];target=folder/f'{prefix}-{cid}.jpg'
        if cid in tiles: tiles[cid].save(target,quality=90)
        else: shutil.copyfile(_local_file(directory,context['base_cells'][cid]),target)
        files[cid]=target
    clean=folder/f'{prefix}-clean.png';labeled=folder/f'{prefix}.jpg'
    compose(context['cells'],files,directory,clean,labeled,context['cell_size'],layout=context.get('layout'))
    return files,clean,labeled,info


def _local_file(directory, file):
    path=(directory/file).resolve()
    if not path.is_relative_to(directory.resolve()) or not path.is_file():
        raise ValueError('这一版的模型原图或单格找不到了，请刷新页面')
    return path


def complete(pid, record, output):
    """Called by the existing image bridge after a successful paid image job."""
    with store._lock(pid):
        data=store.load(pid);context=record['storyboard_context'];sid=context['sheet_id']
        segment=bool(context.get('segment_id'))
        state=(data.get('segment_boards',{}).get('segments',{}) if segment else data.get('storyboard_edits',{}).get('sheets',{})).get(sid)
        if not state: raise ValueError('这张分镜的方案找不到了，请刷新页面')
        if any(v.get('image_id')==record['id'] for v in state.get('versions',[])):return
        directory=store.project_dir(pid);folder=directory/(f'analysis/segments/{sid}/edits' if segment else 'analysis/storyboard/edits');folder.mkdir(parents=True,exist_ok=True)
        number=max([int(v['id'][1:]) for v in state.get('versions',[])]+[0])+1
        vid=f'v{number}';prefix=f'edit-{sid}-{vid}'
        files,clean,labeled,info=_render(directory,folder,prefix,context,output)
        cid=context.get('cell_id')
        selected=context.get('selected_cells')
        base=next((v for v in state.get('versions',[]) if v['id']==context.get('base_current')),None)
        # A job submitted before the slicer upgrade may still complete now.
        # Its untouched legacy cells need repair too; keep the repair notice.
        slicer=2 if not (cid or selected) or (base and base.get('slicer')==2) else 1
        version=dict(id=vid,image_id=record['id'],**{'from':'cells:'+','.join(selected) if selected else f'cell:{cid}' if cid else 'full'},
            clean_file=store.rel(pid,clean),file=store.rel(pid,labeled),cells={cid:store.rel(pid,p) for cid,p in files.items()},
            plan_sig=context['plan_sig'],created_at=datetime.now().astimezone().isoformat(),storyboard_sig=context['storyboard_sig'],
            slicer=slicer,slicing=info,base_version=context.get('base_current'))
        if segment:version.update(layout=context['layout'],segment_sig=context.get('segment_sig'),settings_sig=context.get('settings_sig'))
        if selected:version['selected_cells']=list(selected)
        partial=bool(cid) or bool(selected and len(selected)<len(context['cells']))
        if segment and partial and base and base.get('settings_sig') and base['settings_sig']!=context.get('settings_sig'):
            # A single new cell cannot refresh the casting of untouched cells.
            version['settings_sig']=base['settings_sig']
            if 'settings_materials' in base:version['settings_materials']=copy.deepcopy(base['settings_materials'])
        def commit(d):
            entry=d['segment_boards']['segments'][sid] if segment else d['storyboard_edits']['sheets'][sid]
            entry.setdefault('versions',[]).append(version);entry['current']=vid
            entry.pop('reused_after_replan',None)
            if segment:
                from . import fix_plan
                fix_plan.complete(data,d,sid,record,directory)
        store.update(pid,commit)
        store.log(pid,f'新分镜 {sid} 第 {number} 版已生成')


def reslice(pid, sid, vid):
    """Repair one stored version locally from its model output, without a new job."""
    with store._lock(pid):
        from . import segment_boards
        data=store.load(pid);segment=segment_boards.has_segment(data,sid)
        state=segment_boards.state_for(data,sid) if segment else data.get('storyboard_edits',{}).get('sheets',{}).get(sid,{})
        versions=state.get('versions',[])
        index=next((i for i,v in enumerate(versions) if v['id']==vid),None)
        if index is None: raise ValueError('这一版找不到了，请刷新页面')
        version=versions[index];record=data.get('images',{}).get(version.get('image_id'),{})
        context=copy.deepcopy(record.get('storyboard_context'))
        source_sid=version.get('source_sheet_id',sid) if segment else sid
        if not context or context.get('sheet_id')!=source_sid: raise ValueError('这一版的切格记录找不到了，请刷新页面')
        directory=store.project_dir(pid)
        # Prefer the saved original result. The asset copy is a lossless fallback.
        output=None
        for file in ([str(Path(record['run_dir'])/'result.png')] if record.get('run_dir') else [])+[record.get('result')]:
            if not file: continue
            try: output=_local_file(directory,file);break
            except ValueError: continue
        if output is None: raise ValueError('这一版的模型原图找不到了，没法重新切格')
        cid=version.get('from','').removeprefix('cell:') if version.get('from','').startswith('cell:') else None
        context['cell_id']=cid
        if cid or context.get('selected_cells'):
            base_id=version.get('base_version') or context.get('base_current')
            base=next((v for v in versions[:index] if v['id']==base_id),None) if base_id else (versions[index-1] if index else None)
            if not base or base.get('slicer')!=2: raise ValueError('先重新切上一版')
            context['base_cells']=base['cells']
        folder=directory/(f'analysis/segments/{sid}/edits' if segment else 'analysis/storyboard/edits');folder.mkdir(parents=True,exist_ok=True)
        prefix=f'edit-{sid}-{vid}'
        with tempfile.TemporaryDirectory(prefix='.reslice-',dir=folder) as temp:
            stage=Path(temp)
            files,clean,labeled,info=_render(directory,stage,prefix,context,output)
            staged=[*files.values(),clean,labeled];published=[]
            updated={**version,'cells':{cid:store.rel(pid,folder/p.name) for cid,p in files.items()},
                     'clean_file':store.rel(pid,folder/clean.name),'file':store.rel(pid,folder/labeled.name),
                     'slicer':2,'slicing':info,'resliced_at':datetime.now().astimezone().isoformat(timespec='microseconds')}
            try:
                for file in staged:
                    target=folder/file.name;backup=stage/(file.name+'.backup')
                    if target.exists(): shutil.copyfile(target,backup)
                    file.replace(target);published.append((target,backup))
                def commit(d):
                    entry=d['segment_boards']['segments'][sid] if segment else d['storyboard_edits']['sheets'][sid]
                    entry['versions'][index]=updated
                result=store.update(pid,commit)
            except Exception:
                for target,backup in reversed(published):
                    if backup.exists(): backup.replace(target)
                    else: target.unlink(missing_ok=True)
                raise
        return result
