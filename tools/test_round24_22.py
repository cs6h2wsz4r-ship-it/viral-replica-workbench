"""24.22: read-only user-project copy plus independent synthetic regressions."""
import copy
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import config, gateway, jimmy_bridge, pipeline, replacement_guard as guard, scene_plan, store, runtime_env, speech_timing
from tools import test_round24_17 as fixture

FATHER = 'img1030122655-fa8c'
DISPLAY = 'img1030144927-e5ad'
BOTH = 'img1030122706-b036'
EARLY = 'img1030194736-7819'
BAD_CODES = {'person_image_mismatch', 'camera_image_stale', 'script_stale'}


class UserProjectCopyTests(unittest.TestCase):
    def setUp(self):
        source = ROOT / 'projects/p1030090003-ba53'
        if not (source / 'project.json').is_file():
            self.skipTest('用户已删除只读回归项目；自造回归仍独立运行')
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.temp))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('禁止读配置')))
        self.enterContext(patch.object(config, 'load', return_value=dict(config.DEFAULTS)))
        self.reason = self.enterContext(patch.object(gateway, 'reason_checked', side_effect=AssertionError('换图禁止调用模型')))
        self.enterContext(patch.object(gateway, 'reason', side_effect=AssertionError('只读回归禁止调用模型')))
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('禁止真实请求')))
        self.pid = source.name
        self.directory = self.temp / self.pid
        # Explicit 24.22 exception: copy this project only, never mutate the source.
        shutil.copytree(source, self.directory, ignore=shutil.ignore_patterns('console.env*', 'openAiKey.txt', '.DS_Store'))
        self.data = store.load(self.pid)

    def assert_clean(self, data):
        for task in data['script']['tasks']:
            check = guard.check(data, task, self.directory)
            codes = {i['code'] for i in check['issues']}
            self.assertFalse(codes & BAD_CODES, (task['id'], codes))
            if 'camera_scene_mismatch' not in codes:
                self.assertEqual(check['errors'], [])

    def test_paid_images_are_valid_read_only_and_script_not_stale(self):
        before = (self.directory / 'project.json').read_bytes()
        cast = scene_plan.scene_cast(self.data, self.directory)
        self.assertEqual([cast[sid]['camera_asset'] for sid in ['S1', 'S2', 'S4']], [FATHER, DISPLAY, EARLY])
        for aid, sid in [(FATHER, 'S1'), (DISPLAY, 'S2'), (EARLY, 'S4'), (BOTH, 'S4')]:
            self.assertTrue(scene_plan.camera_valid(self.data, aid, sid, self.directory), aid)
        self.assert_clean(self.data)
        self.assertTrue(11.2 <= cast['S4']['frame_at'] <= 11.7)
        self.assertIn('父亲、儿子都在画面里', cast['S4']['frame']['reason'])
        notice = cast['S4'].get('camera_frame_notice', '')
        for phrase in ['9.65 秒', '儿子还没进画面', '改用之前的版本', '11.44 秒重新生成']:
            self.assertIn(phrase, notice)
        self.assertEqual((self.directory / 'project.json').read_bytes(), before)

    def test_http_project_view_uses_compatible_script_provenance(self):
        before = (self.directory / 'project.json').read_bytes()
        with patch.object(runtime_env, 'apply'):
            import server
        with patch.object(speech_timing, 'for_run', return_value=None):
            view = server.project_view(copy.deepcopy(self.data))
        self.assertFalse(view['scene_edits_script_stale'])
        self.assertEqual(view['scene_cast']['S4']['camera_asset'], EARLY)
        self.assertEqual((self.directory / 'project.json').read_bytes(), before)

    def test_one_click_repair_then_use_previous_version_without_models(self):
        old = copy.deepcopy(self.data['script']['tasks'])
        self.assertEqual(pipeline.follow_scene_camera_selection(self.pid, 'S4'), ['seg-03'])
        repaired = store.load(self.pid)
        self.assertEqual(repaired['script']['tasks'][-1]['image_ids'][0], EARLY)
        for a, b in zip(old, repaired['script']['tasks']):
            self.assertEqual({k:v for k,v in a.items() if k!='image_ids'}, {k:v for k,v in b.items() if k!='image_ids'})
            self.assertEqual(a['image_ids'][1:], b['image_ids'][1:])
        result = pipeline.use_scene_camera_image(self.pid, 'S4', BOTH)
        self.assertEqual(result['script']['tasks'][-1]['image_ids'][0], BOTH)
        self.assertEqual(result['replacements']['scene_plan']['S4']['camera_asset'], BOTH)
        self.assertNotIn('scene:S4', result.get('camera_notice', {}))
        self.assert_clean(result)
        self.reason.assert_not_called()

    def test_legacy_compatibility_ignores_frame_display_but_rejects_real_changes(self):
        self.data['replacements']['scene_plan']['S4'].update(frame_at=11.5, frame_asset_id='different', frame_mode='manual')
        for asset in self.data['assets']:
            asset['present_text'] = 'changed display text'
            for row in asset.get('scene_camera_subjects') or []:
                row['original_file'] = 'new-thumbnail.jpg'
        self.assert_clean(self.data)
        self.data['replacements']['characters'][0]['description'] = '换穿红色外套'
        self.assertFalse(guard.script_current(self.data, self.directory))
        self.assertFalse(scene_plan.camera_valid(self.data, FATHER, 'S1', self.directory))


