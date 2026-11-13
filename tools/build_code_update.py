"""Generate only the root code hash manifest before publishing."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import updater


def build(root):
    files = {}
    candidates = [root / name for name in updater.TOP_FILES]
    for folder in ('console', 'web', 'engine/skills/jimmy-video-generator/scripts',
                   'engine/skills/seedance-storyboard-replication/scripts'):
        candidates.extend((root / folder).rglob('*'))
    for path in sorted(set(candidates)):
        name = path.relative_to(root).as_posix()
        if path.is_file() and not path.is_symlink() and updater.allowed(name):
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = updater.validate_manifest({'format': 1, 'runtime': 1,
        'version': (root / 'VERSION').read_text().strip(), 'files': files})
    (root / updater.MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(f'Manifest: {len(files)} code files; {root / updater.MANIFEST}')


if __name__ == '__main__':
    build(ROOT)
