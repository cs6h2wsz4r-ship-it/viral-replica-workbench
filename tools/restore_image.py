"""Restore a completed image by GET/download only; never creates a remote task."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile


def restore(run: Path, client, *, task_id: str | None = None, expected_sha256: str | None = None) -> dict:
    state_path = run / 'image-state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    task_id = task_id or state.get('remote_task_id')
    expected = expected_sha256 or state.get('output_sha256')
    if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', task_id):
        raise ValueError('缺少可恢复的远端任务编号')
    if not expected:
        raise ValueError('缺少原图 SHA256，不能校验恢复图片')
    reply = client._request('GET', f'/images/{task_id}', None)
    data = reply.get('data', reply)
    if data.get('status') not in {'completed', 'succeeded'}:
        raise ValueError('原生图任务尚未完成，稍后再恢复')
    url = (data.get('result') or {}).get('image_url')
    if not isinstance(url, str) or not url.startswith('https://'):
        raise ValueError('原任务没有有效图片下载地址')
    suffix = Path(state.get('output') or 'result.png').suffix.lower()
    if suffix not in {'.png', '.jpg', '.jpeg', '.webp'}:
        suffix = '.png'
    output = run / ('result' + suffix)
    fd, name = tempfile.mkstemp(prefix='.restore-', dir=run)
    os.close(fd)
    temp = Path(name)
    try:
        client.download_video(url, temp)
        digest = hashlib.sha256(temp.read_bytes()).hexdigest()
        if digest != expected:
            raise ValueError('恢复图片 SHA256 校验失败')
        if output.exists():
            if hashlib.sha256(output.read_bytes()).hexdigest() != expected:
                raise ValueError('本地结果已变化，不覆盖原文件')
        else:
            os.link(temp, output)
        return {'phase':'completed', 'output':str(output), 'output_sha256':digest, 'remote_task_id':task_id}
    finally:
        temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['resume'])
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--env-file', required=True, type=Path)
    parser.add_argument('--task-id')
    parser.add_argument('--sha256')
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    script = Path(__file__).resolve().parents[1] / 'engine/skills/seedance-storyboard-replication/scripts/chain5_image.py'
    spec = importlib.util.spec_from_file_location('_restore_chain5', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    client, _ = module._runtime(args.env_file, need_uploader=False)
    result = restore(args.run.resolve(), client, task_id=args.task_id, expected_sha256=args.sha256)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
