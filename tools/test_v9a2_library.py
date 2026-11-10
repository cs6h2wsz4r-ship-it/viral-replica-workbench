"""V9a2 library contracts, all writes restricted to temporary directories."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from console import config, library, pipeline, store


class LibraryV9a2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory(dir=config.ROOT / 'runtime/temp'))
        root = Path(self.temp)
        self.enterContext(patch.object(config, 'LIBRARY_DIR', root / 'library'))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', root / 'projects'))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('no secrets')))
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('no network')))
        raw = io.BytesIO(); Image.new('RGB', (12, 18), 'orange').save(raw, format='PNG')
        self.raw = raw.getvalue()
        self.item = library.upload('person', '', '多字名字.png', self.raw)['item']

    def project(self, pid, selected=True):
        directory = config.PROJECTS_DIR / pid
        (directory / 'assets').mkdir(parents=True)
        (directory / 'assets/photo.png').write_bytes(self.raw)
        data = {'id': pid, 'assets': [{'id': 'a', 'type': 'image', 'file': 'assets/photo.png'}],
                'replacements': {'characters': [{'source_id': 'C1', 'action': 'replace', 'asset_id': 'a'}] if selected else [], 'product': {}}}
        store.atomic_write_json(directory / 'project.json', data)
        return data

    def test_legacy_groups_ignored_and_removed_on_write(self):
        index = config.LIBRARY_DIR / 'index.json'
        data = json.loads(index.read_text()); data['groups'] = [{'id': 'g', 'kind': 'group'}]
        store.atomic_write_json(index, data)
        self.assertEqual(set(library.list_all()), {'items'})
        with self.assertRaises(ValueError): library.get('g')
        self.assertFalse(hasattr(library, 'save_group'))
        library.rename(self.item['id'], '重命名')
        self.assertNotIn('groups', json.loads(index.read_text()))

    def test_filename_name_and_duplicate(self):
        self.assertEqual(self.item['name'], '素材 1')
        result = library.upload('person', '', 'other.png', self.raw)
        self.assertTrue(result['duplicate'])
        self.assertEqual(result['item']['id'], self.item['id'])

    def test_project_usage_counts_references_once_readonly_and_invalidates(self):
        one = self.project('one'); self.project('unused', selected=False)
        one['replacements']['product'] = {'action': 'replace', 'asset_id': 'a'}
        store.atomic_write_json(config.PROJECTS_DIR / 'one/project.json', one)
        files = {p: p.read_bytes() for p in config.PROJECTS_DIR.rglob('*') if p.is_file()}
        self.assertEqual(library.list_all()['items'][0]['project_count'], 1)
        self.assertEqual(library.list_all()['items'][0]['project_count'], 1)
        self.assertEqual(files, {p: p.read_bytes() for p in files})
        one['replacements'] = {}; store.atomic_write_json(config.PROJECTS_DIR / 'one/project.json', one)
        self.assertEqual(library.list_all()['items'][0]['project_count'], 0)
        self.project('two'); self.assertEqual(library.list_all()['items'][0]['project_count'], 1)

    def test_project_import_uses_upload_and_dedupes_actual_bytes(self):
        data = self.project('one', selected=False)
        data['assets'] = []; store.atomic_write_json(config.PROJECTS_DIR / 'one/project.json', data)
        with patch.object(pipeline, 'save_asset', wraps=pipeline.save_asset) as save:
            first = library.to_project('one', {'library_id': self.item['id']})
            second = library.to_project('one', {'library_id': self.item['id']})
        save.assert_called_once()
        self.assertFalse(first['duplicate']); self.assertTrue(second['duplicate'])
        self.assertEqual(first['asset']['id'], second['asset']['id'])
        self.assertEqual(first['asset']['description'], '')
        self.assertEqual(first['asset']['roles'], ['person_identity'])
        product = library.upload('product', '新商品', 'product.png', self.raw)['item']
        second_project = self.project('two', selected=False)
        second_project['assets'] = []; store.atomic_write_json(config.PROJECTS_DIR / 'two/project.json', second_project)
        self.assertEqual(library.to_project('two', {'library_id': product['id']})['asset']['roles'], ['product_truth'])
        self.assertNotIn('look', first['asset'])
        self.assertEqual((config.PROJECTS_DIR / 'one' / first['asset']['file']).read_bytes(), self.raw)

    def test_group_routes_removed_and_import_dispatch(self):
        import server
        handler = server.Handler.__new__(server.Handler)
        handler.headers = {'X-Client-Build': 'test'}
        handler._body_json = lambda: {'library_id': self.item['id']}
        with patch.object(server, 'build_status', return_value={'restart_pending': False, 'build': 'test'}):
            for method, path in [('POST', ['library', 'groups']), ('PATCH', ['library', 'groups', 'g']), ('DELETE', ['library', 'groups', 'g'])]:
                with self.subTest(method=method), self.assertRaises(server.ApiError) as error:
                    handler._api(method, path, {})
                self.assertEqual(error.exception.status, 404)
            self.project('one', selected=False)
            result = handler._api('POST', ['projects', 'one', 'library-import'], {})
            self.assertTrue(result['duplicate'])


if __name__ == '__main__':
    unittest.main()
