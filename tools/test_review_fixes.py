"""Code-review fixes after V9c: cached hashes, library self-repair, cached remote scan. Temp dirs only."""
import io
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image
from console import config, fission_dashboard, library, store


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory(dir=config.ROOT / 'runtime/temp'))
        root = Path(self.temp)
        self.enterContext(patch.object(config, 'LIBRARY_DIR', root / 'library'))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', root / 'projects'))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('no secrets')))
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('no network')))
        raw = io.BytesIO(); Image.new('RGB', (10, 16), 'teal').save(raw, format='PNG')
        self.raw = raw.getvalue()

    def test_cached_hash_follows_rewrites_and_skips_rereads(self):
        path = Path(self.temp) / 'f.bin'
        path.write_bytes(b'one')
        first = store.sha256_cached(path)
        self.assertEqual(first, store.sha256_file(path))
        with patch.object(store, 'sha256_file', side_effect=AssertionError('reread')):
            self.assertEqual(store.sha256_cached(path), first)
        tmp = path.with_name('f.tmp'); tmp.write_bytes(b'two'); tmp.replace(path)  # app-style atomic replace
        self.assertNotEqual(store.sha256_cached(path), first)
        path.write_bytes(b'thr')  # same size, in-place write still changes mtime/ctime
        os.utime(path, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
        self.assertEqual(store.sha256_cached(path), store.sha256_file(path))

    def test_reupload_repairs_missing_library_file(self):
        item = library.upload('person', '', 'a.png', self.raw)['item']
        stored = config.LIBRARY_DIR / item['file']
        stored.unlink()
        with self.assertRaises(ValueError):
            library.file_path(item['id'])
        again = library.upload('person', '', 'a.png', self.raw)
        self.assertTrue(again['duplicate'])
        self.assertEqual(again['item']['id'], item['id'])
        self.assertEqual(library.file_path(item['id']).read_bytes(), self.raw)

    def test_remote_scan_reparses_only_changed_projects(self):
        def write(pid, state):
            directory = config.PROJECTS_DIR / pid; directory.mkdir(parents=True, exist_ok=True)
            store.atomic_write_json(directory / 'project.json', {
                'id': pid, 'name': pid, 'images': {'i1': {'state': state, 'storyboard_context': {'sheet_id': 'd01'}}}, 'runs': {}})
        write('pa', 'running'); write('pb', 'completed')
        fission_dashboard._remote_cache.clear()
        self.assertEqual(fission_dashboard._remote_keys('images'), {('images', 'pa', 'd01', 'i1')})
        with patch.object(store, 'load', side_effect=AssertionError('unchanged project reparsed')):
            self.assertEqual(fission_dashboard._remote_keys('images'), {('images', 'pa', 'd01', 'i1')})
        write('pa', 'completed'); write('pb', 'running')
        self.assertEqual(fission_dashboard._remote_keys('images'), {('images', 'pb', 'd01', 'i1')})
        self.assertEqual(fission_dashboard._remote_keys('videos'), set())


if __name__ == '__main__':
    unittest.main()
