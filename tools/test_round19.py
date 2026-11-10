"""Round 19: local copied incident, mocked providers and immutable legacy request goldens."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.project_fixture import fixture_path, load_project
from console import asr, config, gateway, jimmy_bridge as bridge, llm, media, motion, pipeline, prompts, replacement_guard as guard, store


class Round19Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.tmp))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('禁止读取配置')))
        # Gateway routing reads explicit offline settings; credential parsing stays forbidden.
        self.enterContext(patch.object(config, "load", return_value=dict(config.DEFAULTS)))
        self.enterContext(patch.object(llm, 'chat_json', side_effect=AssertionError('必须 mock 模型')))
        self.enterContext(patch.object(llm, 'chat_json_checked', side_effect=AssertionError('必须 mock 模型')))
        self.enterContext(patch.object(bridge, '_call', side_effect=AssertionError('必须 mock Jimmy')))
        def delogo(path, masks, out):
            out.write_bytes(b'offline cleaned frame')
            return out
        self.enterContext(patch.object(media, 'delogo_frame', side_effect=delogo))
        self.enterContext(patch.object(media, 'caption_residue', return_value={'status':'checked','suspected':[]}))
        self.pid = store.create('round19')['id']; self.directory = store.project_dir(self.pid)
        raw = fixture_path('p1029100000-r18v').read_bytes()
        (self.directory / 'fixture.json').write_bytes(raw)
        self.data = json.loads((self.directory / 'fixture.json').read_text())
        self.data['id'] = self.pid
        for a in self.data['assets']:
            path = self.directory / a['file']; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'offline'); a['sha256'] = store.sha256_file(path)
        for a in self.data['assets']:
            if a.get('camera_character'):
                a['camera_binding'] = guard.camera_binding(self.data, a['camera_character'], self.directory, scene_id=a.get('camera_scene'))
        self.lines = copy.deepcopy(self.data['breakdown']['dialogue'])
        self.labels = [dict(start=r['start'], end=r['end'], speaker='C2' if i == 0 else 'C1',
                            onScreen=i != 0, evidence='画外称赞，出镜人物听后回应' if i == 0 else '出镜人物正面说话') for i,r in enumerate(self.lines)]
        self.transcript = {'language':'en', 'segments':copy.deepcopy(self.lines)}
        store.atomic_write_json(self.directory / 'analysis/transcript.raw.json', self.transcript)
        self.persist()

    def persist(self):
        store.update(self.pid, lambda d: d.update(copy.deepcopy(self.data)))

    def annotate(self):
        task = self.data['script']['tasks'][0]
        task['dialogue_lines'] = [{**r, **s} for r,s in zip(self.lines, self.labels)]
        task['image_ids'] = pipeline.default_images(self.data, task)
        return task

    def test_close_read_speakers_pack(self):
        overview = {**self.data['breakdown'], 'passages':[{'id':'P1','start':0,'end':10}]}
        meta = {'usage':{},'seconds':0,'model':'mock'}
        with patch.object(config,'load',return_value=dict(config.DEFAULTS)), \
             patch.object(config,'provider_settings',return_value={'model':'mock'}), \
             patch.object(media,'probe',return_value={'duration':10,'audio_stream_count':1}), \
             patch.object(media,'hypit_boundaries',return_value=[]), \
             patch.object(media,'merge_boundaries',return_value=[]), \
             patch.object(pipeline,'_grids',return_value=[]), \
             patch.object(asr,'parse_srt',return_value=self.transcript), \
             patch.object(llm,'chat_json',side_effect=[(overview,meta),({'lineSpeakers':self.labels},meta)]):
            pipeline._run_analysis(self.pid,'jimmy',srt_text='mock')
        rows = store.load(self.pid)['breakdown']['dialogue']
        self.assertEqual([r['speaker'] for r in rows], ['C2','C1','C1','C1','C1'])
        self.assertIs(rows[0]['onScreen'],False)
        self.assertTrue(all(r['onScreen'] for r in rows[1:]))
        self.assertIn('lineSpeakers',prompts.CLOSE_SYSTEM)

    def test_old_script_backfills_source_lines(self):
        result = {'lineSpeakers':self.labels,'segments':[{'sourceStart':0,'sourceEnd':10,'kind':'speaker','mode':'video'}]}
        with patch.object(llm,'chat_json',return_value=(result,{'model':'mock'})), \
             patch.object(pipeline,'apply_replacement_safety'), patch.object(motion,'cleanup_performance'):
            pipeline.run_script(self.pid,'jimmy','keep','',None)
        task = store.load(self.pid)['script']['tasks'][0]
        self.assertEqual([r['speaker'] for r in task.get('dialogue_lines',[])], ['C2','C1','C1','C1','C1'])
        self.assertIs(task['dialogue_windows'][0]['onScreen'],False)
        self.assertIn('lineSpeakers',prompts.SCRIPT_SYSTEM)

    def test_prompt_offscreen_and_unknown_and_image_mode(self):
        task = self.annotate()
        prompt = bridge.compose_prompt(self.data,task,self.directory)
        self.assertIn('OFF-SCREEN (C2 画外称赞者, voice from behind the camera)',prompt)
        self.assertEqual(prompt.count('ON-SCREEN (the person from @image1)'),4)
        self.assertNotIn('The visible person speaks every line.',prompt)
        self.assertNotIn("woman's voice",prompt)
        self.assertIn('only listens and reacts',prompt)
        (ROOT / 'runtime/temp/round19-new-prompt.txt').write_text(prompt)
        image_task = {**task,'mode':'image'}
        self.assertIn('OFF-SCREEN (C2',bridge.compose_prompt(self.data,image_task,self.directory))
        unknown = copy.deepcopy(task); unknown['dialogue_lines'][0].update(speaker='unknown',onScreen=None)
        self.assertNotIn('The visible person speaks every line.',bridge.compose_prompt(self.data,unknown,self.directory))
        self.assertIn('UNKNOWN (speaker not established',bridge.compose_prompt(self.data,unknown,self.directory))
        self.assertIn('有 1 句台词还没确定是谁说的，请在第 04 步（对照和修改）的“提示词”里确认',guard.check(self.data,unknown,self.directory)['warnings'])

    def test_full_preview_preserves_attribution_and_merged_warning(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('round19_package', config.CHAIN5_SCRIPTS / 'chain5_package.py')
        package = importlib.util.module_from_spec(spec); spec.loader.exec_module(package)
        task = self.annotate()
        self.data['script']['replacements_fingerprint'] = guard.fingerprint(self.data,self.directory)
        source = self.directory / self.data['source']['file']; source.parent.mkdir(parents=True,exist_ok=True); source.write_bytes(b'offline-video')
        self.persist()
        info = {'duration':10,'fps':30,'width':720,'height':1280,'audio_stream_count':0,'size_bytes':64}
        def cut(src,start,length,out,**kw): out.write_bytes(b'offline-clip')
        def prepare(args,*a,**kw):
            package.prepare(Path(args[args.index('--plan')+1]),Path(args[args.index('--output')+1])); return 'offline'
        with patch.object(package,'_probe',return_value=info), patch.object(package,'_image_format',return_value='png'), \
             patch.object(media,'cut_clip',side_effect=cut), patch.object(media,'probe',return_value=info), \
             patch.object(media,'caption_residue',return_value={'status':'checked','samples':[],'suspected':[]}), \
             patch.object(motion,'compare_actions',return_value=([],[])), patch.object(bridge,'_call',side_effect=prepare):
            record = bridge._preview(self.pid,task['id'],self.data,task,self.directory,'r19',self.directory/'packages/round19')
        prompt = record['preview']['tasks'][0]['prompt']
        self.assertIn('OFF-SCREEN (C2',prompt)
        self.assertEqual(prompt.count('ON-SCREEN (the person from @image1)'),4)
        self.assertNotIn('The visible person speaks every line.',prompt)
        conflict = [w for w in record['warnings'] if '服装特征' in w]
        self.assertEqual(len(conflict),1); self.assertNotIn('改为 rewrite',str(record['warnings']))
        (ROOT/'runtime/temp/round19-preview-prompt.txt').write_text(prompt)

    def test_overlap_unknown_is_not_guessed(self):
        self.assertTrue(callable(getattr(motion,'annotate_speakers',None)))
        rows = motion.annotate_speakers(self.lines, [dict(self.labels[0],speaker='C9')],self.data['breakdown']['characters'])
        self.assertEqual(rows[0]['speaker'],'unknown')
        self.assertIsNone(rows[0]['onScreen'])
        rows = motion.annotate_speakers(self.lines,[dict(self.labels[0],end=2.1),self.labels[1]],self.data['breakdown']['characters'])
        self.assertEqual([r['speaker'] for r in rows[:2]],['C2','C1'])
        self.assertEqual(rows[2]['speaker'],'unknown')

    def test_proposal_preserves_speakers_even_if_model_changes_them(self):
        self.annotate(); self.persist()
        proposed = ['Excuse me, your look is lovely.','Oh, thank you so much.','It feels nice.','Love it.','The fabric is perfect for this weather.']
        response = {'lines':[dict(start=r['start'],end=r['end'],original=r['text'],proposed=text,speaker='C1',onScreen=True,reason='商品冲突') for r,text in zip(self.lines,proposed)]}
        with patch.object(llm,'chat_json_checked',return_value=(response,{'model':'mock'})):
            proposal = pipeline.dialogue_proposal(self.pid,'seg-01')
        self.assertEqual(proposal['lines'][0].get('speaker'),'C2')
        with patch.object(pipeline,'apply_replacement_safety'):
            saved = pipeline.adopt_dialogue_proposal(self.pid,'seg-01')
        task = saved['script']['tasks'][0]
        self.assertEqual(task['dialogue_lines'][0]['speaker'],'C2')
        self.assertIs(task['dialogue_lines'][0]['onScreen'],False)
        self.assertEqual(task['dialogue_lines'][0]['text'],proposed[0])
        self.assertEqual(task['dialogue_windows'][0]['text'],proposed[0])
        self.assertFalse(guard.dialogue_mismatches(saved,task))

    def test_frame_reason_actual_asset_and_new_selection(self):
        self.assertTrue(callable(getattr(pipeline,'frame_metadata',None)))
        frame = next(a for a in self.data['assets'] if 'frame-C1-0.50' in a.get('file',''))
        meta = pipeline.frame_metadata(self.data,frame)
        self.assertEqual(meta['frame_reason'],'legacy_first_appearance')
        self.assertIn('这一帧是背影',meta['frame_warning'])
        def extract(src,at,out,**kw): out.write_bytes(b'frame'); return out
        with patch.object(media,'extract_frame',side_effect=extract), patch.object(llm,'image_part',return_value=[]), \
             patch.object(llm,'chat_json',return_value=({'person':'p','shot':'s','setting':'s'},{})):
            draft = pipeline.draft_camera_image(self.pid,'jimmy','C1')
            saved = store.load(self.pid)
            frame = next(a for a in saved['assets'] if a['id']==draft['frame_asset_id'])
            self.assertEqual(frame['frame_reason'],'front_product')
            manual = pipeline.draft_camera_image(self.pid,'jimmy','C1',at=.5)
            self.assertEqual(manual['frame_reason_code'],'manual')
            frame = next(a for a in store.load(self.pid)['assets'] if a['id']==manual['frame_asset_id'])
            self.assertEqual(frame['frame_reason'],'legacy_first_appearance')

    def test_manual_frame_reason_survives_preview_and_generated_asset(self):
        frame = next(a for a in self.data['assets'] if 'frame-C1-0.50' in a.get('file',''))
        rep = self.data['replacements']
        ids = [rep['characters'][0]['asset_id'],rep['product']['asset_id'],frame['id']]
        def prepare(args,*a,**kw):
            folder = Path(args[args.index('--run')+1]); folder.mkdir(parents=True)
            store.atomic_write_json(folder/'image-job.preview.json',{'prompt':'mock'})
            return '离线预览'
        with patch.object(bridge,'_call',side_effect=prepare):
            record = bridge.image_preview(self.pid,'mock',ids,'9:16','gpt-image-2','2k','camera:C1:S1',draft_frame_reason='manual')
        self.assertEqual(record['frame_reason'],'manual')
        self.assertEqual(record['frame_at'],.5)
        output = self.directory/'manual.png'; output.write_bytes(b'mock output')
        store.update(self.pid, lambda d:d['replacements']['characters'][0]['camera_assets'].update(S1='deleted'))
        def safety(step, messages, **kwargs):
            rows = json.loads(messages[-1]['content'])['tasks']
            return {'tasks': [{**{k: row[k] for k in ('id', 'action', 'story', 'direction')}, 'changes': []}
                              for row in rows]}, {'provider': 'mock', 'model': 'mock', 'attempts': []}
        with patch.object(gateway, 'reason_checked', side_effect=safety) as check:
            bridge._image_done(self.pid,record['id'],output)
        self.assertEqual(check.call_count, 1, '自动跟随必须执行安全校对')
        saved = store.load(self.pid)
        asset = next(a for a in saved['assets'] if a['id']==record['id'])
        self.assertEqual(asset['frame_reason'],'manual')
        self.assertEqual(asset['frame_asset_id'],frame['id'])
        self.assertNotIn('deleted',saved['replacements']['characters'][0]['camera_assets'].values())

    def test_one_dialogue_warning_with_original_details_and_suppression(self):
        task = self.data['script']['tasks'][0]
        warnings = guard.dialogue_mismatches(self.data,task)
        self.assertEqual(len(warnings),1)
        self.assertIn('台词里有 3 处提到原片的服装特征',warnings[0])
        self.assertIn('改写冲突台词',warnings[0]); self.assertNotIn('改为 rewrite',warnings[0])
        checked = guard.check(self.data,task,self.directory)
        issue = checked['dialogue_conflicts'][0]
        self.assertEqual(len(issue['details']),3)
        self.assertIn(self.lines[0]['text'],issue['details'])
        for choice in ('keep','adopt'):
            self.assertFalse(guard.dialogue_mismatches(self.data,{**task,'dialogue_choice':choice}))
        (ROOT / 'runtime/temp/round19-dialogue-warning.json').write_text(json.dumps(issue,ensure_ascii=False,indent=2))

    def test_structured_rewrite_keeps_multi_sentence_row_and_speaker(self):
        task = self.annotate()
        task['dialogue_lines'] = [{**self.lines[0],**self.labels[0],'text':'Nice shirt. Love it!'},
                                  {**self.lines[1],**self.labels[1]}]
        task['dialogue'] = '\n'.join(r['text'] for r in task['dialogue_lines'])
        self.data['script']['dialogue_mode'] = 'rewrite'
        rows, warnings = motion.dialogue_windows(self.data,task,self.directory)
        self.assertFalse(warnings)
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['speaker'],'C2')
        self.assertEqual(rows[0]['text'],'Nice shirt. Love it!')
        self.assertIn('OFF-SCREEN (C2',bridge.compose_prompt(self.data,task,self.directory))

    def test_legacy_frame_back_evidence_in_camera_field(self):
        frame = next(a for a in self.data['assets'] if 'frame-C1-0.50' in a.get('file',''))
        self.data['breakdown']['shots'] = [{'start':0,'end':2,'characters':['C1'],'camera':'从背影拍摄','action':'站立'}]
        self.assertIn('这一帧是背影',pipeline.frame_metadata(self.data,frame)['frame_warning'])

    def test_chinese_conflict_details_keep_original_sentence(self):
        task = {**self.data['script']['tasks'][0], 'dialogue':'这条裙子很好看。'}
        issues = guard.dialogue_conflicts(self.data,task)
        self.assertTrue(issues)
        self.assertEqual(issues[0]['details'],['这条裙子很好看。'])

    def test_invalid_camera_ids_cleaned_on_save(self):
        rep = copy.deepcopy(self.data['replacements'])
        rep['characters'][0]['camera_assets']['S1'] = 'deleted'
        rep['characters'][0]['camera_assets']['deleted-scene'] = 'deleted'
        saved = pipeline.save_replacements(self.pid,rep)
        cameras = saved['replacements']['characters'][0]['camera_assets']
        self.assertNotIn('deleted',cameras.values()); self.assertNotIn('deleted-scene',cameras)
        self.assertEqual(cameras['S1'],guard.camera_image_for(saved,'C1','S1'))

    def test_speaker_only_save_is_free_even_without_safety_cache(self):
        task = self.annotate(); self.persist()
        script = copy.deepcopy(self.data['script'])
        script['tasks'][0]['dialogue_lines'][0].update(speaker='C1',onScreen=True)
        saved = pipeline.save_script(self.pid,script)
        self.assertEqual(saved['script']['tasks'][0]['dialogue_lines'][0]['speaker'],'C1')

    def test_legacy_requests_against_frozen_prechange_bytes(self):
        from tools.test_scene_camera import SceneCameraTests
        # This fixture's transcript belongs to the incident, never either legacy project.
        (self.directory / 'analysis/transcript.raw.json').unlink()
        for pid in ('p1027232428-166a','p1027195039-ccc0'):
            original = load_project(pid)
            SceneCameraTests._assert_legacy_compatibility(self,original,'round19-compatibility-'+pid+'.json',baseline_round='round19')


if __name__ == '__main__':
    from tools.run_tests_offline import audit
    sys.addaudithook(audit)
    unittest.main()
