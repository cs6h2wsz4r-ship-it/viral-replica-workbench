"""Free source storyboards: deterministic cuts, FFmpeg frames and Pillow sheets."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from . import media, store

GAP = 6
VERSION = 3


def product_names(breakdown):
    """Count normalized spellings, preserving the first original of the winner."""
    groups, variants = {}, []
    for row in breakdown.get('productMoments') or []:
        text = str(row.get('product') or '')
        key = re.sub(r'\s+', '', text).upper()
        if not key:
            continue
        if text not in variants: variants.append(text)
        if key not in groups: groups[key] = [text, 0]
        groups[key][1] += 1
    return dict(name=max(groups.values(), key=lambda v:v[1])[0] if groups else '', variants=variants)


def avoid_transitions(at, start, end, overlays, preferred=None):
    windows = sorted((float(row[k])-.25, float(row[k])+.25)
                     for row in overlays for k in ('start','end') if row.get(k) is not None)
    if not any(a <= at <= b for a,b in windows): return at
    def nearest(lo, hi):
        segments = [(lo, hi)]
        for a,b in windows:
            out = []
            for x,y in segments:
                if b < x or a > y: out.append((x,y)); continue
                if x < a: out.append((x,min(y,a-.00001)))
                if y > b: out.append((max(x,b+.00001),y))
            segments = [(x,y) for x,y in out if y>=x]
        candidates = [max(x,min(at,y)) for x,y in segments]
        return min(candidates,key=lambda t:(abs(t-at),t)) if candidates else None
    candidate = nearest(*preferred) if preferred else None
    if candidate is None: candidate = nearest(start,end-.00001)
    return at if candidate is None else candidate


def _interval(row):
    return float(row.get('start') or 0), float(row.get('end') or 0)


def _overlaps(row, start, end):
    a, b = _interval(row)
    return b > start and a < end


def _people(row):
    return sorted(set(str(c) for c in (row.get('characters') or []) if c))


def _join(values, *, unique=False):
    out = []
    for value in values:
        text = str(value or '').strip()
        if text and (not unique or text not in out):
            out.append(text)
    return '；'.join(out)


def product_at(frame_at, end, breakdown):
    moments = breakdown.get('productMoments') or []
    present = any(a <= frame_at < b for a,b in map(_interval,moments))
    later = not present and any(frame_at < a < end and b > a for a,b in map(_interval,moments))
    return dict(product=product_names(breakdown)['name'] if present else '', product_later=later)


def cell_data(start, end, breakdown):
    rows = sorted((s for s in breakdown.get('shots') or [] if _overlaps(s, start, end)), key=_interval)
    people = sorted({p for s in rows for p in _people(s)})
    full = next((s for s in rows if set(_people(s)) == set(people)), None)
    frame_at = (max(start, _interval(full)[0]) + min(end, _interval(full)[1])) / 2 if full else (start + end) / 2
    frame_at = avoid_transitions(frame_at,start,end,breakdown.get('overlays') or [],
        (max(start,_interval(full)[0]),min(end,_interval(full)[1])-.00001) if full else None)
    def text(key):
        return _join(r.get('text') or r.get('content') for r in sorted(breakdown.get(key) or [], key=_interval) if _overlaps(r,start,end))
    actions = [str(s.get('action') or '').strip() for s in rows if str(s.get('action') or '').strip()]
    frame_at = round(frame_at, 6)
    return dict(frame_at=frame_at, characters=people, **product_at(frame_at,end,breakdown),
                action=' → '.join(re.split('[。；]',a,maxsplit=1)[0][:40] for a in actions[:3]),
                action_full=_join(actions), dialogue=text('dialogue'), overlay=text('overlays'))


def cell_text(cell, breakdown):
    """Keep overlapping descriptions as evidence; the panel image is authoritative."""
    start, end = _interval(cell)
    rows = sorted((s for s in breakdown.get('shots') or [] if _overlaps(s, start, end)), key=_interval)
    return dict(action=_join(row.get('action') for row in rows),
                note='拆解文字可能混入相邻镜头的内容，以这一格的分镜图为准')


def plan_cells(merged_shots, breakdown=None):
    breakdown = breakdown or {}
    descriptions = sorted(breakdown.get('shots') or [], key=_interval)
    names = {c.get('id'): c.get('name') or '' for c in breakdown.get('characters') or []}
    cells = []
    for si, shot in enumerate(sorted(merged_shots, key=_interval), 1):
        start, end = _interval(shot)
        if not all(math.isfinite(x) for x in (start,end)) or end <= start or start < 0:
            raise ValueError('镜头时间不正确，请重新拆解')
        rows = [s for s in descriptions if start <= sum(_interval(s))/2 < end]
        split, reason = None, ''
        # Only the first change is considered, even when it is too close to an edge.
        for previous, current in zip(rows, rows[1:]):
            old, new = set(_people(previous)), set(_people(current))
            product_change = bool(previous.get('product')) != bool(current.get('product'))
            if old != new or product_change:
                at = _interval(current)[0]
                if at-start >= .5 and end-at >= .5:
                    changes = []
                    for ids, verb in ((new-old,'进画面'), (old-new,'离开画面')):
                        if ids:
                            changes.append('、'.join((cid+' '+names.get(cid,'')).strip() for cid in sorted(ids))+verb)
                    if product_change:
                        changes.append('商品出现' if current.get('product') else '商品消失')
                    split, reason = at, '镜头中途'+'，'.join(changes)
                break
        if split is None and end-start > 3:
            split, reason = (start+end)/2, '镜头较长，拆成两格'
        intervals = [(start,split),(split,end)] if split is not None else [(start,end)]
        owned = {**breakdown, 'shots': rows}
        for part, (a,b) in enumerate(intervals,1):
            index = len(cells)+1
            cells.append(dict(id=f'c{index:02d}',index=index,start=a,end=b,shot_index=shot.get('index',si),
                part=part if split is not None else None,split_reason=reason,**cell_data(a,b,owned),
                file=f'analysis/storyboard/cell-{index:02d}.jpg',manual=False))
    return cells


def _inputs(data, directory):
    source = (directory / (data.get('source') or {}).get('file','')).resolve()
    if not source.is_relative_to(directory.resolve()) or not source.is_file():
        raise ValueError('原片文件找不到，请先上传视频')
    path = directory / 'analysis/boundaries.json'
    if not path.is_file():
        raise ValueError('还没有镜头切点，请先拆解视频')
    merged = json.loads(path.read_text(encoding='utf-8')).get('merged_shots') or []
    if not merged:
        raise ValueError('没有可用的镜头切点，请重新拆解')
    return source, merged


def source_sig(data, directory):
    source, merged = _inputs(data, directory)
    # Include all captions/descriptions too: editing a breakdown must invalidate its labels.
    # Speech re-sentencing is independent of these source frames and labels.
    breakdown = {k:v for k,v in (data.get('breakdown') or {}).items()
                 if k not in ('speech_lines', 'speech_lines_state')}
    content = dict(merged_shots=merged, breakdown=breakdown, source=store.sha256_file(source))
    return hashlib.sha256(json.dumps(content,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def status(data, directory):
    board = data.get('storyboard')
    try:
        sig = source_sig(data,directory)
        stale = bool(board and board.get('source_sig') != sig)
    except (OSError,ValueError,TypeError):
        stale = bool(board)
    upgrade = bool(board and board.get('version',1) < VERSION)
    return dict(stale=stale,upgrade=upgrade,needs_rebuild=not board or stale or upgrade)


def _font(size):
    for path in ('/System/Library/Fonts/PingFang.ttc','/System/Library/Fonts/STHeiti Light.ttc',
                 '/System/Library/Fonts/STHeiti Medium.ttc'):
        try:
            return ImageFont.truetype(path,size)
        except (OSError,ValueError):
            pass
    return ImageFont.load_default()


def label_tile(source, cell):
    tile = source.convert('RGBA'); width = tile.width
    font = _font(max(12,round(width*.07)))
    label = f"{cell['index']}  {cell['start']:.1f}–{cell['end']:.1f}s"
    overlay = Image.new('RGBA',tile.size,(0,0,0,0)); draw = ImageDraw.Draw(overlay)
    try:
        box = draw.textbbox((0,0),label,font=font)
    except (UnicodeEncodeError,AttributeError):
        label = label.replace('–','-'); box = draw.textbbox((0,0),label,font=font)
    pad = max(4,round(width*.018)); label_height = box[3]-box[1]+pad*2
    draw.rectangle((0,0,min(width,box[2]+pad*2),label_height), fill=(0,0,0,175))
    draw.text((pad,pad-box[1]),label,font=font,fill='white')
    return Image.alpha_composite(tile,overlay).convert('RGB')


def render_sheets(cells, folder):
    sheets = []
    with Image.open(folder / Path(cells[0]['file']).name) as first:
        width, height = first.size
    for offset in range(0,len(cells),9):
        sheet = Image.new('RGB',(width*3+GAP*2,height*3+GAP*2),'white')
        clean = sheet.copy()
        chunk = cells[offset:offset+9]
        for local, cell in enumerate(chunk):
            with Image.open(folder / Path(cell['file']).name) as source:
                tile = source.convert('RGB')
            position = ((local%3)*(width+GAP),(local//3)*(height+GAP))
            clean.paste(tile,position)
            sheet.paste(label_tile(tile,cell),position)
        index = len(sheets)+1; filename = f'sheet-{index:02d}.jpg'
        sheet.save(folder/filename,quality=90)
        clean_name = f'sheet-{index:02d}-clean.png'; clean.save(folder/clean_name)
        sheets.append(dict(id=f's{index:02d}',cells=[c['id'] for c in chunk],file='analysis/storyboard/'+filename,clean_file='analysis/storyboard/'+clean_name))
    return sheets


def _publish(pid, data, board, stage):
    directory = store.project_dir(pid); target = directory/'analysis/storyboard'
    backup = stage.parent/(stage.name+'-previous')
    if (target/'edits').is_dir():
        shutil.copytree(target/'edits',stage/'edits')
    if target.exists():
        target.rename(backup)
    try:
        stage.rename(target)
        result = store.update(pid,lambda d:d.update(storyboard=board))
    except Exception:
        if target.exists(): shutil.rmtree(target)
        if backup.exists(): backup.rename(target)
        raise
    finally:
        if backup.exists(): shutil.rmtree(backup)
    return result


def build(pid):
    with store._lock(pid):
        data = store.load(pid); directory = store.project_dir(pid)
        source, merged = _inputs(data,directory)
        cells = plan_cells(merged,data.get('breakdown'))
        sig = source_sig(data,directory)
        stage = Path(tempfile.mkdtemp(prefix='.storyboard-',dir=directory/'analysis'))
        try:
            for cell in cells:
                path = stage/Path(cell['file']).name
                media.extract_frame(source,cell['frame_at'],path,width=540)
                with Image.open(path) as frame:
                    rgb = frame.convert('RGB')
                rgb.save(path,quality=90)
            board = dict(version=VERSION,built_at=datetime.now().astimezone().isoformat(timespec='microseconds'),source_sig=sig,
                         cells=cells,sheets=render_sheets(cells,stage),merges=[])
            return _publish(pid,data,board,stage)
        finally:
            if stage.exists(): shutil.rmtree(stage)


def merge(pid, cell_id):
    with store._lock(pid):
        data = store.load(pid); directory = store.project_dir(pid)
        if status(data,directory)['needs_rebuild']:
            raise ValueError('拆解结果变了，原片分镜要重新生成')
        board = copy.deepcopy(data['storyboard']); cells = board['cells']
        position = next((i for i,c in enumerate(cells) if c['id']==cell_id),-1)
        if position < 0 or position >= len(cells)-1:
            raise ValueError('请选择一格和它后面的一格合并')
        left, right = cells[position:position+2]
        longer = left if left['end']-left['start'] >= right['end']-right['start'] else right
        combined = {**left,'start':min(left['start'],right['start']),'end':max(left['end'],right['end']),
                    'frame_at':longer['frame_at'],'file':longer['file'],'manual':True,'part':None,
                    'characters':sorted(set(left['characters'])|set(right['characters'])),
                    **{k:_join([left.get(k),right.get(k)],unique=k=='split_reason') for k in ('dialogue','overlay','split_reason')},
                    **product_at(longer['frame_at'],max(left['end'],right['end']),data.get('breakdown') or {}),
                    'action_full':_join([left.get('action_full',left.get('action')),right.get('action_full',right.get('action'))]),
                    'action':' → '.join((left.get('action','')+' → '+right.get('action','')).split(' → ')[:3])}
        cells[position:position+2] = [combined]
        board.setdefault('merges',[]).append([left['id'],right['id']])
        stage = Path(tempfile.mkdtemp(prefix='.storyboard-',dir=directory/'analysis'))
        try:
            for i,cell in enumerate(cells,1):
                filename=f'cell-{i:02d}.jpg'
                shutil.copyfile(directory/cell['file'],stage/filename)
                cell.update(id=f'c{i:02d}',index=i,file='analysis/storyboard/'+filename)
            board['sheets']=render_sheets(cells,stage)
            return _publish(pid,data,board,stage)
        finally:
            if stage.exists(): shutil.rmtree(stage)
