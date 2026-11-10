"""24.18: synthetic four-scene projects only; transports and models mocked."""
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import gateway, pipeline, replacement_guard as guard, runtime_env, scene_plan, store
from tools import test_round24_17 as fixture


class Round24_18Tests(unittest.TestCase):
    setUp = fixture.Round24_17Tests.setUp
    frame = staticmethod(fixture.Round24_17Tests.frame)
    persist = fixture.Round24_17Tests.persist
    camera = fixture.Round24_17Tests.camera
    script = fixture.Round24_17Tests.script

    def edit(self, action, sid, next_sid=None):
        self.assertTrue(callable(getattr(pipeline, 'edit_scenes', None)), '缺少带锁的场景编辑接口')
        self.data = pipeline.edit_scenes(self.pid, action, sid, next_sid)
        return self.data

    def test_old_float_binding_is_current_after_read(self):
        # Historical pre-normalization digests use the generated image's own frame.
        self.data['breakdown']['shots'][1]['product'] = '无'
        self.data['breakdown']['productMoments'][0]['start'] = 5.7
        self.data['replacements']['scene_plan']['S2'] = {'frame_at': 5.749999999999999, 'frame_mode': 'auto'}
        self.camera('old-float', 'S2')
        self.data['assets'][-1].update(frame_at=5.749999999999999,
            scene_camera_binding=scene_plan._legacy_binding(self.data, 'S2', self.directory, _old_at=5.749999999999999))
        self.persist()
        before = (self.directory/'project.json').read_bytes()
        self.assertTrue(scene_plan.camera_valid(self.data, 'old-float', 'S2', self.directory))
        self.assertNotIn('old-float', guard.camera_stale_assets(self.data, self.directory))
        self.assertEqual(scene_plan.scene_cast(self.data, self.directory)['S2']['camera_asset'], 'old-float')
        one = scene_plan.binding(self.data, 'S2', self.directory)
        self.data['replacements']['scene_plan']['S2']['frame_at'] = 5.75
        self.assertEqual(one, scene_plan.binding(self.data, 'S2', self.directory))
        self.assertEqual((self.directory/'project.json').read_bytes(), before)

    def test_stale_selected_camera_remains_displayed_and_in_history(self):
        self.camera('old-current', 'S2')
        self.data['replacements']['scene_plan']['S2']['subjects'] = {'C1': {'on': False}}
        row = scene_plan.scene_cast(self.data, self.directory)['S2']
        self.assertIsNone(scene_plan.camera_image_for(self.data, 'S2', self.directory))
        self.assertEqual(row.get('display_camera_asset'), 'old-current')
        self.assertIn('old-current', row['camera_candidates'])
        self.assertIn('old-current', guard.camera_stale_assets(self.data, self.directory))

    def test_merge_and_split_restore_settings_and_keep_all_images(self):
        plans = self.data['replacements']['scene_plan']
        plans['S2'] = {'subjects': {'C1': {'on': False, 'asset_id': 'other'}}, 'frame_at': 5.2}
        plans['S3'] = {'subjects': {'C1': {'on': True}, 'product': {'on': False}}, 'added': ['C2']}
        self.camera('s2-old', 'S2'); self.camera('s3-old', 'S3')
        before = copy.deepcopy(plans); breakdown = copy.deepcopy(self.data['breakdown']); self.persist()
        self.edit('merge', 'S2', 'S3')
        scenes = guard.scenes(self.data)
        self.assertEqual([s['id'] for s in scenes], ['S1', 'S2', 'S4'])
        self.assertEqual(scenes[1]['name'], '室内产品展示 + 佩戴与手机演示')
        self.assertEqual(scenes[1]['timeRanges'], [[4.8, 9.0]])
        self.assertEqual(scenes[1]['merged_from'], ['S2', 'S3'])
        rows = {s['key']: s for s in scene_plan.effective_subjects(self.data, 'S2')}
        self.assertEqual(set(rows), {'C1', 'C2', 'product'})
        self.assertFalse(rows['C1']['on']); self.assertEqual(rows['C1']['asset_id'], 'other')
        self.assertTrue(rows['product']['on']); self.assertTrue(rows['C2']['on'])
        self.assertIsNone(scene_plan.camera_image_for(self.data, 'S2', self.directory))
        self.assertNotIn('frame_at', self.data['replacements']['scene_plan']['S2'])
        self.assertIsNone(scene_plan.scene_cast(self.data, self.directory)['S2']['display_camera_asset'])
        self.assertNotIn('s2-old',scene_plan.scene_cast(self.data,self.directory)['S2']['camera_candidates'])
        self.camera('merged-new', 'S2'); self.persist()
        task = {'source_start': 6, 'source_end': 8, 'mode': 'video'}
        self.assertEqual(guard.task_scene(self.data, task), 'S2')
        self.assertEqual(pipeline.default_images(self.data, task)[0], 'merged-new')
        self.edit('split', 'S2')
        self.assertEqual(self.data['replacements']['scene_plan'], before)
        self.assertEqual(scene_plan.camera_image_for(self.data, 'S2', self.directory), 's2-old')
        self.assertEqual(scene_plan.camera_image_for(self.data, 'S3', self.directory), 's3-old')
        self.assertFalse(scene_plan.camera_valid(self.data, 'merged-new', 'S2', self.directory))
        self.assertNotIn('merged-new',scene_plan.scene_cast(self.data,self.directory)['S2']['camera_candidates'])
        self.assertEqual(self.data['breakdown'], breakdown)
        self.assertTrue({'s2-old','s3-old','merged-new'} <= {a['id'] for a in self.data['assets']})

    def test_continuous_merge_and_original_plan_recovery(self):
        self.data['replacements']['scene_plan']['S3'] = {'subjects': {'C1': {'on': False}}}
        original = copy.deepcopy(self.data['replacements']['scene_plan']); self.persist()
        self.edit('merge', 'S2', 'S3'); self.edit('merge', 'S2', 'S4')
        self.assertEqual(guard.scenes(self.data)[1]['merged_from'], ['S2','S3','S4'])
        self.edit('split', 'S2')
        self.assertEqual(self.data['replacements']['scene_plan'], original)

    def test_merge_requires_next_adjacent_scene_and_rejects_gaps(self):
        for sid, nxt in [('S1','S3'),('S3','S2'),('S4','S1')]:
            with self.subTest(sid=sid), self.assertRaisesRegex(ValueError, '相邻|下一场'):
                self.edit('merge', sid, nxt)
        self.data['breakdown']['scene'][2]['timeRanges'] = [[6,9]]; self.persist()
        with self.assertRaisesRegex(ValueError, '相邻|紧挨'):
            self.edit('merge','S2','S3')

    def test_drop_restore_preserves_camera_and_prevents_dropping_all(self):
        self.camera('porch', 'S1'); self.persist()
        self.edit('drop','S1')
        self.assertEqual([s['id'] for s in guard.scenes(self.data)], ['S2','S3','S4'])
        self.assertNotIn('S1', scene_plan.scene_cast(self.data, self.directory))
        self.edit('drop','S2'); self.edit('drop','S3')
        before = (self.directory/'project.json').read_bytes()
        with self.assertRaisesRegex(ValueError, '至少要保留一场'):
            self.edit('drop','S4')
        self.assertEqual((self.directory/'project.json').read_bytes(), before)
        self.edit('restore','S1')
        self.assertEqual(scene_plan.camera_image_for(self.data, 'S1', self.directory), 'porch')

    def test_reanalysis_ignores_missing_merged_ids_and_drops_without_mutation(self):
        self.edit('merge','S2','S3'); self.edit('drop','S1')
        self.data['breakdown']['scene'] = [s for s in self.data['breakdown']['scene'] if s['id'] not in {'S1','S3'}]
        original = copy.deepcopy(self.data)
        self.assertEqual([s['id'] for s in guard.scenes(self.data)], ['S2','S4'])
        self.assertNotIn('merged_from', guard.scenes(self.data)[0])
        self.assertEqual(guard.scenes(self.data)[0]['name'], '室内产品展示')
        self.assertEqual(self.data, original)

    def test_script_prompt_clipping_and_merged_scene_segments(self):
        self.edit('merge','S2','S3'); self.edit('drop','S1')
        raw = [{'sourceStart':a,'sourceEnd':b,'kind':'broll','imageIds':[], 'story':'source scene'}
               for a,b in [(0,3),(4,5.2),(4.2,6.2),(5.8,9)]]
        messages = []
        def model(step, msgs, **kw):
            messages.extend(msgs)
            return {'segments': raw}, {'model':'mock'}
        with patch.object(gateway,'reason',side_effect=model), patch.object(pipeline,'apply_replacement_safety'), patch.object(pipeline,'_attach_motion_evidence'):
            pipeline.run_script(self.pid,'openai','keep','',None)
        content = messages[-1]['content']
        self.assertIn('以下原片时间段不复刻，成片里不出现：0.00–4.80 秒（S1 住宅门廊）', content)
        tasks = store.load(self.pid)['script']['tasks']
        self.assertTrue(tasks)
        self.assertTrue(all(t['source_start'] >= 4.8 for t in tasks))
        self.assertTrue(all(t['source_end']-t['source_start'] >= 1 for t in tasks))
        self.assertEqual({t['scene_id'] for t in tasks}, {'S2', 'S4'})
        missing = next(t for t in tasks if t['scene_id'] == 'S4')
        self.assertEqual((missing['source_start'], missing['source_end'], missing['split_reason']),
                         (9, 12.3, '补齐：模型漏掉了这一场'))
        direct = pipeline._scene_segments(self.data,[{'sourceStart':4,'sourceEnd':5.2,'kind':'broll','imageIds':[]},
            {'sourceStart':4.2,'sourceEnd':6.2,'kind':'broll','imageIds':[]}])
        self.assertEqual([(s['sourceStart'],s['sourceEnd']) for s in direct], [(4.8,5.2), (4.8,6.2)])
        single = pipeline._scene_segments(self.data,[{'sourceStart':4.8,'sourceEnd':9,'kind':'broll','imageIds':[]}])
        self.assertEqual(len(single),1); self.assertEqual(single[0]['scene_id'],'S2')

    def test_middle_drop_splits_across_hole_and_clips_dialogue(self):
        self.edit('drop','S2')
        parts = pipeline._scene_segments(self.data,[{'sourceStart':0,'sourceEnd':9,'kind':'broll','imageIds':[], 'dialogue':'a b c d e f g h i'}])
        self.assertEqual([(p['sourceStart'],p['sourceEnd']) for p in parts],[(0,4.8),(5.8,9)])
        self.assertNotEqual(parts[0]['dialogue'],parts[1]['dialogue'])

    def test_edits_stale_script_and_legacy_fingerprint_unchanged(self):
        self.script([]); before = guard.fingerprint(self.data,self.directory); self.persist()
        self.edit('drop','S1')
        self.assertNotEqual(guard.fingerprint(self.data,self.directory),before)
        task=self.data['script']['tasks'][0]
        self.assertIn('script_stale',{i['code'] for i in guard.check(self.data,task,self.directory)['issues']})
        legacy = copy.deepcopy(self.data); legacy['replacements'].pop('scene_edits'); legacy['replacements'].pop('scene_plan')
        old = guard.fingerprint(legacy,self.directory)
        legacy['replacements']['scene_edits']={'merges':[],'dropped':[]}
        self.assertEqual(old,guard.fingerprint(legacy,self.directory))

    def test_stale_form_cannot_undo_edit_and_keeps_hidden_scene_settings(self):
        stale=copy.deepcopy(self.data['replacements'])
        self.edit('merge','S2','S3'); self.edit('drop','S1')
        stale['scene_plan']['S2']={'subjects':{'C1':{'on':False}}}
        saved=pipeline.save_replacements(self.pid,stale)
        self.assertEqual(saved['replacements']['scene_edits'],self.data['replacements']['scene_edits'])
        self.assertEqual([s['id'] for s in guard.scenes(saved)],['S2','S4'])

    def test_stale_script_cannot_be_assembled(self):
        from console import jimmy_bridge
        self.script([]); self.persist(); self.edit('drop','S1')
        with patch.object(jimmy_bridge.media,'concat') as concat, self.assertRaisesRegex(jimmy_bridge.BridgeError,'重新生成新脚本'):
            jimmy_bridge.assemble(self.pid)
        concat.assert_not_called()

    def test_default_checkbox_conflict_uses_earlier_scene(self):
        self.data['replacements']['scene_plan']['S3']={'subjects':{'C1':{'on':False}}}; self.persist()
        self.edit('merge','S2','S3')
        self.assertTrue(next(s for s in scene_plan.effective_subjects(self.data,'S2') if s['key']=='C1')['on'])

    def test_continuous_merge_archives_intermediate_camera_on_split(self):
        self.camera('original-s2','S2'); self.camera('original-s4','S4'); self.persist()
        self.edit('merge','S2','S3'); self.camera('first-merge','S2'); self.persist()
        self.edit('merge','S2','S4'); self.edit('split','S2')
        self.assertNotIn('first-merge',scene_plan.scene_cast(self.data,self.directory)['S2']['camera_candidates'])
        self.assertEqual(scene_plan.camera_image_for(self.data,'S2',self.directory),'original-s2')
        self.assertEqual(scene_plan.camera_image_for(self.data,'S4',self.directory),'original-s4')

    def test_merged_manual_frame_can_be_saved_in_extended_range(self):
        self.edit('merge','S2','S3')
        captured=pipeline.capture_scene_frame(self.pid,'S2',7)
        rep=copy.deepcopy(store.load(self.pid)['replacements'])
        saved=pipeline.save_replacements(self.pid,rep)
        self.assertEqual(saved['replacements']['scene_plan']['S2']['frame_asset_id'],captured['asset_id'])

    def test_ignored_merge_does_not_hide_surviving_original_camera(self):
        self.camera('surviving','S2'); self.persist(); self.edit('merge','S2','S3')
        self.data['breakdown']['scene']=[s for s in self.data['breakdown']['scene'] if s['id']!='S3']
        self.assertEqual(scene_plan.camera_image_for(self.data,'S2',self.directory),'surviving')
        row=scene_plan.scene_cast(self.data,self.directory)['S2']
        self.assertEqual(row['camera_asset'],'surviving')

    def test_raw_model_dropped_content_is_removed_before_merge(self):
        self.edit('drop','S1')
        raw=[{'sourceStart':0,'sourceEnd':4.8,'kind':'broll','story':'DROPPED_ONLY'},
             {'sourceStart':4.8,'sourceEnd':9,'kind':'broll','story':'RETAINED_ONLY'}]
        self.reason.return_value=({'segments':raw},{'model':'mock'})
        with patch.object(pipeline,'apply_replacement_safety'),patch.object(pipeline,'_attach_motion_evidence'):
            pipeline.run_script(self.pid,'openai','keep','',None)
        script=store.load(self.pid)['script']
        self.assertNotIn('DROPPED_ONLY',json.dumps(script['tasks']))
        self.assertIn('RETAINED_ONLY',json.dumps(script['tasks']))

    def test_raw_script_short_clipped_segment_is_kept_in_its_own_scene(self):
        self.edit('drop','S1')
        raw=[{'sourceStart':4,'sourceEnd':5.2,'kind':'broll','story':'TOO_SHORT'},
             {'sourceStart':5.8,'sourceEnd':9,'kind':'broll','story':'VALID'}]
        self.reason.return_value=({'segments':raw},{'model':'mock'})
        with patch.object(pipeline,'apply_replacement_safety'),patch.object(pipeline,'_attach_motion_evidence'):
            pipeline.run_script(self.pid,'openai','keep','',None)
        tasks = {t['scene_id']: t for t in store.load(self.pid)['script']['tasks']}
        self.assertEqual((tasks['S2']['source_start'], tasks['S2']['source_end'], tasks['S2']['story']),
                         (4.8, 5.8, 'TOO_SHORT'))
        self.assertEqual(tasks['S3']['story'], 'VALID')
        self.assertNotIn('TOO_SHORT', tasks['S3']['story'])

    def test_dropped_old_script_segment_reports_stale_instead_of_missing_scene_error(self):
        self.script([])
        self.data['script']['tasks'][0].update(source_start=0,source_end=4.8,scene_id='S1')
        self.persist(); self.edit('drop','S1')
        check=guard.check(self.data,self.data['script']['tasks'][0],self.directory)
        self.assertIn('script_stale',{i['code'] for i in check['issues']})
        self.assertIn(guard.STALE_SCRIPT,check['errors'])

    def test_invalidated_merge_settings_can_be_edited_after_reanalysis(self):
        self.edit('merge','S2','S3')
        self.data['breakdown']['scene']=[s for s in self.data['breakdown']['scene'] if s['id']!='S3']; self.persist()
        rep=copy.deepcopy(self.data['replacements']);rep['scene_plan']['S2']={'subjects':{'C1':{'on':False}}}
        saved=pipeline.save_replacements(self.pid,rep)
        self.assertFalse(next(s for s in scene_plan.effective_subjects(saved,'S2') if s['key']=='C1')['on'])

    def test_pending_merged_image_finishing_after_split_stays_in_other_images(self):
        self.edit('merge','S2','S3')
        pending_binding=scene_plan.binding(self.data,'S2',self.directory)
        self.data['images']['pending']={'id':'pending','state':'running','for_task':'scene-camera:S2','scene_camera_binding':pending_binding}
        self.persist(); self.edit('split','S2')
        (self.directory/'assets/pending.png').write_bytes(b'offline-pending')
        self.data['assets'].append({'id':'pending','type':'image','file':'assets/pending.png','scene_camera_scene':'S2','scene_camera_binding':pending_binding})
        self.data['images']['pending'].update(state='completed',asset_id='pending')
        self.assertNotIn('pending',scene_plan.scene_cast(self.data,self.directory)['S2']['camera_candidates'])
        self.assertFalse(scene_plan.camera_valid(self.data,'pending','S2',self.directory))

    def test_read_view_ignores_invalid_merge_settings_without_writing_project(self):
        self.data['replacements']['scene_plan']['S3']={'subjects':{'product':{'on':False}}}; self.persist()
        self.edit('merge','S2','S3')
        self.data['breakdown']['scene']=[s for s in self.data['breakdown']['scene'] if s['id']!='S3']; self.persist()
        before=(self.directory/'project.json').read_bytes()
        with patch.object(runtime_env,'apply'):
            import server
        view=server.project_view(store.load(self.pid))
        self.assertNotIn('product',(view['replacements']['scene_plan'].get('S2') or {}).get('subjects',{}))
        self.assertNotIn('merged_from',view['effective_scenes'][1])
        self.assertEqual((self.directory/'project.json').read_bytes(),before)

    def test_http_actions_hold_project_lock_and_return_latest_project(self):
        with patch.object(runtime_env,'apply'):
            import server
        handler=object.__new__(server.Handler);handler.headers={'X-Client-Build':'offline'}
        with patch.object(server,'build_status',return_value={'build':'offline','server_build':'offline','web_build':'offline','restart_pending':False}):
            actual=store.update
            def locked(pid,fn):
                self.assertTrue(store._lock(pid)._is_owned())
                return actual(pid,fn)
            for action,sid,nxt in [('merge','S2','S3'),('split','S2',None),('drop','S1',None),('restore','S1',None)]:
                handler._body_json=lambda:{'action':action,'scene_id':sid,'next_scene_id':nxt}
                with patch.object(store,'update',side_effect=locked):
                    result=handler._api('POST',['projects',self.pid,'scene-edits'],{})
                self.assertEqual(result['id'],self.pid)
        self.assertEqual(self.reason.call_count,0)


if __name__ == '__main__':
    unittest.main()
