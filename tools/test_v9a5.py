"""V9a5 naming and frozen project-label contracts; isolated synthetic photos."""
import concurrent.futures
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from console import config, library, pipeline, store


class V9a5Tests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory(dir=config.ROOT / 'runtime/temp'))
        self.root = Path(self.temp)
        self.enterContext(patch.object(config, 'LIBRARY_DIR', self.root / 'library'))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.root / 'projects'))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('no secrets')))
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('no network')))

    def raw(self, n):
        stream = io.BytesIO()
        Image.new('RGB', (12, 18), (n % 256, n // 256, 80)).save(stream, format='PNG')
        return stream.getvalue()

    def upload(self, n, kind='person', name='', filename='短文件名.png'):
        return library.upload(kind, name, filename, self.raw(n))

    def test_default_name_ignores_all_filenames_shared_sequence(self):
        names = [self.upload(i, 'person' if i % 2 else 'product', filename=f)['item']['name']
                 for i, f in enumerate(['短名.png', 'IMG_9999.jpg', 'ChatGPT Image 长导出文件名.png', ''], 1)]
        self.assertEqual(names, ['素材 1', '素材 2', '素材 3', '素材 4'])

    def test_max_includes_legacy_names_across_both_kinds(self):
        for i, (kind, name) in enumerate([('person', '人物 7'), ('product', '商品 12'), ('product', '素材 15'),
                                          ('person', '素材 999 尾巴'), ('person', '人物 x')], 1):
            self.upload(i, kind, name)
        self.assertEqual(self.upload(10)['item']['name'], '素材 16')
        self.assertEqual(self.upload(11, 'product')['item']['name'], '素材 17')

    def test_batch_order_and_deleted_gap_not_reused(self):
        first = self.upload(1)['item']; self.upload(2); self.upload(3)
        library.delete(first['id'])
        self.assertEqual([self.upload(i)['item']['name'] for i in range(4, 8)],
                         ['素材 4', '素材 5', '素材 6', '素材 7'])

    def test_concurrent_uploads_share_locked_sequence(self):
        self.upload(1, name='商品 9')
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            items = list(pool.map(lambda n: self.upload(n, 'person' if n % 2 else 'product')['item'], range(2, 26)))
        self.assertEqual(len({i['name'] for i in items}), 24)
        self.assertEqual(sorted(int(i['name'].split()[1]) for i in items), list(range(10, 34)))

    def test_explicit_name_and_renames_preserved(self):
        item = self.upload(1, name=' 用户指定的长照片名 ')['item']
        self.assertEqual(item['name'], '用户指定的长照片名')
        library.rename(item['id'], 'IMG_1234 用户选的名字')
        self.assertEqual(library.get(item['id'])['name'], 'IMG_1234 用户选的名字')

    def test_duplicate_retains_name_and_consumes_no_number(self):
        item = self.upload(1)['item']; library.rename(item['id'], '自定义名字')
        duplicate = self.upload(1, name='不应覆盖', filename='别的文件名.png')
        self.assertTrue(duplicate['duplicate']); self.assertEqual(duplicate['item']['name'], '自定义名字')
        self.assertEqual(self.upload(2)['item']['name'], '素材 1')

    def project(self):
        directory = config.PROJECTS_DIR / 'test'
        (directory / 'assets').mkdir(parents=True)
        (directory / 'assets/photo.png').write_bytes(self.raw(42))
        data = {'id': 'test', 'assets': [{'id': 'a', 'type': 'image', 'file': 'assets/photo.png', 'label': '项目原标签'}],
                'replacements': {'characters': [{'source_id': 'C1', 'action': 'replace', 'asset_id': 'a'}], 'product': {}}}
        store.atomic_write_json(directory / 'project.json', data)
        return directory, data

    def test_save_from_step03_uses_shared_name_and_keeps_project_bytes(self):
        self.upload(1, 'product', '商品 6')
        directory, data = self.project(); before = (directory / 'project.json').read_bytes()
        item = library.from_project('test', {'kind': 'person', 'source_id': 'C1'})['item']
        self.assertEqual(item['name'], '素材 7')
        self.assertEqual((directory / 'project.json').read_bytes(), before)
        self.assertEqual(store.load('test')['assets'][0]['label'], '项目原标签')

    def test_existing_names_not_migrated_and_duplicate_save_keeps_rename(self):
        directory, data = self.project()
        item = library.upload('person', '旧的很长名字仍然保留', 'photo.png', self.raw(42))['item']
        before = library.get(item['id'])['name']
        result = library.from_project('test', {'kind': 'person', 'source_id': 'C1'})
        self.assertTrue(result['duplicate']); self.assertEqual(result['item']['name'], before)

    def test_project_label_reaches_model_context_so_upload_is_frozen(self):
        directory, data = self.project()
        before = pipeline.build_replacements(data)
        changed = copy.deepcopy(data); changed['assets'][0]['label'] = '素材 1'
        after = pipeline.build_replacements(changed)
        self.assertEqual(before['人物'][0]['新人物素材'], '项目原标签')
        self.assertNotEqual(json.dumps(before, ensure_ascii=False).encode(), json.dumps(after, ensure_ascii=False).encode())
        source = (config.ROOT / 'web/app.js').read_text()
        start = source.index('bindDrop($("#assetInput")')
        end = source.index('bindDrop($("#audioInput")', start)
        self.assertIn('label=${encodeURIComponent(f.name.replace', source[start:end])


class V9a5FrozenRequests(unittest.TestCase):
    def test_multi_request_bytes_match_start_snapshot(self):
        import hashlib
        import importlib.util
        from console import storyboard_edits as edits, storyboard_multi as multi
        from tools.test_v9a4 import V9a4Tests
        baseline = next((parent / 'before-snapshot/console/storyboard_multi.py' for parent in Path(__file__).resolve().parents
                         if (parent / 'before-snapshot/console/storyboard_multi.py').is_file()), None)
        if baseline is None:
            self.skipTest('Run inside the guarded V9a5 snapshot to compare start-of-task bytes')
        spec = importlib.util.spec_from_file_location('console._v9a5_before_multi', baseline)
        old = importlib.util.module_from_spec(spec); spec.loader.exec_module(old)
        V9a4Tests.setUpClass()
        case = V9a4Tests('test_multiple_cells_one_reason_one_preview_only_selected_bytes_change')
        try:
            case.setUp()
            _, child, sid = case.prepared_child()
            directory = store.project_dir(child['id'])
            selected = child['segments_plan']['segments'][0]['cells'][:2]
            body = {'cells': selected, 'request': '调整所选镜头的背景'}
            _, selected, current = multi._inputs(child, sid, body, directory)
            encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
            self.assertEqual(encode(multi.reasoning_messages(child, sid, selected, current, body['request'], directory)),
                             encode(old.reasoning_messages(child, sid, selected, current, body['request'], directory)))
            refs = [dict(key='current', name='当前新分镜', path=str(directory / current['clean_file']))]
            refs += edits.material_options(child, sid, directory)
            revision = {'cells': [dict(cell=cid, changes='调整背景', keep='主体') for cid in selected]}
            self.assertEqual(multi.image_prompt(child, sid, refs, revision).encode(), old.image_prompt(child, sid, refs, revision).encode())
            self.assertEqual(multi._binding(child, sid, selected, current, directory), old._binding(child, sid, selected, current, directory))
            self.assertEqual(case.paid.call_count, 0)
        finally:
            case.doCleanups()
            V9a4Tests.tearDownClass()


if __name__ == '__main__':
    unittest.main()
