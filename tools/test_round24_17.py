"""24.17 scene camera integration: self-made projects, mocked models only."""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from console import config, gateway, jimmy_bridge, llm, media, pipeline, runtime_env, store
from console import replacement_guard as guard

EXTRACT_FRAME = media.extract_frame


class Round24_17Tests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=config.ROOT / 'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.temp / 'mock-projects'))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('禁止读配置')))
        self.enterContext(patch.object(config, 'load', return_value=dict(config.DEFAULTS)))
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('禁止真实请求')))
        self.reason = self.enterContext(patch.object(gateway, 'reason', return_value=(
            {'person': 'use identities from references', 'shot': 'keep framing', 'setting': 'source room'}, {})))
        self.enterContext(patch.object(llm, 'image_part', return_value=[]))
        self.enterContext(patch.object(jimmy_bridge, '_call', side_effect=AssertionError('禁止外部进程')))
        self.extract = self.enterContext(patch.object(media, 'extract_frame', side_effect=self.frame))
        self.data = store.create('自造父子四场景')
        self.pid = self.data['id']
        self.directory = store.project_dir(self.pid)
        self.data['source'] = {'file': 'source/source.mp4', 'probe': {'duration': 12.3, 'ratio_hint': '9:16'}}
        (self.directory / 'source/source.mp4').write_bytes(b'offline-source')
        for aid, label, role in [('father', '老人照片', 'person_identity'), ('son', '另一男子', 'person_identity'),
                                 ('watch', '手表', 'product_truth'), ('other', '另一张照片', 'person_identity')]:
            file = f'assets/{aid}.png'
            (self.directory / file).write_bytes(aid.encode())
            self.data['assets'].append({'id': aid, 'type': 'image', 'label': label, 'roles': [role],
                                       'file': file, 'sha256': store.sha256_file(self.directory / file)})
        self.data['breakdown'] = {'characters': [{'id': 'C1', 'name': '父亲'}, {'id': 'C2', 'name': '儿子'}],
            'scene': [{'id': f'S{i}', 'name': name, 'timeRanges': [[a, b]]}
                      for i, name, a, b in [(1, '住宅门廊', 0, 4.8), (2, '室内产品展示', 4.8, 5.8),
                                           (3, '佩戴与手机演示', 5.8, 9), (4, '厨房救援', 9, 12.3)]],
            'shots': [{'id': f'shot-{i}', 'start': a, 'end': b, 'characters': chars, 'product': prod, 'action': '正面'}
                      for i, a, b, chars, prod in [(1, 0, 4.8, ['C1'], '无'), (2, 4.8, 5.8, ['C1'], '手环'),
                          (3, 5.8, 9, ['C1'], '手环'), (4, 9, 10.58, ['C1'], '手环'),
                          (5, 10.58, 12.3, ['C1', 'C2'], '手环')]],
            'productMoments': [{'start': 4.8, 'end': 12.3, 'description': '手环清楚'}], 'overlays': []}
        self.data['replacements'] = {'characters': [{'source_id': 'C1', 'action': 'replace', 'asset_id': 'father', 'description': ''},
                                                   {'source_id': 'C2', 'action': 'replace', 'asset_id': 'son', 'description': ''}],
                                     'product': {'action': 'replace', 'asset_id': 'watch', 'description': '真实手表'},
                                     'scene': '', 'scene_plan': {}}
        self.persist()

    @staticmethod
    def frame(src, at, out, **kw):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(f'frame:{at}'.encode())
        return out

    def persist(self):
        store.update(self.pid, lambda data: data.update(copy.deepcopy(self.data)))

    def require(self, name):
        self.assertTrue(callable(getattr(pipeline, name, None)), f'缺少 {name}')

    def camera(self, aid, sid='S4'):
        from console import scene_plan
        file = f'assets/{aid}.png'
        (self.directory / file).write_bytes(aid.encode())
        self.data['assets'].append({'id': aid, 'type': 'image', 'label': aid, 'roles': ['target_state'],
            'file': file, 'camera_scene': sid, 'scene_camera_scene': sid,
            'scene_camera_binding': scene_plan.binding(self.data, sid, self.directory)})
        self.data['images'][aid] = {'id': aid, 'for_task': f'scene-camera:{sid}', 'state': 'completed', 'asset_id': aid}
        self.data['replacements']['scene_plan'].setdefault(sid, {})['camera_asset'] = aid

    def script(self, images):
        self.data['script'] = {'provider': 'openai', 'model': 'offline', 'dialogue_mode': 'keep',
            'tasks': [{'id': 'seg-04', 'kind': 'speaker', 'mode': 'video', 'source_start': 9, 'source_end': 12.3,
                'duration': 4, 'ratio': '9:16', 'image_ids': images, 'action': 'the father from @image1 stands',
                'dialogue': '', 'blank_captions': False}]}
        self.data['script']['replacements_fingerprint'] = guard.fingerprint(self.data, self.directory)

    @staticmethod
    def safety(step, messages, **kwargs):
        rows = json.loads(messages[-1]['content'])['tasks']
        return {'tasks': [{**{k: row[k] for k in ('id', 'action', 'story', 'direction')}, 'changes': []}
                           for row in rows]}, {'provider': 'mock', 'model': 'offline', 'attempts': []}

    def test_frame_capture_is_immediate_local_and_records_people(self):
        self.require('capture_scene_frame')
        frame = pipeline.capture_scene_frame(self.pid, 'S4')
        self.assertTrue(11.2 <= frame['at'] <= 11.7)
        self.assertEqual(frame['characters'], ['C1', 'C2'])
        self.assertIn('父亲', frame['present_text'])
        self.assertIn('儿子', frame['present_text'])
        self.assertTrue((self.directory / frame['file']).is_file())
        self.assertEqual(self.reason.call_count, 0)
        self.assertEqual(self.extract.call_count, 1)
        self.assertEqual(store.load(self.pid)['replacements']['scene_plan']['S4']['frame_asset_id'], frame['asset_id'])

    def test_manual_frame_and_out_of_range(self):
        self.require('capture_scene_frame')
        frame = pipeline.capture_scene_frame(self.pid, 'S4', at=10.581)
        self.assertEqual(frame['at'], 10.58)
        for at in (8.9, 12.31, float('nan'), float('inf'), '无效'):
            with self.subTest(at=at), self.assertRaisesRegex(ValueError, '时间|秒数'):
                pipeline.capture_scene_frame(self.pid, 'S4', at=at)

    def test_multicast_draft_order_and_clear_roles(self):
        self.require('draft_scene_camera_image')
        draft = pipeline.draft_scene_camera_image(self.pid, 'openai', 'S4', at=11.4)
        self.assertEqual(draft['for_task'], 'scene-camera:S4')
        self.assertEqual(draft['image_ids'][:3], ['father', 'son', 'watch'])
        self.assertEqual(len(draft['image_ids']), 4)
        self.assertEqual(draft['image_ids'][-1], draft['frame_asset_id'])
        for phrase in ('图1', '父亲', '图2', '儿子', '图3', '商品', '图4', '站位', '没勾选', '不要多角度、分格、拼贴', '字幕、贴纸、水印'):
            self.assertIn(phrase, draft['prompt'])
        self.assertIsNone(draft.get('anchor_asset_id'))
        self.assertGreater(draft['frame_at'], 10.58)
        system = self.reason.call_args.args[1][0]['content']
        self.assertIn('每个', system)
        self.assertNotIn('写“the person from reference image 1”', system)

    def test_single_person_prompt_is_exactly_legacy(self):
        self.require('draft_scene_camera_image')
        self.data['replacements']['scene_plan']['S4'] = {'subjects': {'C2': {'on': False}}}
        self.persist()
        old = pipeline.draft_camera_image(self.pid, 'openai', 'C1', at=11.4, scene_id='S4')
        new = pipeline.draft_scene_camera_image(self.pid, 'openai', 'S4', at=11.4)
        self.assertEqual(new['prompt'], old['prompt'])
        self.assertEqual(new['image_ids'][:-1], old['image_ids'][:-1])

    def test_single_person_anchor_prompt_and_references_match(self):
        self.require('draft_scene_camera_image')
        self.data['replacements']['scene_plan']['S4'] = {'subjects': {'C2': {'on': False}}}
        aid = 'old-s1-camera'
        (self.directory / 'assets/old-camera.png').write_bytes(b'anchor')
        self.data['assets'].append({'id': aid, 'type': 'image', 'file': 'assets/old-camera.png',
                                   'label': '旧门廊图', 'roles': ['target_state'], 'camera_character': 'C1', 'camera_scene': 'S1'})
        # A legacy asset without character binding remains a valid historical selection.
        self.data['assets'][-1].pop('camera_character')
        self.data['replacements']['characters'][0]['camera_assets'] = {'S1': aid}
        self.data['images']['old'] = {'for_task': 'camera:C1:S1', 'state': 'completed', 'asset_id': aid}
        self.persist()
        old = pipeline.draft_camera_image(self.pid, 'openai', 'C1', at=11.4, scene_id='S4')
        new = pipeline.draft_scene_camera_image(self.pid, 'openai', 'S4', at=11.4)
        self.assertEqual(new['prompt'], old['prompt'])
        self.assertIn('身份锚点', new['prompt'])
        self.assertEqual(new['image_ids'][:-1], old['image_ids'][:-1])

    def test_product_only_and_nothing_selected(self):
        self.require('draft_scene_camera_image')
        self.data['replacements']['scene_plan']['S4'] = {'subjects': {'C1': {'on': False}, 'C2': {'on': False}}}
        self.persist()
        draft = pipeline.draft_scene_camera_image(self.pid, 'openai', 'S4')
        self.assertEqual(draft['image_ids'][:-1], ['watch'])
        self.assertIn('图1', draft['prompt'])
        self.assertIn('图2', draft['prompt'])
        self.assertIn('保持参考帧里的样子', draft['prompt'])
        self.data['replacements']['scene_plan']['S4']['subjects']['product'] = {'on': False}
        self.persist()
        draft = pipeline.draft_scene_camera_image(self.pid, 'openai', 'S4')
        self.assertEqual(draft['image_ids'], [draft['frame_asset_id']])

    def test_scene_override_and_unchecked_product_are_used_in_draft(self):
        self.require('draft_scene_camera_image')
        self.data['replacements']['scene_plan']['S4'] = {'subjects': {
            'C1': {'on': True, 'asset_id': 'other'}, 'C2': {'on': False}, 'product': {'on': False}}}
        self.persist()
        draft = pipeline.draft_scene_camera_image(self.pid, 'openai', 'S4')
        self.assertEqual(draft['image_ids'][:-1], ['other'])
        self.assertEqual(store.load(self.pid)['replacements']['characters'][0]['asset_id'], 'father')

    def test_full_project_view_is_derived_without_writing(self):
        with patch.object(runtime_env, 'apply'):
            import server
        before = (self.directory / 'project.json').read_bytes()
        view = server.project_view(store.load(self.pid))
        self.assertIn('scene_cast', view)
        self.assertEqual([s['key'] for s in view['scene_cast']['S1']['subjects']], ['C1'])
        self.assertEqual([s['key'] for s in view['scene_cast']['S4']['subjects']], ['C1', 'C2', 'product'])
        self.assertEqual((self.directory / 'project.json').read_bytes(), before)

    def test_scene_plan_save_and_restore_following(self):
        from console import scene_plan
        rep = copy.deepcopy(self.data['replacements'])
        rep['scene_plan']['S4'] = {'subjects': {'C1': {'on': True, 'asset_id': 'other'}, 'C2': {'on': False}}}
        pipeline.save_replacements(self.pid, rep)
        rep['characters'][0]['asset_id'] = 'son'
        saved = pipeline.save_replacements(self.pid, rep)
        rows = scene_plan.effective_subjects(saved, 'S4')
        self.assertEqual(rows[0]['asset_id'], 'other')
        self.assertFalse(rows[1]['on'])
        del rep['scene_plan']['S4']['subjects']['C1']['asset_id']
        saved = pipeline.save_replacements(self.pid, rep)
        self.assertEqual(scene_plan.effective_subjects(saved, 'S4')[0]['asset_id'], 'son')
        before = (self.directory / 'project.json').read_bytes()
        rep['scene_plan']['bogus'] = {}
        with self.assertRaisesRegex(ValueError, '场景'):
            pipeline.save_replacements(self.pid, rep)
        self.assertEqual((self.directory / 'project.json').read_bytes(), before)

    def test_default_video_images_scene_camera_then_effective_photos(self):
        self.require('draft_scene_camera_image')
        from console import scene_plan
        self.data['replacements']['scene_plan']['S4'] = {'subjects': {'C1': {'on': True, 'asset_id': 'other'}}}
        aid = 'new-scene-camera'
        (self.directory / 'assets/camera.png').write_bytes(b'new camera')
        self.data['assets'].append({'id': aid, 'type': 'image', 'label': '厨房主镜头图', 'roles': ['target_state'],
            'file': 'assets/camera.png', 'camera_scene': 'S4', 'scene_camera_scene': 'S4',
            'scene_camera_binding': scene_plan.binding(self.data, 'S4', self.directory)})
        self.data['images'][aid] = {'for_task': 'scene-camera:S4', 'state': 'completed', 'asset_id': aid}
        self.data['replacements']['scene_plan']['S4']['camera_asset'] = aid
        self.persist()
        task = {'id': 'seg-04', 'source_start': 9, 'source_end': 12.3, 'kind': 'speaker', 'mode': 'video'}
        self.assertEqual(pipeline.default_images(self.data, task), [aid, 'other', 'son', 'watch'])
        self.data['replacements']['scene_plan']['S4']['subjects']['C2'] = {'on': False}
        self.assertNotIn('son', pipeline.default_images(self.data, task))
        self.assertEqual([x['source_id'] for x in guard.replaced_people(self.data, task)], ['C1'])
        self.assertNotIn('C2', [x['source_id'] for x in guard.source_mappings(self.data, task)])

    def test_downstream_reference_overflow_is_explicit(self):
        self.require('draft_scene_camera_image')
        for i in range(3, 7):
            cid = f'C{i}'
            aid = f'extra-photo-{i}'
            self.data['assets'].append({'id': aid, 'type': 'image'})
            self.data['breakdown']['characters'].append({'id': cid, 'name': cid})
            self.data['breakdown']['shots'][-1]['characters'].append(cid)
            self.data['replacements']['characters'].append({'source_id': cid, 'action': 'replace', 'asset_id': aid})
        with self.assertRaisesRegex(ValueError, '4 张|最多 4|超过.*4'):
            pipeline.default_images(self.data, {'source_start': 10.6, 'source_end': 12.3, 'mode': 'video'})

    def test_real_local_ffmpeg_capture_uses_only_self_made_source(self):
        self.require('capture_scene_frame')
        source = self.directory / self.data['source']['file']
        with patch.dict(os.environ, {'PATH': str(config.ROOT / 'runtime/ffmpeg') + os.pathsep + os.environ.get('PATH', '')}):
            media._run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=red:s=32x48:r=2:d=12.5',
                        '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)])
            with patch.object(media, 'extract_frame', wraps=EXTRACT_FRAME) as extract:
                frame = pipeline.capture_scene_frame(self.pid, 'S4')
                self.assertTrue(11.2 <= frame['at'] <= 11.7)
                self.assertEqual((self.directory / frame['file']).read_bytes()[:2], b'\xff\xd8')
                self.assertEqual(extract.call_count, 1)
        self.assertEqual(self.reason.call_count, 0)

    def test_new_http_endpoints_use_project_lock_and_never_models_for_frame(self):
        self.require('capture_scene_frame')
        with patch.object(runtime_env, 'apply'):
            import server
        handler = object.__new__(server.Handler)
        handler.headers = {'X-Client-Build': 'offline'}
        self.enterContext(patch.object(server, 'build_status', return_value={
            'build': 'offline', 'server_build': 'offline', 'web_build': 'offline', 'restart_pending': False}))
        handler._body_json = lambda: {'scene_id': 'S4'}
        frame = handler._api('POST', ['projects', self.pid, 'scene-camera', 'frame'], {})
        self.assertTrue(11.2 <= frame['at'] <= 11.7)
        self.assertTrue(frame['asset_id'])
        self.assertEqual(self.reason.call_count, 0)
        handler._body_json = lambda: {'scene_id': 'S4', 'at': 11.2, 'provider': 'openai'}
        draft = handler._api('POST', ['projects', self.pid, 'scene-camera', 'draft'], {})
        self.assertEqual(draft['for_task'], 'scene-camera:S4')

    def test_scene_segments_remove_unchecked_global_photos(self):
        self.require('draft_scene_camera_image')
        self.data['replacements']['scene_plan']['S4'] = {'subjects': {'C2': {'on': False}, 'product': {'on': False}}}
        segments = pipeline._scene_segments(self.data, [{'sourceStart': 9, 'sourceEnd': 12.3, 'kind': 'speaker',
            'mode': 'video', 'imageIds': ['father', 'son', 'watch']}])
        self.assertEqual(segments[0]['imageIds'], ['father'])

    def test_scene_segments_never_reassign_unchecked_person_reference(self):
        self.data['replacements']['scene_plan']['S4'] = {'subjects': {'C2': {'on': False}}}
        self.camera('scene-main')
        segments = pipeline._scene_segments(self.data, [{'sourceStart': 9, 'sourceEnd': 12.3, 'kind': 'speaker',
            'mode': 'video', 'imageIds': ['father', 'son', 'watch'],
            'action': 'the son from @image2 stands next to father from @image1'}])
        segment = segments[0]
        self.assertEqual(segment['imageIds'], ['scene-main', 'father', 'watch'])
        self.assertNotIn('son from @image2', segment['action'])
        self.assertIn('original C2', segment['action'])

    def test_legacy_without_plan_retains_saved_data_and_default_references(self):
        del self.data['replacements']['scene_plan']
        old = copy.deepcopy(self.data)
        self.persist()
        self.assertEqual(pipeline.default_images(self.data, {'source_start': 0, 'source_end': 4.8, 'mode': 'video'}),
                         ['father', 'watch'])
        self.assertEqual(self.data, old)
        self.assertNotIn('分场设定', pipeline.build_replacements(self.data))

    def test_switch_active_scene_camera_syncs_image1(self):
        self.require('use_scene_camera_image')
        self.camera('first')
        self.camera('second')
        self.script(['second', 'father', 'son', 'watch'])
        self.persist()
        with patch.object(gateway, 'reason_checked', side_effect=self.safety):
            saved = pipeline.use_scene_camera_image(self.pid, 'S4', 'first')
        self.assertEqual(saved['replacements']['scene_plan']['S4']['camera_asset'], 'first')
        self.assertEqual(saved['script']['tasks'][0]['image_ids'], ['first', 'father', 'son', 'watch'])
        self.assertEqual(saved['camera_selection']['synced_tasks'], ['seg-04'])

    def test_delete_scene_camera_falls_back_and_syncs(self):
        self.camera('first')
        self.camera('second')
        self.script(['second', 'father', 'son', 'watch'])
        self.persist()
        with patch.object(gateway, 'reason_checked', side_effect=self.safety):
            saved = pipeline.delete_asset(self.pid, 'second')
        self.assertEqual(saved['script']['tasks'][0]['image_ids'][0], 'first')
        self.assertEqual(saved['replacements']['scene_plan']['S4']['camera_asset'], 'first')
        self.assertIn('已删除', saved['camera_notice']['scene:S4'])

    def test_scene_selection_failure_rolls_back_active_camera(self):
        self.camera('first')
        self.camera('second')
        self.script(['second', 'father', 'son', 'watch'])
        self.persist()
        with patch.object(pipeline, 'follow_scene_camera_selection', side_effect=ValueError('同步暂时失败')):
            with self.assertRaisesRegex(ValueError, '同步暂时失败'):
                pipeline.use_scene_camera_image(self.pid, 'S4', 'first')
        saved = store.load(self.pid)
        self.assertEqual(saved['replacements']['scene_plan']['S4']['camera_asset'], 'second')
        self.assertEqual(saved['script']['tasks'][0]['image_ids'][0], 'second')

    def test_settings_save_cannot_overwrite_newly_selected_scene_camera(self):
        self.camera('first')
        stale_form = copy.deepcopy(self.data['replacements'])
        self.camera('second')
        self.persist()
        stale_form['characters'][1]['description'] = '儿子的补充说明'
        saved = pipeline.save_replacements(self.pid, stale_form)
        self.assertEqual(saved['replacements']['scene_plan']['S4']['camera_asset'], 'second')
        self.assertEqual(saved['replacements']['characters'][1]['description'], '儿子的补充说明')

    def test_settings_save_cannot_overwrite_newly_captured_frame(self):
        pipeline.capture_scene_frame(self.pid, 'S4', 9.5)
        stale_form = copy.deepcopy(store.load(self.pid)['replacements'])
        latest = pipeline.capture_scene_frame(self.pid, 'S4', 11.4)
        saved = pipeline.save_replacements(self.pid, stale_form)
        row = saved['replacements']['scene_plan']['S4']
        self.assertEqual(row['frame_at'], 11.4)
        self.assertEqual(row['frame_asset_id'], latest['asset_id'])

    def test_camera_for_removed_scene_is_stale_without_breaking_read_view(self):
        self.camera('old-kitchen')
        self.data['breakdown']['scene'] = [s for s in self.data['breakdown']['scene'] if s['id'] != 'S4']
        with patch.object(runtime_env, 'apply'):
            import server
        view = server.project_view(copy.deepcopy(self.data))
        self.assertIn('old-kitchen', view['camera_stale_assets'])
        self.assertNotIn('S4', view['scene_cast'])

    def test_settings_can_be_saved_after_old_scene_was_removed(self):
        self.data['replacements']['scene_plan']['S4'] = {'frame_at': 11.4}
        self.data['breakdown']['scene'] = [s for s in self.data['breakdown']['scene'] if s['id'] != 'S4']
        self.persist()
        form = copy.deepcopy(self.data['replacements'])
        form['scene_plan'].pop('S4')
        form['characters'][0]['description'] = '重新拆解后的说明'
        saved = pipeline.save_replacements(self.pid, form)
        self.assertNotIn('S4', saved['replacements']['scene_plan'])
        self.assertEqual(saved['replacements']['characters'][0]['description'], '重新拆解后的说明')

    def test_absent_legacy_camera_is_not_preserved_as_supplementary_reference(self):
        self.data['replacements']['characters'][1].update(action='keep', asset_id=None)
        self.camera('scene-main', 'S1')
        self.data['assets'].append({'id': 'absent-son-camera', 'type': 'image', 'roles': ['target_state'],
                                   'file': 'assets/absent.png'})
        self.data['images']['absent'] = {'state': 'completed', 'asset_id': 'absent-son-camera',
                                       'for_task': 'camera:C2:S1'}
        segments = pipeline._scene_segments(self.data, [{'sourceStart': 0, 'sourceEnd': 4.8, 'kind': 'speaker',
            'mode': 'video', 'imageIds': ['father', 'absent-son-camera']}])
        self.assertEqual(segments[0]['imageIds'], ['scene-main', 'father'])

    def test_backend_and_editor_choose_same_fallback_when_camera_file_is_missing(self):
        self.camera('first')
        self.camera('second')
        self.persist()
        (self.directory / 'assets/second.png').unlink()
        with patch.object(runtime_env, 'apply'):
            import server
        view = server.project_view(copy.deepcopy(self.data))
        self.assertEqual(view['scene_cast']['S4']['camera_asset'], 'first')
        task = {'source_start': 9, 'source_end': 12.3, 'mode': 'video'}
        self.assertEqual(pipeline.default_images(self.data, task)[0], 'first')


if __name__ == '__main__':
    from tools.run_tests_offline import audit
    import sys
    sys.addaudithook(audit)
    unittest.main()
