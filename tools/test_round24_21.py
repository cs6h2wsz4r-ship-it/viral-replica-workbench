"""24.21: synthetic father/son/watch scenes; no remote/model/real-project I/O."""
import copy
import hashlib
import importlib.util
import json
import os
import unittest
from unittest.mock import patch

from console import asr, gateway, jimmy_bridge, media, motion, pipeline, scene_plan, store
from tools import test_round24_17 as fixture


def generated():
    return {'language': 'en', 'segments': [{'start': 3.106, 'end': 3.82, 'text': 'Thank you, son.',
        'words': [{'word': 'Thank', 'start': 3.106, 'end': 3.25},
                  {'word': 'you,', 'start': 3.26, 'end': 3.45}, {'word': 'son.', 'start': 3.5, 'end': 3.82}]}]}


class Round24_21Tests(unittest.TestCase):
    setUp = fixture.Round24_17Tests.setUp
    persist = fixture.Round24_17Tests.persist
    frame = staticmethod(fixture.Round24_17Tests.frame)
    camera = fixture.Round24_17Tests.camera

    def task(self):
        next(a for a in self.data['assets'] if a['id']=='watch')['description'] = 'Multi-view watch product sheet with English labels; use only the physical product.'
        self.camera('main')
        self.data['breakdown']['shots'][-2]['action'] = '父亲倒在厨房地上，蓝色开衫，白色 SENVIA SOS 手环戴在父亲手腕上'
        self.data['breakdown']['shots'][-1]['action'] = '儿子走进来扶起父亲，两人拥抱'
        self.data['breakdown']['productMoments'][-1]['product'] = '白色 SENVIA SOS 手环，戴在父亲手腕'
        task = {'id': 'seg-03', 'order': 1, 'title': '厨房救援', 'kind': 'speaker', 'mode': 'video',
            'source_start': 9, 'source_end': 12.3, 'duration': 4, 'target_seconds': 3.3,
            'dialogue': 'Thank you, son.', 'action': 'Grateful expression.', 'scene_id': 'S4',
            'image_ids': ['main', 'father', 'son', 'watch'], 'identity_reference_version': 1,
            'dialogue_lines': [{'start': 9.885, 'end': 10.457, 'text': 'Thank you, son.',
                                'speaker': 'C1', 'onScreen': True}]}
        self.data['script'] = {'provider': 'mock', 'model': 'offline', 'dialogue_mode': 'keep', 'tasks': [task]}
        return task

    def english(self, task):
        rows = motion.source_timeline(self.data, task)
        en = [{**r, 'action': a} for r, a in zip(rows, [
            'THE FATHER lies on the kitchen floor.', 'THE SON enters, helps THE FATHER up, then they embrace.'])]
        self.data['action_comparisons'] = {task['id']: {'fingerprint': jimmy_bridge.preflight_fingerprint(self.data, task, self.directory),
            'comparison': {'status': 'completed', 'sourceMotionEn': en,
                           'sourceProductEn': "white SENVIA SOS bracelet on THE FATHER's wrist"}}}
        return en

    def test_multi_person_scene_prompt(self):
        task = self.task(); self.english(task)
        text = jimmy_bridge.compose_prompt(self.data, task, self.directory)
        for phrase in ['@image2 is the identity of THE FATHER (C1)', '@image3 is the identity of THE SON (C2)',
                       'different person', 'never merge, swap or blend', 'THE FATHER speaks every line',
                       'THE SON does not speak', 'very first frame to the last frame', 'ignore its clothing',
                       'Clothing follows @video1', 'white SENVIA SOS bracelet', 'use, wear or place the product correctly',
                       'front or functional side facing the camera', 'multi-view product sheet', 'never render its layout or label text']:
            self.assertIn(phrase, text)
        for phrase in ['same person', 'talking-head', 'The visible person speaks every line', 'packaging text', 'the person from @image']:
            self.assertNotIn(phrase, text)
        timeline = text.split('SOURCE MOTION TIMELINE:', 1)[1].split('\n\n', 1)[0]
        self.assertNotRegex(timeline, r'[\u4e00-\u9fff]')
        self.assertIn('[0.885–1.457]', text)
        (store.config.ROOT / 'runtime/temp/round24_21-new-prompt.txt').write_text(text)

    def test_opener_describes_scene_cast_and_source_timing(self):
        task = self.task(); self.english(task)
        text = jimmy_bridge.compose_prompt(self.data,task,self.directory)
        self.assertIn('@image1 shows the replaced scene (THE FATHER and THE SON in the source setting)',text)
        self.assertIn('timing and framing over time follow @video1',text)
        self.assertNotIn('absent from @image1',text)

    def test_legacy_prompt_is_byte_identical_to_round21_baseline(self):
        task = self.task()
        self.data['replacements'].pop('scene_plan')
        self.data['images'] = {}
        for asset in self.data['assets']:
            for key in ('scene_camera_binding','scene_camera_scene'):
                asset.pop(key,None)
        self.assertFalse(scene_plan.enabled(self.data))
        spec = importlib.util.spec_from_file_location('console._round24_21_legacy',
            store.config.ROOT/'tools/fixtures/round21/jimmy_bridge_before.py')
        before = importlib.util.module_from_spec(spec); spec.loader.exec_module(before)
        for mode in ('image','video'):
            task['mode'] = mode
            self.assertEqual(jimmy_bridge.compose_prompt(self.data,task,self.directory).encode(),
                             before.compose_prompt(self.data,task,self.directory).encode())

    def test_missing_english_warns_and_stale_cache_is_rejected(self):
        task = self.task(); self.english(task)
        task['action'] = 'New expression.'
        check = jimmy_bridge.replacement_check(self.data, task, self.directory)
        self.assertIn('动作时间轴未能转成英文', check['warnings'])
        self.assertNotIn('THE SON enters', jimmy_bridge.compose_prompt(self.data, task, self.directory))

    def test_existing_check_returns_english_without_extra_call(self):
        task = self.task(); en = self.english(task)
        report = {}
        with patch.object(gateway, 'reason_checked', return_value=({'additions': [], 'sourceMotionEn': en},
                {'provider': 'mock', 'model': 'offline', 'attempts': []})) as reason:
            motion.compare_actions(self.data, task, motion.source_timeline(self.data, task), report=report)
        self.assertEqual(reason.call_count, 1)
        self.assertIn('sourceMotionEn', reason.call_args.args[1][0]['content'])
        self.assertEqual(report.get('sourceMotionEn'), en)

    def test_invalid_english_times_or_roles_falls_back(self):
        task = self.task(); en = self.english(task)
        for bad in [[{**en[0], 'start': .5}, en[1]], [{**en[0], 'action': '父亲 lies down.'}, en[1]],
                    [{**en[0], 'action': 'the person from @image2 lies down.'}, en[1]]]:
            with self.subTest(bad=bad), patch.object(gateway, 'reason_checked', return_value=(
                    {'additions': [], 'sourceMotionEn': bad}, {'provider': 'mock', 'model': 'offline', 'attempts': []})):
                report = {}; motion.compare_actions(self.data, task, motion.source_timeline(self.data, task), report=report)
                self.assertNotIn('sourceMotionEn', report)
                self.assertIn('动作时间轴未能转成英文', report['warnings'])

    def test_long_timeline_compresses_before_replacement_rules(self):
        task = self.task(); en = self.english(task)
        en[0]['action'] += ' THE FATHER remains in the source position.' * 200
        text = jimmy_bridge.compose_prompt(self.data, task, self.directory)
        self.assertLessEqual(len(text), 5000)
        self.assertIn('very first frame to the last frame', text)
        self.assertIn('front or functional side facing the camera', text)

    def test_video_auto_frame_covers_cast_and_sends_all_checked_people(self):
        task = self.task(); self.persist()
        choice = scene_plan.frame_choice(self.data, 'S4')
        self.assertTrue(11.2 <= choice['at'] <= 11.7)
        self.assertEqual(choice['characters'], ['C1','C2'])
        self.assertIn('儿子 10.58 秒后进画面', choice['reason'])
        draft = pipeline.draft_scene_camera_image(self.pid, 'openai', 'S4')
        self.assertIn('son', draft['image_ids'])
        self.assertIn('father', draft['image_ids'])
        self.assertNotIn('not in this frame', draft['prompt'])
        self.assertIn('son', pipeline.default_images(self.data, task))
        manual = pipeline.capture_scene_frame(self.pid,'S4',at=9.5)
        self.assertEqual(manual['reason'],'手动选择')
        self.assertEqual(manual['image_subject_keys'],['C1','C2','product'])
        late = pipeline.capture_scene_frame(self.pid,'S4',at=11.4)
        self.assertEqual(late['image_subject_keys'],['C1','C2','product'])

    def test_image_mode_keeps_full_cast_frame_and_manual_is_unchanged(self):
        task = self.task(); task['mode'] = 'image'
        self.assertGreater(scene_plan.frame_choice(self.data, 'S4')['at'], 10.58)
        task['mode'] = 'video'
        self.assertEqual(scene_plan.frame_choice(self.data, 'S4', 11.7)['at'], 11.7)
        self.data['script']['tasks'] = []
        self.assertTrue(11.2 <= scene_plan.frame_choice(self.data, 'S4')['at'] <= 11.7)

    def test_manually_added_undetected_person_is_still_sent_to_image(self):
        self.data['replacements']['scene_plan']['S1'] = {'added':['C2'],'subjects':{'C2':{'on':True}}}
        self.persist()
        draft = pipeline.draft_scene_camera_image(self.pid,'openai','S1',at=1)
        self.assertIn('son',draft['image_ids'])
        self.assertNotIn('THE SON is not in this frame',draft['prompt'])

    def test_all_image_prompt_shapes_follow_source_clothes_and_explicit_override(self):
        for sid, subjects in [('S1', {}), ('S4', {}), ('S4', {'C1': {'on': False}, 'C2': {'on': False}})]:
            with self.subTest(sid=sid, subjects=subjects):
                self.data['replacements']['scene_plan'][sid] = {'frame_at': 11.4 if sid=='S4' else 1, 'frame_mode': 'manual', 'subjects': subjects}
                self.persist()
                draft = pipeline.draft_scene_camera_image(self.pid, 'openai', sid)
                self.assertIn('衣服按参考帧', draft['prompt'])
                self.assertIn('clothing follows the source frame', draft['prompt'])
                system = self.reason.call_args.args[1][0]['content']
                self.assertIn('衣服按参考帧', system)
        self.data['replacements']['characters'][0]['description'] = '换穿红色衬衫'
        self.data['replacements']['scene_plan']['S1'] = {}
        self.persist()
        draft = pipeline.draft_scene_camera_image(self.pid, 'openai', 'S1')
        self.assertIn('换穿红色衬衫', draft['prompt'])
        self.assertIn('explicit clothing instructions take priority', draft['prompt'])

    def clip(self, seconds=4):
        result = self.directory/'clips/generated.mp4'
        self.enterContext(patch.dict(os.environ, {'PATH': str(store.config.ROOT/'runtime/ffmpeg')+os.pathsep+os.environ.get('PATH', '')}))
        media._run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=96x160:r=25',
            '-f', 'lavfi', '-i', 'sine=frequency=400:sample_rate=16000', '-t', str(seconds),
            '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(result)])
        self.data['runs'] = {'seg-03': [{'rev': 'r1', 'state': 'completed', 'result': 'clips/generated.mp4',
                                      'run_dir': 'jimmy-runs/synthetic-r1'}]}
        return result

    def test_assembly_uses_cached_last_word_bounded_by_real_clip(self):
        self.task(); self.clip()
        cache = self.directory/'jimmy-runs/synthetic-r1/motion-check/generated-transcript.json'
        store.atomic_write_json(cache, generated()); self.persist()
        with patch.object(asr, 'transcribe', side_effect=AssertionError('缓存存在不应再转录')):
            result = jimmy_bridge.assemble(self.pid)
        info = media.probe(self.directory/result['final'])
        self.assertAlmostEqual(info['duration'], 4, delta=.06)
        self.assertEqual(info['audio_stream_count'], 1)

    def test_assembly_local_faster_whisper_fallback_and_silent_target(self):
        task = self.task(); self.clip(4.2); self.persist()
        with patch.object(asr, 'transcribe', return_value=generated()) as transcribe:
            result = jimmy_bridge.assemble(self.pid)
        self.assertTrue(transcribe.call_args.kwargs['local_only'])
        self.assertAlmostEqual(media.probe(self.directory/result['final'])['duration'], 4.02, delta=.06)
        task.update(kind='broll', dialogue='', dialogue_lines=[]); self.persist()
        with patch.object(asr, 'transcribe', side_effect=AssertionError('无台词不转录')):
            result = jimmy_bridge.assemble(self.pid)
        self.assertAlmostEqual(media.probe(self.directory/result['final'])['duration'], 3.3, delta=.06)

    def test_speech_read_view_is_capped_and_does_not_transcribe(self):
        task = self.task(); self.clip()
        run = self.data['runs']['seg-03'][0]
        store.atomic_write_json(self.directory/'jimmy-runs/synthetic-r1/motion-check/generated-transcript.json',generated())
        self.persist()
        from console import speech_timing
        with patch.object(asr,'transcribe',side_effect=AssertionError('GET 不转录')):
            timing = speech_timing.for_run(task,run,self.directory)
        self.assertAlmostEqual(timing['extra_seconds'],.7,delta=.03)
        self.assertLessEqual(timing['trim_end'],media.probe(self.directory/run['result'])['duration'])
        self.assertAlmostEqual(timing['lag_seconds'],2.221)

    def test_video_without_audio_still_uses_target(self):
        task = self.task(); self.clip()
        from console import speech_timing
        with patch.object(media,'probe',return_value={'duration':4,'audio_stream_count':0}), \
                patch.object(asr,'transcribe',side_effect=AssertionError('无音轨不转录')):
            timing = speech_timing.for_run(task,self.data['runs']['seg-03'][0],self.directory,transcribe=True)
        self.assertEqual(timing['trim_end'],3.3)

    def test_product_use_is_generic_when_notes_only_describe_color(self):
        task = self.task(); self.english(task)
        self.data['replacements']['product']['description'] = 'black metal'
        text = jimmy_bridge.compose_prompt(self.data,task,self.directory)
        self.assertIn('use, wear or place the product correctly',text)
        self.assertIn('front or functional side facing the camera',text)
        self.assertIn('preserve the source action and real proportions',text)
        self.assertNotIn('for a watch',text)

    def test_role_unknown_and_single_identity_keep_original_identity_wording(self):
        from console import scene_prompt
        task = self.task()
        self.data['breakdown']['characters'][1]['name'] = '访客甲'
        self.assertEqual(scene_prompt.role_names(self.data)['C2'],'PERSON A')
        self.data['replacements']['scene_plan']['S4'] = {'subjects':{'C2':{'on':False}}}
        text = jimmy_bridge.compose_prompt(self.data,task,self.directory)
        self.assertIn('@image2 is the identity reference of the same person: use it only for face, hair and body proportions; ignore its clothing',text)
        self.assertNotIn('talking-head',text)  # Two people remain visible.

    def test_early_and_late_frames_both_send_checked_people_without_absence_rule(self):
        self.task(); self.persist()
        pipeline.draft_scene_camera_image(self.pid,'openai','S4')
        payload = self.reason.call_args.args[1][-1]['content']
        self.assertNotIn('not in this frame',json.dumps(payload,ensure_ascii=False))
        draft = pipeline.draft_scene_camera_image(self.pid,'openai','S4',at=11.4)
        self.assertEqual(draft['image_ids'][:3],['father','son','watch'])
        self.assertNotIn('not in this frame',draft['prompt'])

    def test_clothing_contract_never_forbids_the_default_source_outfit(self):
        task = self.task()
        text = jimmy_bridge.compose_prompt(self.data,task,self.directory)
        self.assertNotIn("actors' faces, hair, clothing",text)
        self.assertIn('explicitly requested clothing changes',text)
        self.data['replacements']['scene_plan']['S4'] = {'subjects':{'C1':{'on':False},'C2':{'on':False}}}
        product_only = jimmy_bridge.compose_prompt(self.data,task,self.directory)
        self.assertNotIn("the original actors' faces or hair",product_only)
        self.assertIn('Keep THE FATHER',product_only)

    def test_supplemental_scene_and_storyboard_references_have_roles(self):
        task = self.task()
        self.data['replacements']['scene_plan']['S4'] = {'subjects':{'C2':{'on':False},'product':{'on':False}}}
        self.data['assets'] += [{'id':'storyboard','type':'image','roles':['storyboard'],'label':'sequence'},
                                {'id':'room','type':'image','roles':['scene'],'label':'new kitchen'}]
        task['image_ids'] = ['main','father','storyboard','room']
        task['scene_references'] = [{'asset_id':'room','scene_id':'S4','start':1.58}]
        text = jimmy_bridge.compose_prompt(self.data,task,self.directory)
        self.assertIn('@image3',text); self.assertIn('storyboard',text)
        self.assertIn('@image4',text); self.assertIn('1.58 s',text)

    def test_preview_keeps_fresh_english_when_effective_mute_is_forced(self):
        task = self.task(); en = self.english(task)
        self.data.pop('action_comparisons')
        self.data['assets'][-1]['sha256'] = store.sha256_file(self.directory/self.data['assets'][-1]['file'])
        self.data['assets'][-1]['scene_camera_binding'] = scene_plan.binding(self.data,'S4',self.directory)
        self.data['script']['replacements_fingerprint'] = pipeline.replacement_guard.fingerprint(self.data,self.directory)
        self.persist()
        def package(args,cwd,timeout=600):
            if args[1]=='prepare':
                spec = json.loads((cwd/'plan.json').read_text())
                spec['tasks'][0]['model'] = jimmy_bridge.MINI_VIDEO
                raw = json.dumps(spec).encode()
                (cwd/'review').mkdir()
                (cwd/'review/jimmy-job.preview.json').write_bytes(raw)
                store.atomic_write_json(cwd/'review/jimmy-handoff.json',{'state':'ready_for_confirmation',
                    'job_sha256':hashlib.sha256(raw).hexdigest()})
            return '{}'
        with patch.object(gateway,'reason_checked',return_value=({'additions':[],'sourceMotionEn':en},
                {'provider':'mock','model':'offline','attempts':[]})), \
                patch.object(media,'cut_clip',side_effect=lambda src,a,b,out,**kw:out.write_bytes(b'clip')), \
                patch.object(media,'probe',return_value={'duration':4,'audio_stream_count':0}), \
                patch.object(jimmy_bridge,'_call',side_effect=package), \
                patch.object(jimmy_bridge.config,'jimmy_configured',return_value=True):
            preview = jimmy_bridge.preview(self.pid,task['id'])
        text = preview['preview']['tasks'][0]['prompt']
        self.assertIn('THE SON enters',text)
        self.assertNotIn('the person from @image',text)
        self.assertIn('sourceMotionEn',store.load(self.pid)['action_comparisons'][task['id']]['comparison'])


if __name__ == '__main__':
    unittest.main()
