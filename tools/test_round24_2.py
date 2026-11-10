"""Round 24.2: temporary frozen-project copies, real script safety, mocked models/I/O."""
import copy
import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import config, gateway, jimmy_bridge, pipeline, replacement_guard as guard, runtime_env, store
from tools.project_fixture import load_project
with patch.object(runtime_env, 'apply'):
    import server


class Round24_2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.temp))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('禁止读取配置')))
        self.enterContext(patch.object(config, 'load', return_value=dict(config.DEFAULTS)))
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('禁止真实请求')))
        self.enterContext(patch.object(jimmy_bridge, '_call', side_effect=AssertionError('禁止真实模型')))
        self.reason = self.enterContext(patch.object(gateway, 'reason_checked', side_effect=self.safety_echo))
        self.pid = store.create('24.2 只读事故副本')['id']
        self.directory = store.project_dir(self.pid)
        data = load_project('p1028222359-9b21')
        data.update(id=self.pid, runs={}, images={}, log=[])
        data['breakdown']['scene'] = [
            {'id': 'S1', 'name': '客厅', 'timeRanges': [[0, 8]]},
            {'id': 'S2', 'name': '户外', 'timeRanges': [[8, 16]]}]
        data['breakdown']['shots'] = [{'start': 0, 'end': 16, 'characters': ['C1']}]
        self.character = data['replacements']['characters'][0]
        self.portrait = self.character['asset_id']
        self.character['camera_assets'] = {'S1': 'old', 'S2': 'scene2'}
        self.character.pop('camera_asset_id', None)
        self.base_task = {'id': 'seg-01', 'kind': 'speaker', 'mode': 'video', 'dialogue': 'Hello!',
            'action': 'The person from @image1 smiles.', 'story': '', 'direction': 'Scene from @image3.',
            'source_start': 0, 'source_end': 4, 'duration': 4, 'ratio': '9:16',
            'image_ids': ['old', self.portrait], 'blank_captions': False}
        data['script'] = {'provider': 'deepseek', 'model': 'mock', 'request_model': 'mock',
            'tasks': [copy.deepcopy(self.base_task)]}
        # Only the JSON is frozen input. All media in this isolated copy are fake.
        for asset in data['assets']:
            path = self.directory / asset['file']; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'offline media'); asset['sha256'] = store.sha256_file(path)
        for aid, sid in [('old', 'S1'), ('new', 'S1'), ('scene2', 'S2')]:
            file = self.directory / f'assets/{aid}.png'; file.write_bytes(b'offline camera')
            data['assets'].append({'id': aid, 'type': 'image', 'label': aid, 'file': f'assets/{aid}.png',
                'roles': ['target_state'], 'camera_character': 'C1', 'camera_scene': sid,
                'sha256': store.sha256_file(file)})
            data['images'][aid] = {'id': aid, 'asset_id': aid, 'state': 'completed', 'for_task': f'camera:C1:{sid}'}
        store.update(self.pid, lambda d: d.update(data))
        def bind(d):
            for asset in d['assets']:
                if asset.get('camera_character'):
                    asset['camera_binding'] = guard.camera_binding(d, asset['camera_character'], self.directory,
                        scene_id=asset.get('camera_scene'))
            d['script']['replacements_fingerprint'] = guard.fingerprint(d, self.directory)
        store.update(self.pid, bind)

    @staticmethod
    def safety_echo(step, messages, **kwargs):
        assert step == 'check'
        tasks = json.loads(messages[-1]['content'])['tasks']
        return {'tasks': [{**{k: t.get(k, '') for k in ('id', 'action', 'story', 'direction')},
                           'changes': []} for t in tasks]}, {'provider': 'mock', 'model': 'mock', 'attempts': []}

    def dispatch(self, method, tail, body=None):
        handler = object.__new__(server.Handler)
        handler.path = f'/api/projects/{self.pid}/{tail}'
        handler.headers = {'X-Client-Build': 'offline'}
        with patch.object(handler, '_body_json', return_value=body or {}), \
                patch.object(handler, '_json') as response, \
                patch.object(server, 'build_status', return_value={'build': 'offline', 'restart_pending': False}):
            handler._dispatch(method)
        return response.call_args.args

    def use(self, aid='new', **extra):
        return self.dispatch('POST', 'camera-image/use', {'character_id': 'C1', 'scene_id': 'S1', 'asset_id': aid, **extra})

    def test_new_completion_becomes_in_use_and_follows_existing_script(self):
        store.update(self.pid, lambda d: d['images'].update({'generated': {'id': 'generated', 'state': 'running',
            'for_task': 'camera:C1:S1', 'camera_character': 'C1', 'camera_scene': 'S1'}}))
        output = self.temp / 'output.png'; output.write_bytes(b'mock generated')
        jimmy_bridge._image_done(self.pid, 'generated', output)
        data = store.load(self.pid)
        self.assertEqual(data['replacements']['characters'][0]['camera_assets']['S1'], 'generated')
        self.assertEqual(data['script']['tasks'][0]['image_ids'][0], 'generated')
        self.assertTrue(self.reason.called)

    def test_use_changes_only_camera_fields_and_clears_notice(self):
        before = store.load(self.pid)['replacements']
        store.update(self.pid, lambda d: d.update(camera_notice={'C1:S1': '旧提示'}))
        result, = self.use()
        after = store.load(self.pid)
        expected = copy.deepcopy(before); expected['characters'][0]['camera_assets']['S1'] = 'new'
        self.assertEqual(after['replacements'], expected)
        self.assertEqual(result['camera_selection']['synced_tasks'], ['seg-01'])
        self.assertIn('已改用“new”，seg-01 的图1 已同步', result['camera_selection']['message'])
        self.assertNotIn('C1:S1', after.get('camera_notice', {}))

    def test_invalid_deleted_stale_wrong_scene_and_wrong_character_are_400(self):
        for aid in ['missing', self.portrait, 'scene2']:
            with self.subTest(aid=aid):
                payload, status = self.use(aid)
                self.assertEqual(status, 400); self.assertRegex(payload['error'], '[\u4e00-\u9fff]')
        payload, status = self.use('new', character_id='C2')
        self.assertEqual(status, 400)
        self.assertEqual(self.use('new', scene_id=[])[1], 400)
        (self.directory / 'assets/new.png').unlink()
        self.assertEqual(self.use()[1], 400)
        (self.directory / 'assets/new.png').write_bytes(b'offline camera')
        store.update(self.pid, lambda d: next(a for a in d['assets'] if a['id'] == 'new').update(camera_binding='stale'))
        self.assertEqual(self.use()[1], 400)
        self.assertEqual(store.load(self.pid)['replacements']['characters'][0]['camera_assets']['S1'], 'old')

    def test_deleted_incident_reference_uses_one_safe_save_and_preserves_completed_run(self):
        deleted = 'img1029082809-c45b'
        def seed(d):
            d['assets'] = [a for a in d['assets'] if a['id'] != deleted]
            # No metadata remains in assets; id alone must also identify the historical record.
            d['images'][deleted] = {'id': deleted, 'for_task': 'camera:C1:S1', 'state': 'completed'}
            d['script']['tasks'][0]['image_ids'][0] = deleted
            d['script']['tasks'].append({**copy.deepcopy(d['script']['tasks'][0]), 'id': 'seg-02'})
            d['runs'] = {'seg-01': [{'rev': 'r1', 'state': 'completed', 'result': 'saved.mp4'},
                                  {'rev': 'r2', 'state': 'pending_confirmation'}]}
        store.update(self.pid, seed)
        completed = copy.deepcopy(store.load(self.pid)['runs']['seg-01'][0])
        with patch.object(pipeline, 'save_script', wraps=pipeline.save_script) as save, \
                patch.object(pipeline, 'apply_replacement_safety', wraps=pipeline.apply_replacement_safety) as safety:
            result, = self.use()
        self.assertEqual(save.call_count, 1); self.assertEqual(safety.call_count, 1)
        self.assertEqual(set(save.call_args.kwargs['camera_reference_aliases']), {'seg-01', 'seg-02'})
        self.assertTrue(self.reason.called)
        data = store.load(self.pid)
        self.assertTrue(all(t['image_ids'][0] == 'new' for t in data['script']['tasks']))
        self.assertTrue(all(t['direction'] == 'Scene from @image1.' for t in data['script']['tasks']))
        self.assertEqual(data['runs']['seg-01'][0], completed)
        self.assertEqual(data['runs']['seg-01'][1]['state'], 'superseded')
        self.assertEqual(data['script']['replacements_fingerprint'], guard.fingerprint(data, self.directory))
        self.assertEqual(result['camera_selection']['synced_tasks'], ['seg-01', 'seg-02'])
        self.assertTrue(any('seg-01、seg-02 的图1 已同步' in row['msg'] for row in data['log']))

    def test_safety_rejection_is_not_bypassed(self):
        previous = store.load(self.pid)
        with patch.object(gateway, 'reason_checked', return_value=({'tasks': []}, {})):
            payload, status = self.use()
        self.assertEqual(status, 400); self.assertIn('映射安全改写', payload['error'])
        self.assertEqual(store.load(self.pid)['script'], previous['script'])
        self.assertEqual(store.load(self.pid)['replacements'], previous['replacements'])

    def test_generated_image_stays_completed_when_script_safety_rejects_sync(self):
        previous = store.load(self.pid)['script']
        store.update(self.pid, lambda d: d['images'].update({'generated': {'id': 'generated', 'state': 'running',
            'for_task': 'camera:C1:S1', 'camera_character': 'C1', 'camera_scene': 'S1'}}))
        output = self.temp / 'output.png'; output.write_bytes(b'mock generated')
        with patch.object(gateway, 'reason_checked', return_value=({'tasks': []}, {})):
            jimmy_bridge._image_done(self.pid, 'generated', output)
        data = store.load(self.pid)
        self.assertEqual(data['images']['generated']['state'], 'completed')
        self.assertEqual(data['replacements']['characters'][0]['camera_assets']['S1'], 'generated')
        self.assertEqual(data['script'], previous)
        self.assertIn('同步失败', data['camera_notice']['C1:S1'])
        self.assertTrue(any('同步失败' in row['msg'] for row in data['log']))

    def test_person_photo_other_scene_and_unreplaced_person_do_not_follow(self):
        def seed(d):
            d['script']['tasks'] = [
                {**copy.deepcopy(self.base_task), 'image_ids': [self.portrait], 'id': 'portrait'},
                {**copy.deepcopy(self.base_task), 'source_start': 8, 'source_end': 12, 'id': 'other-scene'},
                {**copy.deepcopy(self.base_task), 'source_start': 4, 'source_end': 8, 'id': 'other-person'}]
            d['breakdown']['shots'] = [{'start': 0, 'end': 4, 'characters': ['C1']},
                {'start': 4, 'end': 8, 'characters': ['C2']}, {'start': 8, 'end': 16, 'characters': ['C1']}]
        store.update(self.pid, seed)
        before = store.load(self.pid)['script']
        result, = self.use()
        self.assertEqual(result['script'], before)
        self.assertEqual(result['camera_selection']['synced_tasks'], [])
        self.assertEqual(result['camera_selection']['message'], '已改用“new”')
        self.reason.assert_not_called()

    def test_delete_in_use_falls_back_syncs_and_leaves_notice(self):
        self.use()
        result, = self.dispatch('DELETE', 'assets/new')
        self.assertEqual(result['replacements']['characters'][0]['camera_assets']['S1'], 'old')
        self.assertEqual(result['script']['tasks'][0]['image_ids'][0], 'old')
        self.assertEqual(result['camera_notice']['C1:S1'], '原来在用的图已删除，已改用“old”')
        self.assertEqual(result['images']['new']['asset_id'], 'new')

    def test_delete_last_in_use_leaves_script_and_blocks_invalid_camera(self):
        self.dispatch('DELETE', 'assets/new')
        previous = store.load(self.pid)['script']
        result, = self.dispatch('DELETE', 'assets/old')
        self.assertIsNone(result['replacements']['characters'][0]['camera_assets']['S1'])
        self.assertEqual(result['script'], previous)
        self.assertIn('还没有可用的图', result['camera_notice']['C1:S1'])
        check = guard.check(store.load(self.pid), previous['tasks'][0], self.directory)
        self.assertIn('invalid_camera_image', [i['code'] for i in check['issues']])

    def test_no_script_no_changes_and_new_generation_clears_notice(self):
        store.update(self.pid, lambda d: d.update(script=None, camera_notice={'C1:S1': '删除提示'}))
        result, = self.use()
        self.assertEqual(result['camera_selection']['message'], '已改用“new”')
        self.assertIsNone(result['script'])
        store.update(self.pid, lambda d: d.update(camera_notice={'C1:S1': '删除提示'}))
        store.update(self.pid, lambda d: d['images'].update({'fresh': {'id': 'fresh', 'state': 'running',
            'for_task': 'camera:C1:S1', 'camera_character': 'C1', 'camera_scene': 'S1'}}))
        output = self.temp / 'fresh.png'; output.write_bytes(b'fake')
        jimmy_bridge._image_done(self.pid, 'fresh', output)
        self.assertNotIn('C1:S1', store.load(self.pid).get('camera_notice', {}))
        self.reason.assert_not_called()

    def test_saving_other_replacement_fields_does_not_reset_selected_older_camera(self):
        data = store.load(self.pid)
        form = copy.deepcopy(data['replacements'])
        for character in form['characters']:
            character.pop('camera_assets', None)
        saved = pipeline.save_replacements(self.pid, form)
        self.assertEqual(saved['replacements']['characters'][0]['camera_assets']['S1'], 'old')

    def test_legacy_camera_completion_keeps_current_selection(self):
        store.update(self.pid, lambda d: d['images'].update({'legacy': {'id': 'legacy', 'state': 'running',
            'for_task': 'camera:C1', 'camera_character': 'C1',
            'camera_binding': guard.camera_binding(d, 'C1', self.directory)}}))
        previous = store.load(self.pid)['script']
        output = self.temp / 'legacy.png'; output.write_bytes(b'mock legacy')
        jimmy_bridge._image_done(self.pid, 'legacy', output)
        saved = store.load(self.pid)
        self.assertEqual(saved['replacements']['characters'][0]['camera_assets']['S1'], 'old')
        self.assertEqual(saved['script'], previous)
        self.reason.assert_not_called()

    def test_delete_unselected_version_does_not_change_in_use_or_script(self):
        previous = store.load(self.pid)
        result, = self.dispatch('DELETE', 'assets/new')
        self.assertEqual(result['script'], previous['script'])
        self.assertEqual(result['replacements'], previous['replacements'])
        self.assertNotIn('C1:S1', result.get('camera_notice', {}))
        self.reason.assert_not_called()

    def test_generation_follow_holds_project_lock_through_safety(self):
        acquired = []
        def probe():
            lock = store._lock(self.pid)
            allowed = lock.acquire(blocking=False)
            if allowed:
                lock.release()
            return allowed
        def safety(*args, **kwargs):
            with ThreadPoolExecutor(max_workers=1) as pool:
                acquired.append(pool.submit(probe).result())
            return self.safety_echo(*args, **kwargs)
        store.update(self.pid, lambda d: d['images'].update({'generated': {'id': 'generated', 'state': 'running',
            'for_task': 'camera:C1:S1', 'camera_character': 'C1', 'camera_scene': 'S1'}}))
        output = self.temp / 'output.png'; output.write_bytes(b'mock generated')
        with patch.object(gateway, 'reason_checked', side_effect=safety):
            jimmy_bridge._image_done(self.pid, 'generated', output)
        self.assertEqual(acquired, [False], '自动同步的安全校对与保存必须持项目锁，避免批量场景脚本冲突')
        self.assertEqual(store.load(self.pid)['script']['tasks'][0]['image_ids'][0], 'generated')

    def test_batch_scene_completions_each_select_and_sync_their_own_camera(self):
        def seed(d):
            d['script']['tasks'].append({**copy.deepcopy(self.base_task), 'id': 'seg-02',
                'source_start': 8, 'source_end': 12, 'image_ids': ['scene2', self.portrait]})
            for iid, sid in [('generated1', 'S1'), ('generated2', 'S2')]:
                d['images'][iid] = {'id': iid, 'state': 'running', 'for_task': f'camera:C1:{sid}',
                    'camera_character': 'C1', 'camera_scene': sid}
        store.update(self.pid, seed)
        outputs = []
        for iid in ['generated1', 'generated2']:
            path = self.temp / f'{iid}.png'; path.write_bytes(b'fake output'); outputs.append((iid, path))
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda pair: jimmy_bridge._image_done(self.pid, *pair), outputs))
        saved = store.load(self.pid)
        self.assertEqual(saved['replacements']['characters'][0]['camera_assets'], {'S1': 'generated1', 'S2': 'generated2'})
        self.assertEqual([t['image_ids'][0] for t in saved['script']['tasks']], ['generated1', 'generated2'])
        self.assertFalse(saved.get('camera_notice'))
        self.assertEqual(self.reason.call_count, 2)


if __name__ == '__main__':
    from tools.run_tests_offline import audit
    sys.dont_write_bytecode = True
    sys.addaudithook(audit)
    unittest.main(verbosity=2)
