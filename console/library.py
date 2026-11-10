"""Local, content-addressed casting library. Reads and imports never invoke models."""
from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime
import hashlib
from io import BytesIO
import json
import re
from pathlib import Path
import threading
import uuid

from PIL import Image
from . import cast, config, store

_lock = threading.RLock()
_usage_cache = {}


def _now():
    return datetime.now().astimezone().isoformat()


@contextmanager
def _writing():
    """Serialize threads and processes; only explicit mutations create the directory."""
    with _lock:
        config.LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        with (config.LIBRARY_DIR / '.lock').open('a+b') as handle:
            if __import__('os').name == 'nt':
                import msvcrt
                handle.seek(0); handle.write(b'0'); handle.flush(); handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if __import__('os').name == 'nt':
                    handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)


def _read():
    path = config.LIBRARY_DIR / 'index.json'
    data = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    return {'items': data.get('items', [])}


def _write(data):
    store.atomic_write_json(config.LIBRARY_DIR / 'index.json', {'items': data.get('items', [])})


def _public(item):
    return {**copy.deepcopy(item), 'url': f'/api/library/{item["id"]}/file'}


def project_usage():
    """Count selected casting photos once per project; never migrate/write projects.

    Cache parsed references by project mtime and hashes by photo mtime so a large
    library grid does not repeatedly read every project and photo byte.
    """
    counts = {}; alive = set()
    for path in config.PROJECTS_DIR.glob('*/project.json'):
        path = path.resolve(); alive.add(path)
        try:
            stamp = (path.stat().st_mtime_ns, path.stat().st_size)
            cached = _usage_cache.get(path)
            if not cached or cached['stamp'] != stamp:
                data = json.loads(path.read_text(encoding='utf-8'))
                rep = data.get('replacements') or {}
                ids = {e.get('asset_id') for e in [*rep.get('characters', []), rep.get('product') or {}]
                       if e.get('action') in ('replace', 'add', 'keep') and e.get('asset_id')}
                files = [(path.parent / a.get('file', '')).resolve() for a in data.get('assets', [])
                         if a.get('id') in ids and a.get('type') == 'image']
                cached = {'stamp': stamp, 'files': files, 'hashes': {}}
                _usage_cache[path] = cached
            hashes = set()
            for photo in cached['files']:
                if not photo.is_relative_to(path.parent): continue
                try:
                    stat = photo.stat(); photo_stamp = (stat.st_mtime_ns, stat.st_size)
                    previous = cached['hashes'].get(photo)
                    if not previous or previous[0] != photo_stamp:
                        previous = (photo_stamp, store.sha256_file(photo))
                        cached['hashes'][photo] = previous
                    hashes.add(previous[1])
                except OSError:
                    continue
            for sha in hashes: counts[sha] = counts.get(sha, 0) + 1
        except (OSError, ValueError, TypeError):
            continue
    for path in set(_usage_cache) - alive: _usage_cache.pop(path, None)
    return counts


def list_all():
    with _lock:
        data = _read()
        counts = project_usage()
        return {'items': [{**_public(item), 'project_count': counts.get(item['sha256'], 0)} for item in data['items']]}


def get(item_id):
    data = _read()
    item = next((row for row in data['items'] if row['id'] == item_id), None)
    if not item:
        raise ValueError('这个素材已删除，请从素材库重新选择')
    return _public(item)


def file_path(item):
    item = get(item) if isinstance(item,str) else item
    path = (config.LIBRARY_DIR / item.get('file','')).resolve()
    if not path.is_relative_to(config.LIBRARY_DIR.resolve()) or not path.is_file():
        raise ValueError('素材文件找不到了，请从素材库重新上传')
    if store.sha256_file(path) != item.get('sha256'):
        raise ValueError('素材文件已改变，请从素材库重新上传')
    return path


def _name(value):
    if not isinstance(value,str) or not value.strip() or len(value.strip()) > 120:
        raise ValueError('请填写 1–120 字的素材名称')
    return value.strip()


