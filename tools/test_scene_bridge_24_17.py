"""Self-built scene-camera bridge fixtures; no real project or model I/O."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import config, gateway, jimmy_bridge as bridge, pipeline, replacement_guard as guard, scene_plan, store


def scene_data():
    return {
        'breakdown': {
            'characters': [{'id': 'C1', 'name': '父亲'}, {'id': 'C2', 'name': '儿子'}],
            'scene': [{'id': 'S1', 'name': '门廊', 'timeRanges': [[0, 5]]},
                      {'id': 'S4', 'name': '厨房', 'timeRanges': [[5, 12.3]]}],
            'shots': [{'start': 0, 'end': 5, 'characters': ['C1'], 'product': '无'},
                      {'start': 5, 'end': 10.58, 'characters': ['C1'], 'product': '手表'},
                      {'start': 10.58, 'end': 12.3, 'characters': ['C1', 'C2'], 'product': '手表'}],
            'productMoments': [{'start': 5, 'end': 12.3, 'product': '手表'}],
            'overlays': [],
        },
        'replacements': {
            'characters': [{'source_id': 'C1', 'action': 'replace', 'asset_id': 'father'},
                           {'source_id': 'C2', 'action': 'replace', 'asset_id': 'son'}],
            'product': {'action': 'replace', 'asset_id': 'watch'},
            'scene_plan': {'S4': {'subjects': {'C1': {'on': False},
                'C2': {'on': True, 'asset_id': 'other-son'}, 'product': {'on': False}}}},
        },
        'assets': [{'id': aid, 'type': 'image', 'file': f'assets/{aid}.png',
                    'label': aid, 'roles': [role]}
                   for aid, role in [('father', 'person_identity'), ('son', 'person_identity'),
                                     ('other-son', 'person_identity'), ('watch', 'product_truth')]],
        'images': {}, 'script': {'tasks': []}, 'runs': {},
        'source': {'file': 'source/source.mp4', 'probe': {'duration': 12.3}},
    }


class SceneBridgeTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'runtime/temp').mkdir(parents=True, exist_ok=True)
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.temp))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('禁止配置读取')))
        self.enterContext(patch.object(config, 'load', return_value={**config.DEFAULTS}))
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('禁止联网')))
        self.enterContext(patch.object(gateway, 'reason', side_effect=AssertionError('禁止推理请求')))
        self.enterContext(patch.object(bridge, '_call', side_effect=AssertionError('禁止实际通道调用')))
        self.pid = store.create('自造分场桥接测试')['id']
        self.directory = store.project_dir(self.pid)
        self.data = scene_data()
        self.data['id'] = self.pid
        self.persist()

    def persist(self):
        for asset in self.data['assets']:
            path = self.directory / asset['file']
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'offline fixture ' + asset['id'].encode())
            asset['sha256'] = store.sha256_file(path)
        store.update(self.pid, lambda d: d.update(copy.deepcopy(self.data)))

    def task(self, **kwargs):
        return {'id': 'seg-04', 'source_start': 5, 'source_end': 12.3,
                'kind': 'speaker', 'mode': 'video', 'dialogue': 'Hello',
                'action': '', 'image_ids': [], 'blank_captions': False, **kwargs}

    def test_only_selected_effective_people_and_mappings(self):
        people = guard.replaced_people(self.data, self.task())
        self.assertEqual([(p['source_id'], p['asset_id']) for p in people], [('C2', 'other-son')])
        mappings = guard.source_mappings(self.data, self.task(image_ids=['other-son']))
        self.assertEqual([r['source_id'] for r in mappings], ['C2'])
        self.assertEqual(mappings[0]['reference'], 'the person from @image1')

    def test_product_only_references_exclude_unchecked_photos(self):
        self.data['replacements']['scene_plan']['S4']['subjects'] = {
            'C1': {'on': False}, 'C2': {'on': False}, 'product': {'on': True}}
        self.assertTrue(callable(getattr(bridge, 'scene_image_references', None)))
        refs = bridge.scene_image_references(self.data, 'S4')
        self.assertEqual(refs['image_ids'], ['watch'])
        self.assertIsNone(refs['anchor_asset_id'])

    def test_scene_preview_records_binding_and_rejects_wrong_order(self):
        self.data['replacements']['scene_plan']['S4']['subjects'] = {}
        self.data['assets'].append({'id': 'frame', 'type': 'image', 'file': 'assets/frame.jpg',
            'roles': ['scene'], 'label': '参考帧', 'camera_scene': 'S4', 'frame_at': 11.4})
        self.persist()
        rec = bridge.image_preview(self.pid, '场景提示词', ['father', 'son', 'watch', 'frame'],
            '9:16', 'gpt-image-2', '2k', 'scene-camera:S4', channel='official')
        self.assertEqual(rec.get('camera_scene'), 'S4')
        self.assertTrue(rec.get('scene_camera_binding'))
        self.assertEqual([r['key'] for r in rec['scene_camera_subjects']], ['C1', 'C2', 'product'])
        with self.assertRaisesRegex(bridge.BridgeError, '顺序|参考图'):
            bridge.image_preview(self.pid, '错序', ['son', 'father', 'watch', 'frame'],
                '9:16', 'gpt-image-2', '2k', 'scene-camera:S4', channel='official')

    def test_scene_video_prompt_preserves_unchecked_people_and_product(self):
        prompt = bridge.compose_prompt(self.data, self.task(image_ids=['other-son']))
        self.assertIn('C2', prompt)
        self.assertIn('C1', prompt)
        self.assertIn('Keep', prompt)

    def add_camera(self, sid='S4', aid='main'):
        self.data['assets'].append({'id': aid, 'type': 'image', 'file': f'assets/{aid}.png',
            'roles': ['target_state'], 'label': '多人主镜头图', 'scene_camera_scene': sid,
            'camera_scene': sid})
        self.persist()
        asset = self.data['assets'][-1]
        asset['scene_camera_binding'] = scene_plan.binding(self.data, sid, self.directory)
        self.data['images'][aid] = {'id': aid, 'asset_id': aid, 'state': 'completed',
            'for_task': f'scene-camera:{sid}', 'scene_camera_scene': sid,
            'scene_camera_binding': asset['scene_camera_binding']}
        self.data['replacements']['scene_plan'].setdefault(sid, {})['camera_asset'] = aid
        self.data['script']['replacements_fingerprint'] = guard.fingerprint(self.data, self.directory)
        self.persist()
        return aid

    def test_one_scene_camera_satisfies_two_people_and_product(self):
        self.data['replacements']['scene_plan']['S4']['subjects'] = {}
        main = self.add_camera()
        task = self.task(image_ids=[main, 'father', 'son', 'watch'])
        checked = guard.check(self.data, task, self.directory)
        self.assertEqual(checked['errors'], [])
        self.assertEqual(set(guard.camera_images(self.data, 'S4').values()), {main})
        mappings = guard.source_mappings(self.data, task)
        self.assertEqual([m['reference'] for m in mappings], [
            'the person from @image2', 'the person from @image3', 'the outfit/product from @image4'])

    def test_local_change_stales_only_its_scene_and_rejects_paid_confirm(self):
        main = self.add_camera()
        before = scene_plan.binding(self.data, 'S1', self.directory)
        self.data['replacements']['scene_plan']['S4']['subjects']['C2']['asset_id'] = 'son'
        self.persist()
        self.assertIn(main, guard.camera_stale_assets(self.data, self.directory))
        self.assertEqual(scene_plan.binding(self.data, 'S1', self.directory), before)
        self.data['images']['pending'] = {'id': 'pending', 'state': 'pending_confirmation',
            'for_task': 'scene-camera:S4', 'scene_camera_binding': 'old'}
        self.persist()
        with self.assertRaisesRegex(bridge.BridgeError, '过期'):
            bridge._verify_camera_image_current(self.pid, 'pending')

    def test_image_limits_fail_before_channel_io_and_video_limit_is_four(self):
        self.assertLess(bridge.max_image_references('third_party'), 100)
        with self.assertRaisesRegex(bridge.BridgeError, '最多支持'):
            bridge.image_preview(self.pid, '过多', ['father'] * 100, '9:16',
                'gpt-image-2', '2k', 'scene-camera:S4', channel='official')
        checked = guard.check(self.data, self.task(image_ids=['other-son'] * 5), self.directory)
        self.assertIn('too_many_images', [i['code'] for i in checked['issues']])

    def test_completed_scene_camera_activates_and_syncs(self):
        self.data['replacements']['scene_plan']['S4']['subjects'] = {}
        self.data['images']['result'] = {'id': 'result', 'state': 'running',
            'for_task': 'scene-camera:S4', 'scene_camera_scene': 'S4', 'camera_scene': 'S4',
            'scene_camera_binding': scene_plan.binding(self.data, 'S4', self.directory),
            'scene_camera_subjects': scene_plan.effective_subjects(self.data, 'S4'),
            'camera_product_id': 'watch'}
        self.persist()
        output = self.temp / 'result.png'
        output.write_bytes(b'offline generated result')
        with patch.object(pipeline, 'follow_scene_camera_selection', create=True) as sync:
            bridge._image_done(self.pid, 'result', output)
        saved = store.load(self.pid)
        self.assertEqual(saved['replacements']['scene_plan']['S4']['camera_asset'], 'result')
        self.assertEqual(saved['images']['result']['state'], 'completed')
        sync.assert_called_once_with(self.pid, 'S4', report_failure=True)

    def test_legacy_prompt_is_identical_with_no_scene_plan(self):
        legacy = copy.deepcopy(self.data)
        del legacy['replacements']['scene_plan']
        task = self.task(image_ids=['son'])
        prompt = bridge.compose_prompt(legacy, task)
        self.assertNotIn('SCENE REPLACEMENTS', prompt)
        self.assertNotIn('scene', guard.replaced_people(legacy, task)[0])

    def test_removed_scene_marks_pending_preview_stale(self):
        self.data['images']['pending'] = {'id': 'pending', 'state': 'pending_confirmation',
            'for_task': 'scene-camera:S99', 'scene_camera_binding': 'old'}
        self.assertIn('pending', guard.camera_stale_images(self.data, self.directory))

    def test_image_mode_retains_unchecked_subject_without_video_token(self):
        prompt = bridge.compose_prompt(self.data, self.task(mode='image', image_ids=['other-son']))
        self.assertIn('SCENE REPLACEMENTS', prompt)
        self.assertNotIn('@video1', prompt)

    def test_product_only_scene_still_requires_its_selected_camera_in_image1(self):
        wrong_camera = self.add_camera('S1', 'porch-camera')
        self.data['replacements']['scene_plan']['S4']['subjects'] = {
            'C1': {'on': False}, 'C2': {'on': False}, 'product': {'on': True}}
        expected = self.add_camera('S4', 'product-main')
        for mode in ('video', 'image'):
            with self.subTest(mode=mode):
                task = self.task(mode=mode, image_ids=[wrong_camera, 'watch'])
                checked = guard.check(self.data, task, self.directory)
                issue = next((i for i in checked['issues'] if i['code'] == 'camera_scene_mismatch'), None)
                self.assertIsNotNone(issue, '只换商品也必须以本场在用主镜头图为图1')
                self.assertEqual(issue['expected_asset_id'], expected)
                self.assertEqual(issue['scene_id'], 'S4')
                self.assertEqual(issue['reference_index'], 1)
                self.assertNotIn('missing_camera_image', [i['code'] for i in checked['issues']])


if __name__ == '__main__':
    unittest.main()
