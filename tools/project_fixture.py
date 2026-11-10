"""Immutable round24 project inputs; verified media copies stay in runtime/temp.

ROUND24_MEDIA_SOURCE_DIR may point at an alternate p1029100000-r18v directory.
It changes media lookup only: project JSON always comes from tools/fixtures.
Other projects retain their own default media directory. Tests may pass an
explicit source_dir to copy_media when exercising missing/corrupt local inputs.
"""
import hashlib
import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tools/fixtures/round24'
MAIN_PROJECT = 'p1029100000-r18v'


def _entry(project_id):
    return json.loads((FIXTURES / 'manifest.json').read_text(encoding='utf-8'))['projects'][project_id]


def fixture_path(project_id):
    """Return a known frozen JSON path, never a mutable projects/ path."""
    _entry(project_id)
    return FIXTURES / project_id / 'project.json'


def load_project(project_id):
    """Load a fresh copy of the frozen JSON and verify its recorded checksum."""
    raw = fixture_path(project_id).read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    expected = _entry(project_id)['fixture_sha256']
    if actual != expected:
        raise ValueError(f'Fixture SHA256 mismatch: project={project_id} path=project.json expected={expected} actual={actual}')
    return json.loads(raw)


def media_source_dir(project_id):
    """Resolve the optional media-only override for the round22 incident."""
    _entry(project_id)
    override = os.environ.get('ROUND24_MEDIA_SOURCE_DIR') if project_id == MAIN_PROJECT else None
    return Path(override).expanduser().resolve() if override else ROOT / 'projects' / project_id


def copy_media(project_id, destination, relative_paths=None, *, source_dir=None):
    """Verify each recorded media file before copying, or fail with its digest.

    No directory scanning is performed, so source JSON and unrelated files are
    never read. A source handle stays open from verification through copying.
    """
    destination = Path(destination).resolve()
    temporary_root = (ROOT / 'runtime/temp').resolve()
    if not destination.is_relative_to(temporary_root):
        raise ValueError(f'Media destination must be inside runtime/temp: {destination}')
    checksums = _entry(project_id)['media_sha256']
    paths = list(checksums if relative_paths is None else relative_paths)
    source = Path(source_dir).resolve() if source_dir is not None else media_source_dir(project_id)
    for relative in paths:
        if relative not in checksums:
            raise ValueError(f'Media path not recorded: project={project_id} path={relative}')
        if Path(relative).is_absolute() or '..' in Path(relative).parts:
            raise ValueError(f'Invalid media path: project={project_id} path={relative}')
        target = (destination / relative).resolve()
        if not target.is_relative_to(temporary_root) or not target.is_relative_to(destination):
            raise ValueError(f'Media destination must be inside runtime/temp: {target}')
        origin = source / relative
        expected = checksums[relative]
        try:
            stream = origin.open('rb')
        except FileNotFoundError as error:
            raise FileNotFoundError(f'Media missing: project={project_id} path={origin} expected={expected} actual=missing') from error
        with stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            if actual != expected:
                raise ValueError(f'Media SHA256 mismatch: project={project_id} path={origin} expected={expected} actual={actual}')
            stream.seek(0)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('wb') as output:
                shutil.copyfileobj(stream, output)
        with target.open('rb') as copied:
            copied_digest = hashlib.file_digest(copied, 'sha256').hexdigest()
        if copied_digest != expected:
            target.unlink()
            raise ValueError(f'Copied media SHA256 mismatch: project={project_id} path={target} expected={expected} actual={copied_digest}')
