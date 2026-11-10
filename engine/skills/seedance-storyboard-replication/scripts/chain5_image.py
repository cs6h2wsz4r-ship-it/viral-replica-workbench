#!/usr/bin/env python3
"""Chain-five prerequisite images through Jimmy's asynchronous image endpoint.

The controller supplies execution intent separately; a video confirmation or the
existence of these local files never authorizes a new image request. This helper
only executes one previously prepared image. It never selects creative content.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urlparse

try:
    import fcntl as _fcntl
except ImportError:  # Windows standard library; exercised with a stub on Mac.
    _fcntl = None
    import msvcrt as _msvcrt
else:
    _msvcrt = None

DEFAULT_MODEL = 'gpt-image-2'
MODELS = {'gpt-image-2', 'nano-banana', 'nano-banana-2', 'nano-banana-2.1', 'nano-banana-pro'}
RATIOS = {'auto', '1:1', '3:2', '2:3', '4:3', '3:4', '5:4', '4:5', '16:9', '9:16',
          '2:1', '1:2', '3:1', '1:3', '21:9', '9:21'}
GPT_4K_RATIOS = {'16:9', '9:16', '2:1', '1:2', '21:9', '9:21'}
MAX_REFS = 8  # Conservative adapter policy; only Nano 2.1 explicitly documents eight.
MAX_BYTES = 100 * 1024 * 1024
TASK_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')


class ImageError(RuntimeError):
    pass


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ImageError('local image job record is unreadable') from exc
    if not isinstance(data, dict):
        raise ImageError('local image job record must be an object')
    return data


def _save(path: Path, value: Any) -> None:
    raw = _json_bytes(value)
    fd, tmp = tempfile.mkstemp(prefix='.image-', dir=path.parent)
    try:
        if hasattr(os, 'fchmod'):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        os.replace(tmp, path)
        try:
            directory = os.open(path.parent, os.O_RDONLY)
        except OSError:  # Windows does not expose a directory fsync handle.
            directory = None
        if directory is not None:
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        Path(tmp).unlink(missing_ok=True)


@contextmanager
def _lock(run: Path):
    try:
        fd = os.open(run / '.image.lock', os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    except OSError as exc:
        raise ImageError('cannot lock local image run') from exc
    try:
        try:
            if _fcntl is not None:
                _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            else:
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b'\0')
                os.lseek(fd, 0, os.SEEK_SET)
                _msvcrt.locking(fd, _msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise ImageError('image run is busy; resume this same run later') from exc
        yield
    finally:
        os.close(fd)


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ImageError(f'{where} must be nonempty text')
    return value


def _https(value: Any) -> str:
    value = _text(value, 'image URL')
    try:
        parsed = urlparse(value)
        host = parsed.hostname
        if parsed.scheme != 'https' or not host or parsed.username or parsed.password or parsed.fragment:
            raise ValueError
        if host.lower().rstrip('.') in {'localhost', 'localhost.localdomain'} or host.endswith('.localhost'):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError
        if parsed.port not in (None, 443):
            raise ValueError
    except ValueError as exc:
        raise ImageError('reference/result must use a public HTTPS URL without credentials or fragments') from exc
    return value


def _format(path: Path) -> str:
    try:
        with path.open('rb') as handle:
            header = handle.read(16)
    except OSError as exc:
        raise ImageError('local reference/result image is unreadable') from exc
    if header.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if header.startswith(b'\xff\xd8\xff'):
        return 'jpeg'
    if header.startswith(b'RIFF') and header[8:12] == b'WEBP':
        return 'webp'
    raise ImageError('adapter accepts actual PNG/JPEG/WebP image bytes only')


def _validate_image(path: Path) -> dict[str, Any]:
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
        raise ImageError('local image must be a nonempty file within the 100 MiB adapter/upload limit')
    kind = _format(path)
    probe, ffmpeg = shutil.which('ffprobe'), shutil.which('ffmpeg')
    if not probe or not ffmpeg:
        raise ImageError('existing FFmpeg and FFprobe are required; no image dependency is installed automatically')
    demuxer = {'png': 'png_pipe', 'jpeg': 'jpeg_pipe', 'webp': 'webp_pipe'}[kind]
    options = ['-v', 'error', '-protocol_whitelist', 'file,pipe', '-f', demuxer]
    try:
        info = subprocess.run([probe, *options, '-show_entries', 'stream=width,height', '-of', 'json', str(path)],
                              capture_output=True, text=True, timeout=30, check=True)
        streams = json.loads(info.stdout).get('streams', [])
        if len(streams) != 1 or int(streams[0].get('width', 0)) < 1 or int(streams[0].get('height', 0)) < 1:
            raise ValueError
        subprocess.run([ffmpeg, *options, '-xerror', '-i', str(path), '-f', 'null', '-'],
                       capture_output=True, timeout=30, check=True)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        raise ImageError('local image failed actual FFmpeg decoding') from exc
    return {'format': kind, 'width': int(streams[0]['width']), 'height': int(streams[0]['height'])}


def _build(job_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = job_path.read_bytes()
    job = _read(job_path)
    allowed = {'schema_version', 'id', 'model', 'prompt', 'ratio', 'resolution', 'quality', 'n', 'images', 'metadata'}
    if set(job) - allowed or job.get('schema_version') != 'chain5.image-job.v1':
        raise ImageError('invalid image-job schema or unsupported fields')
    ident = _text(job.get('id'), 'id')
    if not TASK_ID.fullmatch(ident):
        raise ImageError('id must be a simple identifier')
    model = job.get('model', DEFAULT_MODEL)
    if not isinstance(model, str) or model not in MODELS:
        raise ImageError('unsupported exact image model; no automatic substitution')
    prompt = _text(job.get('prompt'), 'prompt')
    ratio = job.get('ratio')
    if not isinstance(ratio, str) or ratio not in RATIOS:
        raise ImageError('ratio must be an explicitly supported image ratio')
    resolution = job.get('resolution', '1k' if model == 'nano-banana' else '2k')
    supported = {'1k'} if model == 'nano-banana' else ({'1k', '2k'} if model == 'nano-banana-2' else {'1k', '2k', '4k'})
    if not isinstance(resolution, str) or resolution not in supported:
        raise ImageError('resolution is outside this model adapter policy; explicit values are never changed')
    if model == DEFAULT_MODEL and resolution == '4k' and ratio not in GPT_4K_RATIOS:
        raise ImageError('GPT Image 2 4k requires a verified wide/portrait ratio')
    if type(job.get('n', 1)) is not int or job.get('n', 1) != 1:
        raise ImageError('one asynchronous image per job is required (n=1)')
    preview = {'schema_version': 'chain5.image-job.v1', 'id': ident, 'model': model,
               'prompt': prompt, 'ratio': ratio, 'resolution': resolution, 'n': 1}
    if model == DEFAULT_MODEL:
        quality = job.get('quality', 'auto')
        if not isinstance(quality, str) or quality not in {'auto', 'low', 'medium', 'high'}:
            raise ImageError('unsupported GPT image quality')
        preview['quality'] = quality
    elif job.get('quality', 'auto') != 'auto':
        raise ImageError('NanoBanana ignores quality; remove this setting instead of promising its effect')
    images = job.get('images', [])
    if not isinstance(images, list) or len(images) > MAX_REFS:
        raise ImageError('images must be an ordered list within the eight-reference adapter policy')
    normalized, files = [], []
    for item in images:
        if not isinstance(item, dict):
            raise ImageError('image reference must be a typed file or URL object')
        if item.get('source') == 'file' and set(item) == {'source', 'path'}:
            path = Path(_text(item.get('path'), 'image path')).expanduser()
            path = (job_path.parent / path).resolve() if not path.is_absolute() else path.resolve()
            info = _validate_image(path)
            record = {'source': 'file', 'path': str(path), 'sha256': _file_sha(path), **info}
            normalized.append(record); files.append(record)
        elif item.get('source') == 'url' and set(item) == {'source', 'url'}:
            normalized.append({'source': 'url', 'url': _https(item.get('url'))})
        else:
            raise ImageError('image references only support source=file/path or source=url/url')
    preview['images'] = normalized
    if 'metadata' in job:
        if not isinstance(job['metadata'], dict):
            raise ImageError('metadata must be a local object')
        preview['metadata'] = job['metadata']
    ready = {'schema_version': 'chain5.image-ready.v1', 'id': ident, 'job_path': str(job_path),
             'job_sha256': _sha(raw), 'preview_sha256': _sha(_json_bytes(preview)),
             'files': files, 'policy': {'max_references': MAX_REFS, 'formats': ['png', 'jpeg', 'webp']}}
    return preview, ready


def prepare(job_path: Path, run_dir: Path) -> dict[str, Any]:
    job_path, run = Path(job_path).resolve(), Path(run_dir).resolve()
    try:
        preview, ready = _build(job_path)
        if run.exists():
            existing = verify(run)
            if ready != existing:
                raise ImageError('this run belongs to a different image job; use a new run directory')
            return existing
        run.mkdir(parents=True, mode=0o700, exist_ok=False)
        _save(run/'image-job.preview.json', preview)
        _save(run/'image-ready.json', ready)
        return ready
    except (OSError, ValueError) as exc:
        raise ImageError('could not prepare a new image run') from exc


def verify(run_dir: Path) -> dict[str, Any]:
    run = Path(run_dir).resolve()
    try:
        ready = _read(run/'image-ready.json')
        if ready.get('schema_version') != 'chain5.image-ready.v1':
            raise ImageError('invalid image-ready record')
        job_path = Path(ready['job_path'])
        if _file_sha(job_path) != ready.get('job_sha256'):
            raise ImageError('image job changed since preview; prepare a new run')
        preview, rebuilt = _build(job_path)
        if ready != rebuilt or _sha((run/'image-job.preview.json').read_bytes()) != ready['preview_sha256']:
            raise ImageError('image preview or reference bytes changed; prepare a new run')
        if _json_bytes(preview) != (run/'image-job.preview.json').read_bytes():
            raise ImageError('image preview changed')
        return ready
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ImageError('image run fingerprint verification failed') from exc


def _frozen(run: Path) -> dict[str, Any]:
    """Verify saved artifacts without needing mutable inputs for a paid task."""
    ready = _read(run/'image-ready.json')
    try:
        raw = (run/'image-job.preview.json').read_bytes()
        preview = _read(run/'image-job.preview.json')
        if (ready.get('schema_version') != 'chain5.image-ready.v1'
                or ready.get('preview_sha256') != _sha(raw)
                or preview.get('schema_version') != 'chain5.image-job.v1'
                or preview.get('id') != ready.get('id')):
            raise ImageError('frozen image artifacts changed; preserve the known task ID for recovery')
    except OSError as exc:
        raise ImageError('frozen image artifacts are unreadable') from exc
    return ready


def _runtime(env_file: Path | None, need_uploader: bool = True) -> tuple[Any, Any]:
    scripts = Path(__file__).resolve().parents[2] / 'jimmy-video-generator' / 'scripts'
    if not scripts.is_dir():
        raise ImageError('install the paired Jimmy Skill beside this Skill before channel image execution')
    # Clear names can collide with unrelated upstream modules in a shared interpreter.
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    try:
        for name in ('config', 'seedance_submit', 'jimmy_submit', 'jimmy_file_upload', 'runtime_dns_patch'):
            cached = sys.modules.get(name)
            if cached is not None and Path(getattr(cached, '__file__', '')).resolve() != (scripts/(name+'.py')).resolve():
                raise ImageError('use the standalone image CLI to isolate the paired Jimmy runtime')
        from config import DEFAULT_ENV_FILE, load_settings
        from jimmy_file_upload import JimmyFileUploader
        from jimmy_submit import JimmyClient
        from runtime_dns_patch import install_runtime_dns_patch
        settings = load_settings(env_file or DEFAULT_ENV_FILE)
        settings.require_jimmy()
        install_runtime_dns_patch()
        return (JimmyClient(settings.jimmy_api_key, base_url=settings.jimmy_base_url),
                JimmyFileUploader(settings.jimmy_api_key, base_url=settings.jimmy_base_url) if need_uploader else None)
    except Exception as exc:
        raise ImageError('Jimmy image configuration unavailable: check JIMMY_API_KEY and JIMMY_BASE_URL '
                         '(the fixed official Jimmy origin) in the existing local secret configuration') from exc


def _data(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict) or response.get('code') not in (0, '0', 20000, '20000'):
        raise ImageError('Jimmy image response did not report a known success code')
    data = response.get('data')
    if not isinstance(data, dict):
        raise ImageError('Jimmy image response has no data object')
    return data


def _cost(state: dict[str, Any], data: dict[str, Any]) -> None:
    amount = data.get('cost')
    if type(amount) in (float, int) and math.isfinite(amount) and amount >= 0:
        state['cost'] = {'amount': amount, 'unit': 'JimiCoin'}


def _state(run: Path, ready: dict[str, Any]) -> dict[str, Any] | None:
    path = run/'image-state.json'
    if not path.exists():
        return None
    state = _read(path)
    if (state.get('schema_version') != 'chain5.image-state.v1'
            or state.get('preview_sha256') != ready['preview_sha256']
            or state.get('ready_sha256') != _sha(_json_bytes(ready))):
        raise ImageError('saved image state does not match this exact preview')
    if 'request' in state and _sha(_json_bytes(state['request'])) != state.get('request_sha256'):
        raise ImageError('saved image request changed')
    return state


def _completed(state: dict[str, Any]) -> dict[str, Any]:
    path = Path(state.get('output', ''))
    if not path.is_file() or _file_sha(path) != state.get('output_sha256'):
        raise ImageError('completed image is missing or changed; do not silently reuse or overwrite it')
    return state


def submit(run_dir: Path, *, client: Any = None, uploader: Any = None,
           env_file: Path | None = None) -> dict[str, Any]:
    run = Path(run_dir).resolve()
    with _lock(run):
        ready = verify(run)
        preview = _read(run/'image-job.preview.json')
        state = _state(run, ready)
        if state:
            if state.get('phase') == 'completed':
                return _completed(state)
            if state.get('phase') == 'submitted' and state.get('remote_task_id'):
                return state
            if state.get('phase') not in {'uploading', 'upload_failed'}:
                raise ImageError('image submission outcome unknown or terminal; never create again in this run')
        else:
            state = {'schema_version': 'chain5.image-state.v1', 'id': ready['id'],
                     'preview_sha256': ready['preview_sha256'], 'ready_sha256': _sha(_json_bytes(ready)),
                     'phase': 'uploading',
                     'uploads': {}, 'remote_task_id': None}
            _save(run/'image-state.json', state)
        if client is None or (uploader is None and any(x['source'] == 'file' for x in preview['images'])):
            runtime_client, runtime_uploader = _runtime(env_file)
            client = client or runtime_client
            uploader = uploader or runtime_uploader
        urls = []
        try:
            for item in preview['images']:
                if item['source'] == 'url':
                    urls.append(item['url']); continue
                digest = item['sha256']
                if digest not in state['uploads']:
                    url = _https(uploader.upload(Path(item['path']))['url'])
                    state['uploads'][digest] = {'url': url}
                    _save(run/'image-state.json', state)
                urls.append(_https(state['uploads'][digest]['url']))
            # Catch changes during uploads before the paid create.
            verify(run)
        except Exception as exc:
            state['phase'] = 'upload_failed'; _save(run/'image-state.json', state)
            raise ImageError('image reference upload/preflight failed; resume this same run without creating a new task') from exc
        body = {key: preview[key] for key in ('model', 'prompt', 'ratio', 'resolution', 'n', 'quality') if key in preview}
        if urls:
            body['images'] = urls
        state['request'] = body
        state['request_sha256'] = _sha(_json_bytes(body))
        state['phase'] = 'submission_outcome_unknown'
        _save(run/'image-state.json', state)
        try:
            data = _data(client._request('POST', '/images', body))
            task_id = data.get('task_id')
            if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
                raise ImageError('image create has no valid task ID')
            state['remote_task_id'] = task_id; state['phase'] = 'submitted'; _cost(state, data)
            _save(run/'image-state.json', state)
        except Exception as exc:
            raise ImageError('image create outcome unknown; never retry automatically; recover the original task ID') from exc
        return state


def resume(run_dir: Path, *, client: Any = None, env_file: Path | None = None) -> dict[str, Any]:
    run = Path(run_dir).resolve()
    with _lock(run):
        ready = _frozen(run)
        state = _state(run, ready)
        if not state:
            raise ImageError('image run was never submitted')
        if state.get('phase') == 'completed':
            return _completed(state)
        task_id = state.get('remote_task_id')
        if state.get('phase') != 'submitted' or not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
            raise ImageError('image task cannot resume from an unknown or terminal submission state')
        if client is None:
            client, _ = _runtime(env_file, need_uploader=False)
        try:
            data = _data(client._request('GET', f'/images/{task_id}', None))
            _cost(state, data)
            status = data.get('status')
            if status in {'queued', 'pending', 'processing', 'running'}:
                _save(run/'image-state.json', state); return state
            if status in {'failed', 'error', 'cancelled'}:
                state['phase'] = 'failed'; _save(run/'image-state.json', state)
                raise ImageError('Jimmy image task failed; inspect the original remote task before planning any new image')
            if status not in {'completed', 'succeeded'}:
                raise ImageError('Jimmy image task reported an unrecognized status')
            result = data.get('result')
            url = _https(result.get('image_url') if isinstance(result, dict) else None)
            _save(run/'image-state.json', state)
            fd, name = tempfile.mkstemp(prefix='.download-image-', dir=run)
            os.close(fd); temporary = Path(name)
            try:
                client.download_video(url, temporary)
                info = _validate_image(temporary)
                with temporary.open('rb+') as handle:
                    os.fsync(handle.fileno())
                suffix = {'png': 'png', 'jpeg': 'jpg', 'webp': 'webp'}[info['format']]
                output = run / ('result.' + suffix)
                if any(run.glob('result.*')):
                    raise ImageError('image result path already exists; never overwrite an untracked output')
                # Hard link is atomic and refuses overwrite even if another writer races.
                os.link(temporary, output)
                state.update(phase='completed', output=str(output), output_sha256=_file_sha(output), image=info)
                _save(run/'image-state.json', state)
                return state
            finally:
                temporary.unlink(missing_ok=True)
        except ImageError:
            raise
        except Exception as exc:
            raise ImageError('image query/download failed; resume this same task ID without creating another task') from exc


def recover(run_dir: Path, remote_task_id: str) -> dict[str, Any]:
    run = Path(run_dir).resolve()
    with _lock(run):
        ready = _frozen(run)
        state = _state(run, ready)
        if not state or state.get('phase') != 'submission_outcome_unknown' or state.get('remote_task_id'):
            raise ImageError('only an uncertain submission without a saved ID can recover the original task ID')
        if not isinstance(remote_task_id, str) or not TASK_ID.fullmatch(remote_task_id):
            raise ImageError('invalid original image task ID')
        state.update(remote_task_id=remote_task_id, phase='submitted')
        _save(run/'image-state.json', state)
        return state


def _summary(data: dict[str, Any], run: Path) -> dict[str, Any]:
    return {key: value for key, value in {'id': data.get('id'), 'phase': data.get('phase', 'prepared'),
            'run_dir': str(run.resolve()), 'output': data.get('output'), 'cost': data.get('cost')}.items() if value is not None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'verify', 'submit', 'resume', 'recover-task-id'):
        command = commands.add_parser(name)
        command.add_argument('--run', type=Path, required=True)
        if name == 'prepare': command.add_argument('--job', type=Path, required=True)
        if name in {'submit', 'resume'}: command.add_argument('--env-file', type=Path)
        if name == 'recover-task-id': command.add_argument('--task-id', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare': result = prepare(args.job, args.run)
        elif args.command == 'verify': result = verify(args.run)
        elif args.command == 'submit': result = submit(args.run, env_file=args.env_file)
        elif args.command == 'resume': result = resume(args.run, env_file=args.env_file)
        else: result = recover(args.run, args.task_id)
        print(json.dumps(_summary(result, args.run), ensure_ascii=False))
        return 0
    except (ImageError, OSError, ValueError) as exc:
        # Do not print provider exceptions, response bodies, signed URLs or keys.
        print('Image execution stopped: ' + (str(exc) if isinstance(exc, ImageError) else 'local file/record error'), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
