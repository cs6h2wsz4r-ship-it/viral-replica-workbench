"""Read-only local model/dependency inventory. Never loads or downloads weights."""
from pathlib import Path
import json
import shutil

from . import align_models, asr, runtime_env

HF_HUB = runtime_env.DIRS['HF_HOME'] / 'hub'


def _whisper(model: str) -> dict:
    path = Path(model)
    local = path.is_dir()
    # Reuse the installed engine's aliases (large, turbo, distil...) without
    # calling its download_model or constructing a WhisperModel.
    try:
        from faster_whisper.utils import _MODELS
    except Exception:
        _MODELS = {}
    repo = _MODELS.get(model, model if '/' in model else 'Systran/faster-whisper-' + model)
    path = path if local else HF_HUB / ('models--' + repo.replace('/', '--'))
    row = {'name': f'语音识别（faster-whisper {model}）', 'status': 'missing',
           'path': str(path), 'download_url': '' if local else 'https://huggingface.co/' + repo}
    try:
        if 'faster-whisper' not in asr.engines():
            return {**row, 'status': 'broken', 'detail': '未安装或无法导入 faster-whisper'}
        if not path.exists():
            return row
        if not local:
            revision = (path / 'refs/main').read_text(encoding='utf-8').strip()
            if not revision or Path(revision).name != revision or revision in {'.', '..'}:
                raise ValueError('本地缓存版本无效')
            path = path / 'snapshots' / revision
        for name in ('config.json', 'tokenizer.json'):
            if not isinstance(json.loads((path / name).read_text(encoding='utf-8')), dict):
                raise ValueError('模型配置文件无效')
        if not (path / 'model.bin').is_file() or (path / 'model.bin').stat().st_size == 0:
            raise ValueError('模型权重文件缺失或为空')
        return {**row, 'status': 'ok'}
    except (OSError, ValueError) as exc:
        return {**row, 'status': 'broken', 'detail': str(exc)}


def report(model: str) -> list[dict]:
    rows = [_whisper(model)]
    for item in align_models.report():
        row = {**item, 'name': {'zh': '中文对齐模型', 'en': '英文对齐模型'}.get(item['language'], item['language'] + '对齐模型')}
        if row['status'] == 'broken' and not row.get('detail'):
            row['detail'] = '模型文件不完整或损坏'
        rows.append(row)
    paths = {name: shutil.which(name) for name in ('ffmpeg', 'ffprobe')}
    rows.append({'name': 'FFmpeg', 'status': 'ok' if all(paths.values()) else 'missing',
                 'path': '；'.join(f'{name}：{path or "未找到"}' for name, path in paths.items())})
    return rows
