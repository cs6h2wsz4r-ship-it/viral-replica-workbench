"""Round 18 regressions: local copies only, no provider/media calls."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.project_fixture import fixture_path
from console import config, llm, media, pipeline, replacement_guard as guard, store


class PipelineRound18Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.tmp))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('禁止读取配置')))
        # Gateway routing reads explicit offline settings; credential parsing stays forbidden.
        self.enterContext(patch.object(config, "load", return_value=dict(config.DEFAULTS)))
        self.enterContext(patch.object(llm, 'chat_json', side_effect=AssertionError('必须 mock 模型')))
        self.enterContext(patch.object(llm, 'chat_json_checked', side_effect=AssertionError('必须 mock 模型')))
        # These fixtures intentionally contain non-image bytes. Keep all local media
        # mocked as before; round22 separately exercises real FFmpeg and residue checks.
        def delogo(path, masks, out):
            out.write_bytes(b'offline cleaned frame')
            return out
        self.enterContext(patch.object(media, 'delogo_frame', side_effect=delogo))
        self.enterContext(patch.object(media, 'caption_residue', return_value={'status':'checked','suspected':[]}))
        self.pid = store.create('round18')['id']; self.directory = store.project_dir(self.pid)
        raw = fixture_path('p1028222359-9b21').read_bytes()
        (self.directory / 'fixture.json').write_bytes(raw)
        self.data = json.loads((self.directory / 'fixture.json').read_text())
        self.original_id = self.data['id']
        self.data['id'] = self.pid
        # A selected route is separate from the recorded script provenance.
        self.data['routes'] = {'script': {'channel': self.data['script']['provider'],
                                         'model': self.data['script']['request_model']}}
        # Preserve recorded digests/bindings: fixture bytes are not production media.
        for a in self.data['assets']:
            p = self.directory / a['file']; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'offline'); a['sha256'] = store.sha256_file(p)
        self.camera = 'img1029085721-2d86'
        self.portrait = self.data['replacements']['characters'][0]['asset_id']
        self.product = self.data['replacements']['product']['asset_id']
        for a in self.data['assets']:
            if a.get('camera_character'):
                a['camera_binding'] = guard.camera_binding(self.data,a['camera_character'],self.directory,scene_id=a.get('camera_scene'))
        store.update(self.pid, lambda d: d.update(self.data))

    def test_deleted_camera_falls_back_and_default_has_identity(self):
        self.assertEqual(guard.camera_image_for(self.data, 'C1', 'S1'), self.camera)
        task = dict(self.data['script']['tasks'][0], mode='video')
        self.assertEqual(pipeline.default_images(self.data, task), [self.camera, self.portrait, self.product])
        self.data['assets'] = [a for a in self.data['assets'] if a['id'] == self.product]
        self.assertEqual(pipeline.default_images(self.data, task), [self.product])

    def test_missing_file_and_stale_camera_are_rejected(self):
        asset = next(a for a in self.data['assets'] if a['id'] == self.camera)
        (self.directory / asset['file']).unlink()
        self.assertIsNone(guard.camera_image_for(self.data, 'C1', 'S1'))
        (self.directory / asset['file']).write_bytes(b'offline')
        portrait = next(a for a in self.data['assets'] if a['id'] == self.portrait)
        (self.directory / portrait['file']).write_bytes(b'changed identity')
        self.assertIsNone(guard.camera_image_for(self.data, 'C1', 'S1'))
        (self.directory / portrait['file']).write_bytes(b'offline')
        self.data['replacements']['characters'][0]['camera_assets']['S1'] = None
        self.assertEqual(guard.camera_image_for(self.data, 'C1', 'S1'), self.camera)
        asset['camera_binding'] = 'expired'
        self.assertIsNone(guard.camera_image_for(self.data, 'C1', 'S1'))

    def test_deleted_image1_is_one_repair(self):
        checked = guard.check(self.data, self.data['script']['tasks'][0])
        self.assertEqual(len(checked['issues']), 1, checked['issues'])
        issue = checked['issues'][0]
        self.assertEqual(issue['title'], 'seg-01 的图1 指向一张已删除或过期的主镜头图')
        self.assertEqual(issue['expected_asset_id'], self.camera)
        ui = copy.deepcopy(self.data)
        ui['camera_current'] = {c['source_id']: {scene['id']: guard.camera_image_for(self.data,c['source_id'],scene['id'])
            for scene in guard.scenes(self.data)} for c in self.data['replacements']['characters']}
        ui['camera_stale_assets'] = guard.camera_stale_assets(self.data,self.directory)
        ui['camera_stale_images'] = guard.camera_stale_images(self.data,self.directory)
        ui['missing_asset_files'] = []
        ui['checks'] = ui['replacement_checks'] = {t['id']: guard.check(self.data,t,self.directory) for t in self.data['script']['tasks']}
        ui['id'] = self.original_id
        (ROOT / 'runtime/temp/round18-project.json').write_text(json.dumps(ui,ensure_ascii=False,indent=2))

    def test_front_frame_and_internal_frame_reuse(self):
        def frame(src, at, out, **kw):
            out.write_bytes(b'frame'); return out
        with patch.object(media, 'extract_frame', side_effect=frame), patch.object(llm, 'image_part', return_value=[]), patch.object(llm, 'chat_json', return_value=({'person':'p','shot':'s','setting':'s'}, {})):
            draft = pipeline.draft_camera_image(self.pid, 'jimmy', 'C1')
            self.assertGreaterEqual(draft['frame_at'], 2.78)
            self.assertIn('正面', draft['frame_reason'])
            again = pipeline.draft_camera_image(self.pid, 'jimmy', 'C1', at=draft['frame_at'] + .001)
            third = pipeline.draft_camera_image(self.pid, 'jimmy', 'C1', at=draft['frame_at'])
            same = [a for a in store.load(self.pid)['assets'] if a.get('frame_character') == 'C1']
            self.assertEqual(len(same), 1)
            adjacent = pipeline.draft_camera_image(self.pid, 'jimmy', 'C1', at=draft['frame_at'] - .01)
            self.assertNotEqual(adjacent['frame_asset_id'], draft['frame_asset_id'])
            self.assertEqual(third['frame_asset_id'], draft['frame_asset_id'])
        self.assertEqual(draft['frame_asset_id'], again['frame_asset_id'])
        asset = next(a for a in store.load(self.pid)['assets'] if a['id'] == draft['frame_asset_id'])
        self.assertTrue(asset['internal'])
        self.assertEqual(asset['frame_character'], 'C1')

    def test_legacy_camera_frame_reused_and_marked_internal(self):
        frame=self.directory/'assets/frame-C1-3.36.jpg'; frame.write_bytes(b'legacy frame')
        legacy={'id':'legacy-frame','type':'image','roles':['scene'],'camera_scene':'S1','frame_at':3.36,
                'file':'assets/frame-C1-3.36.jpg','label':'原片参考帧 穿裙女子 3.4s'}
        store.update(self.pid,lambda d:d['assets'].append(legacy))
        def extract(src,at,out,**kw):
            out.write_bytes(b'new frame'); return out
        with patch.object(media,'extract_frame',side_effect=extract) as extract_call, \
             patch.object(llm,'image_part',return_value=[]), \
             patch.object(llm,'chat_json',return_value=({'person':'p','shot':'s','setting':'s'},{})):
            draft=pipeline.draft_camera_image(self.pid,'jimmy','C1',at=3.36,scene_id='S1')
        self.assertEqual(draft['frame_asset_id'],'legacy-frame')
        extract_call.assert_not_called()
        saved=next(a for a in store.load(self.pid)['assets'] if a['id']=='legacy-frame')
        self.assertTrue(saved['internal']); self.assertEqual(saved['frame_character'],'C1')

    def test_offscreen_cannot_be_replaced(self):
        rep = copy.deepcopy(self.data['replacements'])
        rep['characters'][1].update(action='replace', asset_id=self.portrait)
        saved = pipeline.save_replacements(self.pid, rep)
        self.assertEqual(saved['replacements']['characters'][1]['action'], 'keep')
        self.assertEqual([c['source_id'] for c in guard.replaced_people(saved)], ['C1'])
        self.assertIsNone(guard.camera_image_for(saved, 'C2', 'S1'))

    def prepare_dialogue(self):
        task = self.data['script']['tasks'][0]
        task.update(source_start=0, source_end=4, dialogue='Hello there. I love this dress.', image_ids=[self.camera,self.portrait,self.product], action='', story='', direction='')
        transcript={'language':'en','segments':[{'start':0,'end':1,'text':'Hello there.'},{'start':1,'end':4,'text':'I love this dress.'}]}
        (self.directory / 'analysis/transcript.raw.json').write_text(json.dumps(transcript))
        store.update(self.pid,lambda d:d.update(self.data))
        return {'lines':[{'start':0,'end':1,'original':'Hello there.','proposed':'Hello there.','changed':False,'reason':''},{'start':1,'end':4,'original':'I love this dress.','proposed':'I love this look.','changed':True,'reason':'商品由长裙改为套装'}]}

    def test_dialogue_proposal_and_adoption_keep_motion_windows(self):
        result = self.prepare_dialogue()
        self.assertTrue(callable(getattr(pipeline,'dialogue_proposal',None)))
        with patch.object(llm,'chat_json_checked',return_value=(result,{'model':'mock-script','provider':'jimmy'})) as call:
            proposal=pipeline.dialogue_proposal(self.pid,'seg-01','只改冲突')
        self.assertEqual(proposal['model'],'mock-script')
        self.assertEqual(call.call_args.kwargs['model'],self.data['script']['request_model'])
        self.assertEqual(store.load(self.pid)['script']['tasks'][0]['dialogue_proposal'],proposal)
        with patch.object(pipeline,'apply_replacement_safety'):
            saved=pipeline.adopt_dialogue_proposal(self.pid,'seg-01')
        self.assertEqual(saved['script']['dialogue_mode'],'rewrite')
        t=saved['script']['tasks'][0]
        self.assertEqual([r['start'] for r in t['dialogue_windows']],[0,1])
        self.assertIn('look',t['dialogue'])
        self.assertIn('source_motion_timeline',t)
        saved=pipeline.keep_original_dialogue(self.pid,'seg-01')
        self.assertEqual(saved['script']['tasks'][0]['dialogue_choice'],'keep')
        self.assertIn('this dress',saved['script']['tasks'][0]['dialogue'])
        self.assertFalse(guard.dialogue_mismatches(saved,saved['script']['tasks'][0]))

    def test_adopted_quotes_keep_source_motion_and_survive_readoption(self):
        from console import motion, jimmy_bridge
        result=self.prepare_dialogue()
        action='Emphasize "I love this dress"; gentle warmth on "dress".'
        task=self.data['script']['tasks'][0]
        task['action']=action
        self.data['breakdown']['shots']=[{'id':'K1','start':0,'end':4,'characters':['C1'],
            'action':'C1 raises a hand on "I love this dress", then lowers it after "dress".'}]
        task['dialogue_proposal']={**result,'model':'mock'}
        store.update(self.pid,lambda d:d.update(self.data))
        with patch.object(pipeline,'apply_replacement_safety'):
            saved=pipeline.adopt_dialogue_proposal(self.pid,'seg-01')
            task=saved['script']['tasks'][0]
            self.assertEqual(task['action'],'Emphasize "I love this look"; gentle warmth on [1–4 s].')
            rows=motion.source_timeline(saved,task)
            self.assertEqual([(r['start'],r['end']) for r in rows],[(0,4)])
            self.assertIn('raises a hand on "I love this look", then lowers it after [1–4 s].',rows[0]['action'])
            self.assertEqual([(r['start'],r['end']) for r in task['dialogue_windows']],[(0,1),(1,4)])
            prompt=jimmy_bridge.compose_prompt(saved,task)
            self.assertNotIn('"I love this dress"',prompt)
            self.assertNotIn('"dress"',prompt)
            self.assertIn('raises a hand',prompt)
            self.assertFalse(motion.performance_errors(task))
            # Repeat adoption restores the recorded anchor text before applying new words.
            store.update(self.pid,lambda d:d['script']['tasks'][0].update(action=task['action']+' Quiet tone.'))
            next_lines=copy.deepcopy(result['lines']); next_lines[1]['proposed']='I love this top.'
            saved=pipeline.adopt_dialogue_proposal(self.pid,'seg-01',next_lines)
            task=saved['script']['tasks'][0]
            self.assertEqual(task['action'],'Emphasize "I love this top"; gentle warmth on [1–4 s]. Quiet tone.')
        saved=pipeline.keep_original_dialogue(self.pid,'seg-01')
        task=saved['script']['tasks'][0]
        self.assertEqual(task['action'],action+' Quiet tone.')
        self.assertIn('"I love this dress"',motion.source_timeline(saved,task)[0]['action'])

    def test_incident_five_line_proposal_handles_dress_flowy_open_back(self):
        from console import jimmy_bridge, motion
        task=self.data['script']['tasks'][0]
        originals=copy.deepcopy(task['dialogue_windows'])
        transcript={'language':'en','segments':[dict(row) for row in originals]}
        (self.directory/'analysis/transcript.raw.json').write_text(json.dumps(transcript))
        proposed=['Excuse me, your look is lovely.','Oh, thank you so much.',
                  'It feels nice.','Love it.','The fabric is perfect for this weather.']
        response={'lines':[{'start':row['start'],'end':row['end'],'original':row['text'],
                    'proposed':text,'changed':text!=row['text'],'reason':'替换商品冲突' if text!=row['text'] else ''}
                  for row,text in zip(originals,proposed)]}
        with patch.object(pipeline,'apply_replacement_safety'):
            pipeline.repair_camera_reference(self.pid,'seg-01','C1','S1')
            with patch.object(llm,'chat_json_checked',return_value=(response,{'model':'mock','provider':'jimmy'})):
                proposal=pipeline.dialogue_proposal(self.pid,'seg-01','只改商品冲突')
            self.assertEqual([r['changed'] for r in proposal['lines']],[True,False,True,True,False])
            saved=pipeline.adopt_dialogue_proposal(self.pid,'seg-01')
        task=saved['script']['tasks'][0]
        self.assertEqual([r['text'] for r in task['dialogue_windows']],proposed)
        self.assertEqual([(r['start'],r['end']) for r in task['dialogue_windows']],
                         [(r['start'],r['end']) for r in originals])
        self.assertIn("'thank you so much'",task['action'])
        self.assertIn('[5.201–6.56 s]',task['action'])
        self.assertIn('[6.76–7.578 s]',task['action'])
        prompt=jimmy_bridge.compose_prompt(saved,task)
        for old in ('your dress is beautiful','flowy','open back'):
            self.assertNotIn(old,prompt)
        self.assertIn('SOURCE MOTION TIMELINE',prompt)
        self.assertFalse(motion.performance_errors(task))
        self.assertFalse(motion.anchor_errors(task))

    def test_page_rewrite_without_replacements_still_prepares_reviewable_proposal(self):
        from console import motion
        proposal=self.prepare_dialogue()
        for line in proposal['lines']:
            line.update(proposed=line['original'],changed=False)
        store.update(self.pid,lambda d:d.update(replacements={}))
        segments=[{'sourceStart':0,'sourceEnd':4,'kind':'speaker','mode':'video','dialogue':'unapproved model wording'}]
        with patch.object(llm,'chat_json',return_value=({'segments':segments},{'model':'mock'})), \
             patch.object(llm,'chat_json_checked',return_value=(proposal,{'model':'mock','provider':'jimmy'})), \
             patch.object(pipeline,'apply_replacement_safety'),patch.object(motion,'cleanup_performance'):
            pipeline.run_script(self.pid,'jimmy','rewrite','',None,review_dialogue=True)
        saved=store.load(self.pid)['script']
        self.assertEqual(saved['dialogue_mode'],'keep')
        self.assertEqual(saved['tasks'][0]['dialogue'],'Hello there. I love this dress.')
        self.assertIn('dialogue_proposal',saved['tasks'][0])

    def test_new_script_references_and_rewrite_are_reviewable(self):
        from console import motion, jimmy_bridge
        proposal = self.prepare_dialogue()
        segments = [{'sourceStart':0,'sourceEnd':4,'kind':'speaker','mode':'video',
                     'imageIds':['deleted',self.product], 'dialogue':'model must not overwrite original'}]
        with patch.object(llm,'chat_json',return_value=({'segments':segments},{'model':'mock'})), \
             patch.object(llm,'chat_json_checked',return_value=(proposal,{'model':'mock','provider':'jimmy'})), \
             patch.object(pipeline,'apply_replacement_safety'), patch.object(motion,'cleanup_performance'), \
             patch.object(store,'sha256_file',side_effect=lambda path: next((a['sha256'] for a in self.data['assets'] if path.name==Path(a['file']).name),'0'*64)):
            pipeline.run_script(self.pid,'jimmy','rewrite','',None)
            saved=store.load(self.pid); task=saved['script']['tasks'][0]
            self.assertEqual(task['image_ids'],[self.camera,self.portrait,self.product])
            self.assertEqual(task['identity_reference_version'],1)
            self.assertEqual(task['dialogue'],'Hello there. I love this dress.')
            self.assertEqual(saved['script']['dialogue_mode'],'keep')
            self.assertIn('dialogue_proposal',task)
            self.assertFalse(guard.check(saved,task)['errors'])
            self.assertIn('identity reference of the same person',jimmy_bridge.compose_prompt(saved,task))

    def test_camera_repair_preserves_person_and_scene_semantics(self):
        self.assertTrue(callable(getattr(pipeline,'repair_camera_reference',None)))
        with patch.object(pipeline,'apply_replacement_safety'):
            saved=pipeline.repair_camera_reference(self.pid,'seg-01','C1','S1')
        task=saved['script']['tasks'][0]
        self.assertEqual(task['image_ids'],[self.camera,self.product])
        self.assertIn('person from @image1',task['action'])
        self.assertIn('lot from @image1',task['action'])
        self.assertFalse(task['reference_mapping_errors'])
        self.assertFalse(guard.check(saved,task,self.directory)['errors'])
        # Unrelated unknown references must still be visible to the guard.
        store.update(self.pid,lambda d:d['script']['tasks'][0].update(direction='Use an unknown reference @image8.'))
        with patch.object(pipeline,'apply_replacement_safety'):
            saved=pipeline.repair_camera_reference(self.pid,'seg-01','C1','S1')
        self.assertTrue(any(i['code']=='reference_mapping' for i in guard.check(saved,saved['script']['tasks'][0])['issues']))

    def test_camera_merge_retains_independent_missing_reference(self):
        task=self.data['script']['tasks'][0]
        task['image_ids'].append('independent-deleted-photo')
        checked=guard.check(self.data,task)
        self.assertEqual(len(checked['issues']),2,checked['issues'])
        independent=next(i for i in checked['issues'] if i['code']=='invalid_image')
        self.assertIn('independent-deleted-photo',' '.join(independent['details']))
        task['direction']='Unknown reference @image8.'
        self.assertIn('reference_mapping',[i['code'] for i in guard.check(self.data,task)['issues']])
        store.update(self.pid,lambda d:d.update(self.data))
        with patch.object(pipeline,'apply_replacement_safety'):
            saved=pipeline.repair_camera_reference(self.pid,'seg-01','C1','S1')
        checked=guard.check(saved,saved['script']['tasks'][0])
        self.assertIn('invalid_image',[i['code'] for i in checked['issues']])
        self.assertIn('reference_mapping',[i['code'] for i in checked['issues']])

    def test_proposal_preserves_actual_source_window_without_length_block(self):
        result=self.prepare_dialogue()
        source=pipeline._dialogue_source(self.data,self.data['script']['tasks'][0],self.directory)
        source[1]['end']=1.2; result['lines'][1]['end']=1.2
        checked=pipeline._validate_dialogue_proposal(self.data,self.data['script']['tasks'][0],source,result)
        self.assertNotIn('too_long',checked[1])
        self.assertEqual(checked[1]['end'],1.2)
        self.assertEqual(checked[1]['proposed'],result['lines'][1]['proposed'])

    def test_round18_baseline_legacy_previews_are_byte_identical(self):
        from tools.test_scene_camera import SceneCameraTests
        for pid in ('p1027232428-166a','p1027195039-ccc0'):
            with self.subTest(project=pid):
                local=self.directory / (pid+'.json')
                local.write_bytes(fixture_path(pid).read_bytes())
                original=json.loads(local.read_text())
                SceneCameraTests._assert_legacy_compatibility(self,original,'round18-compatibility-'+pid+'.json',baseline_round='round18')

    def test_dialogue_rejects_unrelated_language_and_accepts_length_changes(self):
        original=self.prepare_dialogue()
        self.assertTrue(callable(getattr(pipeline,'dialogue_proposal',None)))
        for index,text in [(0,'Howdy friend.'),(1,'我很喜欢这套衣服。'),(1,'I love this wonderful beautiful high quality premium outfit so very very much.')]:
            result=copy.deepcopy(original); result['lines'][index]['proposed']=text
            with self.subTest(text=text), patch.object(llm,'chat_json_checked',return_value=(result,{'model':'mock'})):
                if text.startswith('I love this wonderful'):
                    proposal=pipeline.dialogue_proposal(self.pid,'seg-01','')
                    self.assertNotIn('too_long',proposal['lines'][1])
                    self.assertEqual(store.load(self.pid)['script']['tasks'][0]['dialogue_proposal'],proposal)
                    with patch.object(pipeline,'apply_replacement_safety'):
                        saved=pipeline.adopt_dialogue_proposal(self.pid,'seg-01')
                    self.assertIn(text,saved['script']['tasks'][0]['dialogue'])
                elif index == 0:
                    proposal=pipeline.dialogue_proposal(self.pid,'seg-01','')
                    self.assertEqual(proposal['lines'][0]['proposed'],original['lines'][0]['original'])
                    self.assertTrue(proposal['lines'][0]['restored'])
                    self.assertFalse(proposal['lines'][0]['changed'])
                else:
                    with self.assertRaises(ValueError):
                        pipeline.dialogue_proposal(self.pid,'seg-01','')

if __name__ == '__main__': unittest.main()