class SyntheticSceneTests(unittest.TestCase):
    setUp = fixture.Round24_17Tests.setUp
    persist = fixture.Round24_17Tests.persist
    frame = staticmethod(fixture.Round24_17Tests.frame)
    camera = fixture.Round24_17Tests.camera
    script = fixture.Round24_17Tests.script

    def frozen_camera(self):
        info = pipeline.capture_scene_frame(self.pid, 'S4', at=9.65)
        self.data = store.load(self.pid)
        self.camera('paid')
        self.data['assets'][-1].update(frame_asset_id=info['asset_id'], frame_at=info['at'],
            scene_camera_subjects=scene_plan.effective_subjects(self.data, 'S4'))
        self.script(['paid', 'father', 'son', 'watch'])
        return self.data['assets'][-1]

    def test_auto_rule_and_manual_reselection_preserve_paid_frame(self):
        self.frozen_camera()
        self.data['replacements']['scene_plan']['S4'].update(frame_mode='auto', frame_at=11.44)
        with patch.object(scene_plan, 'frame_choice', return_value={'at': 11.7}):
            self.assertTrue(scene_plan.camera_valid(self.data, 'paid', 'S4', self.directory))
        self.persist()
        pipeline.capture_scene_frame(self.pid, 'S4', at=11.4)
        self.assertTrue(scene_plan.camera_valid(store.load(self.pid), 'paid', 'S4', self.directory))

    def test_display_fields_never_affect_binding_or_script(self):
        camera = self.frozen_camera()
        fingerprint = guard.fingerprint(self.data, self.directory)
        camera['scene_camera_subjects'][0]['original_file'] = 'new-thumbnail.jpg'
        camera['present_text'] = 'another display label'
        self.data['replacements']['scene_plan']['S4'].update(frame_reason='new display rule', present_text='changed')
        with patch.object(scene_plan, 'original_file', return_value='changed.jpg'):
            self.assertTrue(scene_plan.camera_valid(self.data, 'paid', 'S4', self.directory))
            self.assertEqual(fingerprint, guard.fingerprint(self.data, self.directory))

    def test_frame_and_camera_choice_do_not_change_script_fingerprint(self):
        self.frozen_camera()
        fingerprint = guard.fingerprint(self.data, self.directory)
        self.data['replacements']['scene_plan']['S4'].update(frame_at=11.4, frame_asset_id='different', frame_mode='auto', camera_asset='new')
        self.assertEqual(fingerprint, guard.fingerprint(self.data, self.directory))
        self.assertNotIn('script_stale', {i['code'] for i in guard.check(self.data, self.data['script']['tasks'][0], self.directory)['issues']})
        self.data['replacements']['characters'][0]['description'] = 'red coat'
        self.assertNotEqual(fingerprint, guard.fingerprint(self.data, self.directory))
        self.assertFalse(scene_plan.camera_valid(self.data, 'paid', 'S4', self.directory))

    def test_changed_photo_bytes_or_scene_description_invalidates_camera(self):
        self.frozen_camera()
        (self.directory / 'assets/father.png').write_bytes(b'changed identity')
        self.assertFalse(scene_plan.camera_valid(self.data, 'paid', 'S4', self.directory))
        (self.directory / 'assets/father.png').write_bytes(b'father')
        self.assertTrue(scene_plan.camera_valid(self.data, 'paid', 'S4', self.directory))
        self.data['replacements']['scene'] = 'new room'
        self.assertFalse(scene_plan.camera_valid(self.data, 'paid', 'S4', self.directory))

    def test_all_selected_people_are_sent_even_with_early_manual_frame(self):
        draft = pipeline.draft_scene_camera_image(self.pid, 'openai', 'S4', at=9.65)
        self.assertEqual(draft['image_ids'][:3], ['father', 'son', 'watch'])
        self.assertNotIn('not in this frame', draft['prompt'])
        self.assertNotIn('这张主镜头图里没有他', draft['frame_reason'])
        self.assertEqual(scene_plan.frame_choice(self.data, 'S4')['at'], 11.44)

    def test_direct_sync_replaces_every_same_scene_position_without_reordering_text(self):
        self.camera('old'); self.camera('new')
        self.script(['father', 'old', 'watch', 'son'])
        self.data['script']['tasks'][0]['direction'] = 'scene from @image3; person from @image2'
        old = copy.deepcopy(self.data['script']['tasks'][0]); self.persist()
        with patch.object(gateway, 'reason_checked', side_effect=AssertionError('禁止模型')) as reason:
            self.assertEqual(pipeline.follow_scene_camera_selection(self.pid, 'S4'), ['seg-04'])
        task = store.load(self.pid)['script']['tasks'][0]
        self.assertEqual(task['image_ids'], ['father', 'new', 'watch', 'son'])
        self.assertEqual({k:v for k,v in task.items() if k!='image_ids'}, {k:v for k,v in old.items() if k!='image_ids'})
        reason.assert_not_called()

    def test_cross_scene_secondary_camera_references_follow_without_reordering(self):
        self.camera('old'); self.camera('new')
        self.script(['father', 'old', 'watch', 'son'])
        task = self.data['script']['tasks'][0]
        task.update(scene_id='S1', source_start=0, source_end=4.8, direction='Use @image2 for the kitchen.')
        self.persist()
        with patch.object(gateway, 'reason_checked', side_effect=AssertionError('禁止模型')):
            self.assertEqual(pipeline.follow_scene_camera_selection(self.pid, 'S4'), ['seg-04'])
        saved = store.load(self.pid)['script']['tasks'][0]
        self.assertEqual(saved['image_ids'], ['father', 'new', 'watch', 'son'])
        self.assertEqual(saved['direction'], task['direction'])

    def test_remove_person_is_a_real_script_change(self):
        self.data['replacements']['characters'][0]['action'] = 'keep'
        before = guard.fingerprint(self.data, self.directory)
        self.data['replacements']['characters'][0]['action'] = 'remove'
        self.assertNotEqual(before, guard.fingerprint(self.data, self.directory))

    def test_legacy_fingerprint_cannot_hide_keep_to_remove(self):
        self.data['replacements']['characters'][0]['action'] = 'keep'
        rows = [{'scene_id': scene['id'], 'binding': scene_plan._legacy_binding(self.data, scene['id'], self.directory),
                 'camera_asset': None} for scene in guard.scenes(self.data)]
        self.data['script'] = {'tasks': [], 'replacements_fingerprint': hashlib.sha256(
            json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
        self.assertTrue(guard.script_current(self.data, self.directory))
        self.data['replacements']['characters'][0]['action'] = 'remove'
        self.assertFalse(guard.script_current(self.data, self.directory))

    def test_pending_image_without_active_asset_has_no_in_use_notice(self):
        self.data['images']['pending'] = {'id': 'pending', 'state': 'pending_confirmation',
            'for_task': 'scene-camera:S4', 'frame_at': 9.65}
        self.assertEqual(scene_plan.scene_cast(self.data, self.directory)['S4'].get('camera_frame_notice', ''), '')

    def test_image_completion_retains_submitted_frame_after_auto_rule_changes(self):
        draft = pipeline.draft_scene_camera_image(self.pid, 'openai', 'S4', at=9.65)
        record = jimmy_bridge.image_preview(self.pid, draft['prompt'], draft['image_ids'],
            '9:16', 'gpt-image-2', '2k', 'scene-camera:S4', channel='official', draft_binding=draft['camera_binding'])
        output = self.directory / 'image-runs' / record['id'] / 'result.png'
        output.write_bytes(b'generated offline image')
        # Local completion only; the image has already been generated by a mock.
        with patch.object(scene_plan, 'frame_choice', return_value={'at': 11.44}):
            jimmy_bridge._image_done(self.pid, record['id'], output)
        data = store.load(self.pid)
        asset = next(a for a in data['assets'] if a['id'] == record['id'])
        self.assertEqual(asset['frame_at'], 9.65)
        self.assertEqual(asset['frame_asset_id'], draft['frame_asset_id'])
        self.assertTrue(scene_plan.camera_valid(data, asset['id'], 'S4', self.directory))
        self.assertEqual(data['replacements']['scene_plan']['S4']['camera_asset'], asset['id'])

    def test_safety_discards_unlisted_changes_but_applies_matching_list_and_logs(self):
        self.script(['father', 'son', 'watch'])
        script = copy.deepcopy(self.data['script']); task = script['tasks'][0]
        task.update(action='the father lifts a hand', story='Keep source motion.', direction='Calm.')
        response = {'tasks': [{'id': task['id'], 'action': 'the person from @image1 jumps twice',
            'story': 'New unrelated story', 'direction': 'Calm.',
            'changes': [{'field': 'action', 'before': 'the father', 'after': 'the person from @image1'}]}]}
        meta = {'provider': 'mock', 'model': 'offline', 'attempts': []}
        with patch.object(gateway, 'reason_checked', return_value=(response, meta)), patch.object(store, 'log') as log:
            pipeline.apply_replacement_safety(self.data, script)
        self.assertEqual(task['action'], 'the person from @image1 lifts a hand')
        self.assertEqual(task['story'], 'Keep source motion.')
        self.assertTrue(log.called)
        response['tasks'][0]['changes'][0]['before'] = 'missing original phrase'
        with patch.object(gateway, 'reason_checked', return_value=(response, meta)), self.assertRaisesRegex(ValueError, '清单与原文不一致'):
            pipeline.apply_replacement_safety(self.data, copy.deepcopy(self.data['script']))


if __name__ == '__main__':
    unittest.main()
