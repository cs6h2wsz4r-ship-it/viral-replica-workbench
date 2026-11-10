"""Round 17 scene camera tests. All model, media and Jimmy I/O is mocked."""
import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.project_fixture import fixture_path
from console import config, jimmy_bridge, llm, media, pipeline, replacement_guard as guard, store


class SceneCameraTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.temp))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('禁止配置读取')))
        self.enterContext(patch.object(config, 'load', return_value=dict(config.DEFAULTS)))
        self.enterContext(patch.object(llm, 'chat_json', side_effect=AssertionError('必须 mock 模型')))
        self.enterContext(patch.object(jimmy_bridge, '_call', side_effect=AssertionError('必须 mock Jimmy')))
        def delogo(path, masks, out):
            out.write_bytes(b'offline cleaned frame')
            return out
        self.enterContext(patch.object(media, 'delogo_frame', side_effect=delogo))
        self.enterContext(patch.object(media, 'caption_residue', return_value={'status':'checked','suspected':[]}))
        self.pid = store.create('场景离线回归')['id']
        self.directory = store.project_dir(self.pid)
        original = fixture_path('p1027232428-166a').read_bytes()
        (self.directory / 'original.json').write_bytes(original)
        self.original = json.loads((self.directory / 'original.json').read_text())
        self.data = copy.deepcopy(self.original)
        self.char = self.data['replacements']['characters'][0]
        self.portrait = self.char['asset_id']
        self.camera = self.char['camera_asset_id']
        self.product = self.data['replacements']['product']['asset_id']
        self.data['breakdown']['scene'] = [
            {'id':'S1','name':'试衣间','description':'第一场景','timeRanges':[[0,7.33]]},
            {'id':'S2','name':'住宅客厅','description':'第二场景','timeRanges':[[7.33,15.167]]}]
        self.data['breakdown']['shots'] = [dict(s, scene_id='S1' if s['start'] < 7.33 else 'S2')
                                                for s in self.data['breakdown']['shots']]
        self.char['camera_assets'] = {'S1': self.camera, 'S2': 'camera-S2'}
        self.data['assets'].append({'id':'camera-S2','type':'image','roles':['target_state'],
                                   'label':'主镜头图 C1 · S2 住宅客厅','file':'assets/camera-S2.png',
                                   'camera_character':'C1','camera_scene':'S2'})
        self.data['script']['tasks'] = []
        self.persist()
        self.ready()
        self.persist()

    def persist(self):
        for a in self.data['assets']:
            path = self.directory / a['file']
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'mock-image')
            a['sha256'] = store.sha256_file(path)
        source = self.directory / self.data['source']['file']
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b'mock-video')
        store.update(self.pid, lambda d: d.update(self.data, id=self.pid, runs={}))

    def task(self, start=8, end=15, **kw):
        return {'id':'seg-02','kind':'speaker','mode':'video','source_start':start,'source_end':end,
                'duration':7,'dialogue':'Hello','action':'','image_ids':['camera-S2',self.product],
                'blank_captions':False, **kw}

    def ready(self):
        for a in self.data['assets']:
            if a.get('camera_character'):
                a['camera_binding'] = guard.camera_binding(self.data, 'C1', scene_id=a.get('camera_scene'))
        self.data['script']['replacements_fingerprint'] = guard.fingerprint(self.data)

    def test_range_and_legacy_lookup(self):
        self.assertTrue(callable(getattr(guard, 'scene_for_range', None)), '缺少场景映射')
        for start,end,scene in [(0,5,'S1'),(8,15,'S2'),(6,9,'S2')]:
            self.assertEqual(guard.scene_for_range(self.data,start,end),scene)
        self.assertEqual(guard.scene_for_range({},0,5),'S1')
        self.assertEqual(guard.camera_image_for(self.data,'C1','S2'),'camera-S2')
        del self.char['camera_assets']
        self.assertEqual(guard.camera_image_for(self.data,'C1','S1'),self.camera)
        self.assertIsNone(guard.camera_image_for(self.data,'C1','S2'))

    def test_draft_scene_and_anchor_and_explicit_time(self):
        def frame(src,at,out,**kw):
            out.write_bytes(b'frame'); return out
        with patch.object(media,'extract_frame',side_effect=frame) as extract, \
             patch.object(llm,'image_part',return_value=[]), \
             patch.object(llm,'chat_json',return_value=({'person':'p','shot':'s','setting':'room'},{})):
            draft = pipeline.draft_camera_image(self.pid,'jimmy','C1',None,scene_id='S2')
            self.assertGreaterEqual(extract.call_args.args[1],7.33)
            self.assertLessEqual(extract.call_args.args[1],15.167)
            self.assertEqual(draft['image_ids'][:3],[self.portrait,self.product,self.camera])
            self.assertEqual(draft['for_task'],'camera:C1:S2')
            self.assertEqual(draft.get('camera_binding'),guard.camera_binding(store.load(self.pid),'C1',self.directory,scene_id='S2'))
            self.assertIn('身份锚点',draft['prompt'])
            with self.assertRaisesRegex(ValueError,'截帧时间不在场景 S2'):
                pipeline.draft_camera_image(self.pid,'jimmy','C1',1,scene_id='S2')

    def test_image_done_scene_selection(self):
        self.char['camera_assets'].pop('S2')
        self.data['images']['new'] = {'for_task':'camera:C1:S2','state':'running',
                                    'camera_character':'C1','camera_scene':'S2'}
        self.persist()
        output = self.temp / 'result.png'; output.write_bytes(b'output')
        jimmy_bridge._image_done(self.pid,'new',output)
        saved = store.load(self.pid)
        self.assertEqual(saved['replacements']['characters'][0]['camera_assets']['S2'],'new')
        asset = next(a for a in saved['assets'] if a['id']=='new')
        self.assertEqual(asset['camera_scene'],'S2')
        self.assertEqual(asset['label'],'主镜头图 C1 · S2 住宅客厅')

    def test_default_missing_and_scene_mismatch(self):
        self.assertEqual(pipeline.default_images(self.data,self.task(0,7))[0],self.camera)
        self.assertEqual(pipeline.default_images(self.data,self.task(7.5,15))[0],'camera-S2')
        self.ready()
        wrong = self.task(image_ids=[self.camera,self.product])
        self.assertIn('camera_scene_mismatch',[i['code'] for i in guard.check(self.data,wrong)['issues']])
        self.char['camera_assets']['S2'] = None
        tasks = [self.task(),self.task(9,15,id='seg-03')]
        self.data['script']['tasks'] = tasks
        missing = [i for i in guard.check(self.data,tasks[0])['issues'] if i['code']=='missing_camera_image']
        self.assertEqual(len(missing),1)
        self.assertIn('S2',missing[0]['title']); self.assertIn('seg-02',missing[0]['title'])
        self.assertIn('seg-03',' '.join(missing[0]['details']))
        (ROOT / 'runtime/temp/round17-missing-camera.txt').write_text(
            missing[0]['title'] + '\n' + missing[0]['fix'] + '\n明细：' + '、'.join(missing[0]['segment_ids']))

    def test_scene_split_and_unsplittable_references(self):
        self.assertTrue(callable(getattr(pipeline,'_scene_segments',None)),'缺少跨场景分段')
        self.ready()
        raw = [{'sourceStart':0,'sourceEnd':15.167,'kind':'speaker','imageIds':[self.camera,self.product]}]
        parts = pipeline._scene_segments(self.data,raw)
        self.assertEqual(len(parts),2)
        self.assertEqual(parts[1]['splitReason'],'场景变化：不同场景使用不同的主镜头图')
        parts = pipeline._scene_segments(self.data,[dict(raw[0],sourceStart=3,sourceEnd=9)])
        self.assertEqual(len(parts),1)
        self.assertEqual(parts[0]['imageIds'][:2],[self.camera,'camera-S2'])
        task = self.task(3,9,image_ids=parts[0]['imageIds'],scene_id=parts[0]['scene_id'],
                         scene_references=parts[0]['scene_references'])
        checked = guard.check(self.data,task)
        self.assertEqual(checked['errors'],[])
        self.assertIn('横跨两个场景',' '.join(checked['warnings']))
        self.assertIn('@image2 is the same person in scene S2',jimmy_bridge.compose_prompt(self.data,task))

    def test_scene_description_invalidates_only_its_camera(self):
        self.ready()
        self.data['breakdown']['scene'][1]['description'] = '新的布景'
        codes = [i['code'] for i in guard.check(self.data,self.task())['issues']]
        self.assertIn('camera_image_stale',codes)
        first = self.task(0,7,image_ids=[self.camera,self.product])
        self.assertNotIn('camera_image_stale',[i['code'] for i in guard.check(self.data,first)['issues']])


    def test_image_preview_anchor_metadata_and_validation(self):
        frame = {'id':'scene-frame','file':'assets/scene-frame.jpg','type':'image','roles':['scene'],
                 'label':'S2 参考帧','camera_scene':'S2','frame_at':8.0}
        self.data['assets'].append(frame)
        self.persist()
        def prepare(args,cwd,**kwargs):
            run = Path(args[args.index('--run') + 1])
            job = json.loads(Path(args[args.index('--job') + 1]).read_text())
            store.atomic_write_json(run / 'image-job.preview.json',job)
            return 'mock prepared'
        with patch.object(jimmy_bridge,'_call',side_effect=prepare):
            record = jimmy_bridge.image_preview(self.pid,'prompt',[self.portrait,self.product,self.camera,'scene-frame'],
                                                '9:16','gpt-image-2','2k','camera:C1:S2')
            self.assertEqual(record['camera_scene'],'S2')
            self.assertEqual(record['frame_asset_id'],'scene-frame')
            self.assertEqual(record['frame_at'],8)
            self.assertEqual(record['anchor_asset_id'],self.camera)
            self.assertEqual(record['image_ids'][2],self.camera)
            with self.assertRaisesRegex(jimmy_bridge.BridgeError,'身份锚点'):
                jimmy_bridge.image_preview(self.pid,'prompt',[self.portrait,self.product,'camera-S2','scene-frame'],
                                           '9:16','gpt-image-2','2k','camera:C1:S2')

    def test_saving_changes_invalidates_scene_selections(self):
        changed = copy.deepcopy(self.data['replacements'])
        changed['characters'][0]['description'] += '改发型'
        saved = pipeline.save_replacements(self.pid,changed)
        self.assertEqual(saved['replacements']['characters'][0]['camera_assets'],{'S1':None,'S2':None})
        self.assertIsNone(guard.camera_image_for(saved,'C1','S1'))
        self.assertIsNone(guard.camera_image_for(saved,'C1','S2'))

    def test_run_script_records_scene_and_keeps_scene_images(self):
        from console import motion
        self.ready(); self.persist()
        # Scene-based source clipping needs word evidence when a sentence spans
        # the scene boundary; create it locally instead of guessing its words.
        store.atomic_write_json(self.directory/'analysis/transcript.raw.json', {'language': 'en', 'segments': [
            {'start': 0, 'end': 15.167, 'text': 'Hello goodbye', 'words': [
                {'start': 0, 'end': 1, 'word': 'Hello'}, {'start': 8, 'end': 9, 'word': 'goodbye'}]}]})
        segments = [{'sourceStart':0,'sourceEnd':15.167,'kind':'speaker','useSourceFootage':True,'dialogue':'Hello'}]
        with patch.object(llm,'chat_json',return_value=({'segments':segments},{'model':'mock'})), \
             patch.object(pipeline,'apply_replacement_safety'), patch.object(pipeline,'dialogue_proposal'), \
             patch.object(motion,'cleanup_performance'):
            pipeline.run_script(self.pid,'jimmy','rewrite','',None)
        tasks = store.load(self.pid)['script']['tasks']
        self.assertEqual([t['scene_id'] for t in tasks],['S1','S2'])
        self.assertEqual([t['image_ids'][0] for t in tasks],[self.camera,'camera-S2'])
        self.assertTrue(all(t['duration'] >= 4 for t in tasks))

    def test_single_scene_matches_original_guard_and_exact_jimmy_preview(self):
        self._assert_legacy_compatibility(self.original, 'round17-compatibility.json')

    def test_second_legacy_project_exact_preview(self):
        original_path = self.directory / 'second-original.json'
        original_path.write_bytes(fixture_path('p1027195039-ccc0').read_bytes())
        original = json.loads(original_path.read_text())
        self._assert_legacy_compatibility(original, 'round17-compatibility-second.json')

    def _assert_legacy_compatibility(self, original, report_name, baseline_round="round17"):
        from console import motion
        def module(name,path):
            spec = importlib.util.spec_from_file_location(name,path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        baseline = ROOT / 'runtime/temp' / f'{baseline_round}-baseline/console'
        old_guard = module('console._round17_guard_baseline',baseline / 'replacement_guard.py')
        old_bridge = module('console._round17_bridge_baseline',baseline / 'jimmy_bridge.py')
        old_bridge.replacement_guard = old_guard
        if baseline_round in {"round19", "round20"}:
            # Freeze every prompt dependency, not just the bridge; both requests
            # use the same temporary paths/IDs and identical media mocks.
            import console
            old_motion = module('console._round19_motion_baseline', baseline / 'motion.py')
            old_kits = module('console._round19_kits_baseline', baseline / 'kits.py')
            old_motion.guard = old_guard
            old_bridge.motion = old_motion
            original_preview = old_bridge._preview
            def isolated_preview(*args, **kwargs):
                with patch.object(console, 'motion', old_motion), patch.object(console, 'kits', old_kits):
                    return original_preview(*args, **kwargs)
            old_bridge._preview = isolated_preview
        package = module('_round17_package',config.CHAIN5_SCRIPTS / 'chain5_package.py')
        original = copy.deepcopy(original)
        for entry in original['assets'] + [original['source']]:
            file = self.directory / entry['file']
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b'mocked media bytes')
        for task in original['script']['tasks']:
            self.assertEqual(guard.check(original,task)['issues'],old_guard.check(original,task)['issues'])
        self.assertEqual(guard.camera_image_for(original,'C1','S1'),original['replacements']['characters'][0]['camera_asset_id'])
        hashes = {str((self.directory / a['file']).resolve()):a['sha256'] for a in original['assets']}
        def checksum(path):
            return hashes.get(str(Path(path).resolve()),'0' * 64)
        def cut(src,start,length,out,**kwargs):
            out.write_bytes(b'mock processed video')
        def prepare(args,cwd,**kwargs):
            package.prepare(Path(args[args.index('--plan') + 1]),Path(args[args.index('--output') + 1]))
            return 'offline prepared'
        task = original['script']['tasks'][0]
        info = {'duration':15,'fps':30,'width':720,'height':1280,'audio_stream_count':0,'size_bytes':64}
        records, payloads = [], []
        with patch.object(store,'sha256_file',side_effect=checksum), \
             patch.object(package,'_file_digest',side_effect=checksum), patch.object(package,'_probe',return_value=info), \
             patch.object(package,'_image_format',return_value='png'), \
             patch.object(media,'cut_clip',side_effect=cut), patch.object(media,'probe',return_value=info), \
             patch.object(media,'caption_residue',return_value={'status':'checked','samples':[],'suspected':[]}), \
             patch.object(motion,'compare_actions',return_value=([],[])):
            for index,bridge in enumerate((old_bridge,jimmy_bridge)):
                store.update(self.pid,lambda d: d.update(copy.deepcopy(original),runs={}))
                folder = self.directory / 'packages' / f'{Path(report_name).stem}-{index}'
                with patch.object(bridge,'_call',side_effect=prepare):
                    records.append(bridge._preview(self.pid,task['id'],copy.deepcopy(original),copy.deepcopy(task),
                                                   self.directory,'r1',folder))
                payloads.append((folder / 'review/jimmy-job.preview.json').read_bytes())
        self.assertEqual(payloads[0],payloads[1],'旧项目 Jimmy 请求必须逐字相同')
        if baseline_round in {"round19", "round20"}:
            for label, payload in zip(("before", "after"), payloads):
                (ROOT / 'runtime/temp' / f"{baseline_round}-{original['id']}-{label}.json").write_bytes(payload)
        # 24.10 intentionally replaces union bands with individual caption masks.
        # Keep request bytes and every other processing option strictly identical.
        self.assertEqual({k:v for k,v in records[0]['clip_processing'].items() if k != 'masks'},
                         {k:v for k,v in records[1]['clip_processing'].items() if k != 'masks'})
        expected_masks = {
            'p1027232428-166a': [
                ([.07,.745,.93,.915],.85,4.15), ([.065,.704,.935,.878],3.85,5.22),
                ([.36,.82,.65,.93],4.92,6.22), ([.36,.82,.65,.93],5.92,7.42),
                ([.38,.82,.62,.93],7.12,8.02), ([.05,.79,.95,.93],8.12,10.3),
                ([.01,.69,.99,.85],10.48,13.92), ([.24,.87,.76,.99],13.62,15)],
            'p1027195039-ccc0': [
                ([.04,.69,.96,.83],.85,4.85), ([.36,.72,.65,.85],5.15,6.25),
                ([.36,.72,.65,.85],5.95,7.25), ([.4,.72,.61,.85],6.95,8.25),
                ([.09,.695,.91,.82],7.95,10.65), ([.02,.69,.98,.84],10.45,13.75),
                ([.24,.65,.76,.77],13.55,15)],
        }[original['id']]
        new_masks = records[1]['clip_processing']['masks']
        self.assertEqual([(m['bbox'],m['start'],m['end']) for m in new_masks], expected_masks)
        self.assertEqual([m['source_bboxes'] for m in new_masks],
                         [[o['bbox']] for o in original['breakdown']['overlays']])
        self.assertTrue(all('motion_warning' not in m for m in new_masks))
        summary = {'project_id':original['id'],'issues_identical':True,'preview_bytes_identical':True,'preview_bytes':len(payloads[0]),
                   'prompt_identical':records[0]['preview']['tasks'][0]['prompt']==records[1]['preview']['tasks'][0]['prompt'],
                   'references_identical':records[0]['preview']['tasks'][0]['images']==records[1]['preview']['tasks'][0]['images'],
                   'masks_and_mute_identical':records[0]['clip_processing']==records[1]['clip_processing']}
        (ROOT / 'runtime/temp' / report_name).write_text(json.dumps(summary,ensure_ascii=False,indent=2))
        print('第 17 轮单场景兼容：'+json.dumps(summary,ensure_ascii=False))


    def test_first_appearance_uses_start_even_if_shot_mostly_second_scene(self):
        self.data['breakdown']['shots'] = [{'start':7,'end':15,'characters':['C1']}]
        del self.char['camera_assets']
        self.assertEqual(guard.first_scene(self.data,'C1'),'S1')
        self.assertEqual(guard.camera_image_for(self.data,'C1','S1'),self.camera)
        self.assertIsNone(guard.camera_image_for(self.data,'C1','S2'))

    def test_missing_scene_ranges_normalize_to_default_scene(self):
        self.data['breakdown']['scene'] = [{'id':'S9','name':'旧客厅','description':'没有时间范围'}]
        self.data['breakdown']['shots'] = []
        self.persist()
        self.assertEqual(guard.scenes(self.data)[0]['id'],'S1')
        self.assertEqual(guard.first_scene(self.data,'C1'),'S1')
        def frame(src,at,out,**kw):
            out.write_bytes(b'frame'); return out
        with patch.object(media,'extract_frame',side_effect=frame), patch.object(llm,'image_part',return_value=[]), \
             patch.object(llm,'chat_json',return_value=({'person':'p','shot':'s','setting':'room'},{})):
            draft = pipeline.draft_camera_image(self.pid,'jimmy','C1')
        self.assertEqual(draft['scene_id'],'S1')

    def test_scene_regeneration_replaces_selected_legacy_camera(self):
        self.data = copy.deepcopy(self.original)
        self.persist()
        self.ready()
        self.data['images']['new'] = {'for_task':'camera:C1:S1','state':'running',
                                    'camera_character':'C1','camera_scene':'S1'}
        self.persist()
        output = self.temp / 'result.png'; output.write_bytes(b'output')
        jimmy_bridge._image_done(self.pid,'new',output)
        saved = store.load(self.pid)
        self.assertEqual(guard.camera_image_for(saved,'C1','S1'),'new')
        self.assertEqual(saved['replacements']['characters'][0]['camera_asset_id'],self.camera)

    def test_crossing_missing_secondary_camera_blocks_and_does_not_claim_two_images(self):
        self.ready()
        self.char['camera_assets']['S2'] = None
        task = self.task(3,9,image_ids=[self.camera,self.product])
        result = guard.check(self.data,task)
        missing = [i for i in result['issues'] if i['code']=='missing_camera_image']
        self.assertTrue(any(i.get('scene_id') == 'S2' for i in missing))
        self.assertNotIn('已同时放入两个场景',' '.join(result['warnings']))


    def test_default_uses_only_visible_replaced_character(self):
        self.data['replacements']['characters'].append({'source_id':'C2','action':'replace','asset_id':'person-C2',
                                                       'camera_assets':{'S2':'camera-C2'}})
        self.data['assets'].extend([{'id':'camera-C2','type':'image','roles':['target_state'],'file':'assets/camera-C2.png'},
                                    {'id':'person-C2','type':'image','roles':['person_identity'],'file':'assets/person-C2.png'}])
        self.persist()
        self.data['breakdown']['shots'] = [{'start':0,'end':7.33,'characters':['C1']},
                                          {'start':7.33,'end':15.167,'characters':['C2']}]
        self.assertEqual(pipeline.default_images(self.data,self.task())[0],'camera-C2')

    def test_new_save_does_not_create_legacy_field(self):
        changed = copy.deepcopy(self.data['replacements'])
        del changed['characters'][0]['camera_asset_id']
        changed['characters'][0]['description'] = '新的造型'
        saved = pipeline.save_replacements(self.pid,changed)
        self.assertNotIn('camera_asset_id',saved['replacements']['characters'][0])

    def test_stale_assets_available_without_script_and_targeted_issue(self):
        self.assertTrue(callable(getattr(guard,'camera_stale_assets',None)),'需要无脚本时的过期信息')
        self.ready()
        self.data['breakdown']['scene'][1]['description'] = '布景变化'
        self.data['assets'].append({'id':'frame-only','camera_scene':'S1'})
        self.assertEqual(guard.camera_stale_assets(self.data),['camera-S2'])
        issue = next(i for i in guard.check(self.data,self.task())['issues'] if i['code']=='camera_image_stale')
        self.assertEqual((issue['character_id'],issue['scene_id']),('C1','S2'))
        self.assertEqual(issue.get('asset_id'),'camera-S2')


    def test_pending_camera_preview_rejects_changed_binding_and_anchor_before_submit(self):
        self.ready()
        self.data['images']['pending'] = {'id':'pending','state':'pending_confirmation','for_task':'camera:C1:S2',
            'camera_character':'C1','camera_scene':'S2','anchor_asset_id':self.camera,'run_dir':'image-runs/pending',
            'camera_binding':guard.camera_binding(self.data,'C1',self.directory,scene_id='S2')}
        self.persist()
        for change in ('description','anchor'):
            with self.subTest(change=change):
                changed = copy.deepcopy(self.data)
                if change == 'description':
                    changed['replacements']['characters'][0]['description'] += '变化'
                else:
                    changed['assets'].append({'id':'another-camera','type':'image','roles':['target_state'],
                                               'file':next(a['file'] for a in changed['assets'] if a['id']==self.camera)})
                    changed['replacements']['characters'][0]['camera_assets']['S1'] = 'another-camera'
                store.update(self.pid,lambda d: d.update(changed))
                with patch.object(config,'jimmy_configured',return_value=True), \
                     patch.object(jimmy_bridge.jobs,'start'), patch.object(config,'jimmy_env_file',return_value=self.temp / 'mock.env'), \
                     patch.object(jimmy_bridge,'_image_submit') as submit:
                    with self.assertRaisesRegex(jimmy_bridge.BridgeError,'主镜头图.*过期'):
                        jimmy_bridge.image_confirm(self.pid,'pending')
                    submit.assert_not_called()
                self.assertTrue(callable(getattr(guard,'camera_stale_images',None)))
                self.assertIn('pending',guard.camera_stale_images(changed,self.directory))


    def test_generic_generated_images_without_for_task_remain_supported(self):
        self.data['images']['generic'] = {'for_task':None,'state':'completed','asset_id':'generic'}
        self.assertEqual(guard.camera_images(self.data)['C1'],self.camera)

    def test_crossing_reference_insertion_rebases_product_tokens(self):
        raw = {'sourceStart':3,'sourceEnd':9,'kind':'speaker','imageIds':[self.camera,self.product],
               'action':'The person from @image1 likes the item from @image2.',
               'story':'Display @image2.', 'direction':'Keep @image1.'}
        part = pipeline._scene_segments(self.data,[raw])[0]
        self.assertEqual(part['imageIds'],[self.camera,'camera-S2',self.product])
        self.assertEqual(part['action'],'The person from @image1 likes the item from @image3.')
        self.assertEqual(part['story'],'Display @image3.')
        self.assertEqual(part['direction'],'Keep @image1.')
        second = pipeline._scene_segments(self.data,[dict(raw,sourceStart=8,sourceEnd=15,
                       imageIds=[self.product,self.camera],action='Product @image1; person @image2.')])[0]
        self.assertEqual(second['imageIds'],['camera-S2',self.product])
        self.assertEqual(second['action'],'Product @image2; person @image1.')

    def test_single_scene_selection_schema_migration_keeps_fingerprint(self):
        original = copy.deepcopy(self.original)
        before = guard.fingerprint(original)
        char = original['replacements']['characters'][0]
        char['camera_assets'] = {'S1':char.pop('camera_asset_id')}
        self.assertEqual(guard.fingerprint(original),before)
        self.assertEqual(guard.check(original,original['script']['tasks'][0])['issues'],[])


    def test_old_draft_cannot_be_rebound_to_current_settings_by_preview(self):
        old_binding = guard.camera_binding(self.data,'C1',self.directory,scene_id='S2')
        self.data['breakdown']['scene'][1]['description'] = '起草之后修改了场景'
        frame = {'id':'draft-frame','type':'image','file':'assets/draft-frame.jpg','roles':['scene'],
                 'camera_scene':'S2','frame_at':8}
        self.data['assets'].append(frame)
        self.persist()
        with patch.object(jimmy_bridge,'_call') as call:
            with self.assertRaisesRegex(jimmy_bridge.BridgeError,'起草.*过期'):
                jimmy_bridge.image_preview(self.pid,'old prompt',[self.portrait,self.product,self.camera,'draft-frame'],
                    '9:16','gpt-image-2','2k','camera:C1:S2',draft_binding=old_binding)
            call.assert_not_called()

    def test_legacy_inflight_camera_completion_keeps_binding_semantics(self):
        self.data = copy.deepcopy(self.original)
        self.persist()
        self.data['images']['legacy-inflight'] = {'id':'legacy-inflight','for_task':'camera:C1','state':'running',
            'camera_character':'C1','camera_product_id':self.product,
            'camera_binding':guard.camera_binding(self.data,'C1',self.directory)}
        store.update(self.pid,lambda d:d.update(self.data))
        output = self.temp / 'legacy-result.png'; output.write_bytes(b'mock legacy output')
        jimmy_bridge._image_done(self.pid,'legacy-inflight',output)
        saved = store.load(self.pid)
        asset = next(a for a in saved['assets'] if a['id']=='legacy-inflight')
        self.assertEqual(asset['camera_scene'],'S1')
        self.assertNotIn('legacy-inflight',guard.camera_stale_assets(saved,self.directory))
        saved['replacements']['characters'][0]['description'] += '已经修改'
        self.assertIn('legacy-inflight',guard.camera_stale_assets(saved,self.directory))

if __name__ == '__main__':
    unittest.main(verbosity=2)