def upload(kind, name, filename, raw, *, description='', look=None, origin=None):
    if kind not in ('person','product'):
        raise ValueError('请选择人物或商品')
    explicit_name = _name(name) if name else None
    try:
        with Image.open(BytesIO(raw)) as picture:
            picture.verify(); suffix = {'JPEG':'.jpg','PNG':'.png','WEBP':'.webp'}.get(picture.format)
        if not suffix: raise ValueError()
    except Exception as exc:
        raise ValueError('请上传 PNG、JPG 或 WebP 照片') from exc
    sha = hashlib.sha256(raw).hexdigest()
    with _writing():
        data = _read()
        old = next((i for i in data['items'] if i['sha256']==sha and i['kind']==kind), None)
        if old:
            try:
                file_path(old)
            except ValueError:
                # Same sha256 means these bytes are the lost file: re-uploading repairs it.
                target = (config.LIBRARY_DIR / old['file']).resolve()
                if not target.is_relative_to(config.LIBRARY_DIR.resolve()):
                    raise
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_name('.'+uuid.uuid4().hex+'.tmp')
                try:
                    tmp.write_bytes(raw); tmp.replace(target)
                finally:
                    tmp.unlink(missing_ok=True)
            if origin:
                old.update(description=copy.deepcopy(description),look=copy.deepcopy(look or {}))
                old['from']=copy.deepcopy(origin)
                _write(data)
            return {'item':_public(old),'duplicate':True}
        # Allocate across both kinds while holding the process + file write lock.
        # Legacy numbered names participate; existing records are never renamed.
        numbers = [int(match.group(1)) for item in data['items']
                   if (match := re.fullmatch(r'(?:素材|人物|商品)\s+(\d+)', item.get('name', '')))]
        name = explicit_name or f'素材 {max(numbers, default=0) + 1}'
        relative = f'files/{sha}{suffix}'; path = config.LIBRARY_DIR / relative
        path.parent.mkdir(exist_ok=True)
        if not path.exists():
            tmp = path.with_name('.'+uuid.uuid4().hex+'.tmp')
            try:
                tmp.write_bytes(raw); tmp.replace(path)
            finally:
                tmp.unlink(missing_ok=True)
        item = dict(id='l'+uuid.uuid4().hex[:20],kind=kind,name=name,file=relative,sha256=sha,
                    description=copy.deepcopy(description),look=copy.deepcopy(look or {}),created_at=_now())
        if origin: item['from'] = copy.deepcopy(origin)
        data['items'].append(item); _write(data)
        return {'item':_public(item),'duplicate':False}


def rename(item_id, name):
    with _writing():
        data = _read()
        item = next((i for i in data['items'] if i['id']==item_id),None)
        if not item: raise ValueError('这个素材已删除，请刷新素材库')
        item['name'] = _name(name); _write(data)
        return _public(item)


def delete(item_id):
    with _writing():
        data = _read(); item = next((i for i in data['items'] if i['id']==item_id),None)
        if not item: raise ValueError('这个素材已删除，请刷新素材库')
        data['items'] = [i for i in data['items'] if i['id']!=item_id]
        _write(data)
        if item.get('file') and not any(i['file']==item['file'] for i in data['items']):
            path = (config.LIBRARY_DIR / item['file']).resolve()
            if path.is_relative_to(config.LIBRARY_DIR.resolve()): path.unlink(missing_ok=True)
    return {'deleted':item_id}


def from_project(pid, body):
    kind = body.get('kind')
    if kind not in ('person','product'): raise ValueError('请选择人物或商品')
    with store._lock(pid):
        data = store.load(pid); directory = store.project_dir(pid)
        entry = cast._target(data,'character' if kind=='person' else kind,body.get('source_id'))
        photo = cast._photo(data,entry,directory)
        asset = next(a for a in data['assets'] if a['id']==entry['asset_id'])
        return upload(kind,body.get('name'),photo.name,photo.read_bytes(),
                      description=entry.get('description',''),look=entry.get('look',{}),origin={'project_id':pid,'asset_id':asset['id']})


def to_project(pid, body):
    """Explicit import uses the existing upload path, with local-byte dedupe."""
    from . import pipeline
    item = get(body.get('library_id'))
    photo = file_path(item)
    with store._lock(pid):
        directory = store.project_dir(pid); data = store.load(pid)
        for asset in data.get('assets', []):
            target = (directory / asset.get('file', '')).resolve()
            if (asset.get('type') == 'image' and target.is_relative_to(directory.resolve())
                    and target.is_file() and store.sha256_file(target) == item['sha256']):
                return {'asset': copy.deepcopy(asset), 'duplicate': True}
        temp = directory / 'assets' / ('.library-' + uuid.uuid4().hex + photo.suffix)
        try:
            temp.write_bytes(photo.read_bytes())
            roles = ['person_identity' if item['kind'] == 'person' else 'product_truth']
            asset = pipeline.save_asset(pid, photo.name, temp, 'image', item['name'], roles)
            return {'asset': asset, 'duplicate': False}
        finally:
            temp.unlink(missing_ok=True)


def saved_assets(data, directory):
    hashes = {i['sha256'] for i in _read()['items']}
    if not hashes: return []
    result = []
    for asset in data.get('assets',[]):
        if asset.get('type')!='image' or asset.get('internal'): continue
        path = (directory / asset.get('file','')).resolve()
        if path.is_relative_to(directory.resolve()) and path.is_file() and store.sha256_cached(path) in hashes:
            result.append(asset['id'])
    return result
