"""24.19: synthetic scene coverage and local audiovisual pacing regressions."""
import copy
import json
import os
import sys
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import gateway, jimmy_bridge, media, pipeline, prompts, store
from tools import test_round24_17 as fixture


def segment(a, b, **fields):
    return {'sourceStart': a, 'sourceEnd': b, 'kind': 'broll', 'useSourceFootage': True,
            'story': f'Emotion {a}', **fields}


class Round24_19Tests(unittest.TestCase):
    setUp = fixture.Round24_17Tests.setUp
    persist = fixture.Round24_17Tests.persist
    frame = staticmethod(fixture.Round24_17Tests.frame)
    camera = fixture.Round24_17Tests.camera

    def generate(self, segments, *, dropped=('S2',), merges=(), transcript=None, mode='keep', attach_motion=False):
        self.data['replacements']['scene_edits'] = {'dropped': list(dropped), 'merges': list(merges)}
        self.persist()
        points = [0, 2.75, 3.833, 5.333, 6.333, 7.5, 9, 10.583, 12.3]
        store.atomic_write_json(self.directory/'analysis/boundaries.json', {
            'merged_shots': [{'start': a, 'end': b} for a, b in zip(points, points[1:])]})
        if transcript:
            store.atomic_write_json(self.directory/'analysis/transcript.raw.json', transcript)
        with patch.object(gateway, 'reason', return_value=({'segments': copy.deepcopy(segments)}, {'model': 'mock'})), \
                patch.object(pipeline, 'apply_replacement_safety'), \
                (nullcontext() if attach_motion else patch.object(pipeline, '_attach_motion_evidence')):
            pipeline.run_script(self.pid, 'openai', mode, '', None)
        return store.load(self.pid)['script']['tasks']

    @staticmethod
    def six():
        return [segment(a, b) for a, b in [(0, 2.75), (2.75, 3.833), (6.333, 7.5),
                                          (7.5, 8.8), (9, 10.583), (10.583, 12.3)]]

    def test_six_model_segments_become_three_complete_scenes(self):
        for sid in ['S1', 'S3', 'S4']:
            self.camera('main-'+sid, sid)
        tasks = self.generate(self.six())
        self.assertEqual([(t['scene_id'], t['source_start'], t['source_end']) for t in tasks],
                         [('S1', 0, 4.8), ('S3', 5.8, 9), ('S4', 9, 12.3)])
        self.assertEqual([t['duration'] for t in tasks], [5, 4, 4])
        for actual, expected in zip([t.get('target_seconds') for t in tasks], [4.8, 3.2, 3.3]):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual([t['image_ids'][0] for t in tasks], ['main-S1', 'main-S3', 'main-S4'])
        self.assertTrue(all(t['split_reason'] == '同一场，镜头切换由参考视频保留' for t in tasks))

    def test_merged_scenes_are_one_complete_segment(self):
        tasks = self.generate(self.six(), dropped=(), merges=(['S2', 'S3'],))
        self.assertEqual([(t['scene_id'], t['source_start'], t['source_end']) for t in tasks],
                         [('S1', 0, 4.8), ('S2', 4.8, 9), ('S4', 9, 12.3)])

    def test_video_crosses_shot_cuts_but_never_scene_boundaries(self):
        tasks = self.generate([segment(0, 12.3)], dropped=())
        self.assertEqual([(t['source_start'], t['source_end']) for t in tasks],
                         [(0, 4.8), (4.8, 5.8), (5.8, 9), (9, 12.3)])

    def test_late_son_same_scene_images_do_not_split(self):
        raw = self.six()
        raw[-1].update(imageIds=['son', 'father', 'watch'], sceneChange=True)
        tasks = self.generate(raw)
        self.assertEqual(len([t for t in tasks if t['scene_id'] == 'S4']), 1)
        self.assertEqual(tasks[-1]['image_ids'], ['father', 'son', 'watch'])

    def test_different_kinds_merge_and_text_stays_in_time_order(self):
        self.data['replacements'].update(characters=[], product={'action': 'keep'})
        raw = [segment(0, 2, kind='broll', story='First story'),
               segment(2, 4.8, kind='speaker', dialogue='Hello', action='Second action')]
        tasks = self.generate(raw[::-1], mode='rewrite')
        task = tasks[0]
        self.assertEqual(task['kind'], 'speaker')
        self.assertEqual(task['story'], 'First story\nEmotion 2')
        self.assertEqual(task['action'], 'Second action')
        self.assertEqual(task['dialogue'], 'Hello')

    def test_no_dialogue_merge_uses_longest_kind(self):
        tasks = self.generate([segment(0, 1, kind='camera'), segment(1, 4.8)])
        self.assertEqual(tasks[0]['kind'], 'broll')

    def test_different_supplemental_references_still_split(self):
        # Create actual additional reference assets, distinct from scene casting photos.
        self.data['assets'] += [{'id': a, 'type': 'image', 'file': f'assets/{a}.png'} for a in ['extra-a', 'extra-b']]
        tasks = self.generate([segment(0, 2, imageIds=['extra-a']), segment(2, 4.8, imageIds=['extra-b'])])
        self.assertEqual([(t['source_start'], t['source_end']) for t in tasks[:2]], [(0, 2), (2, 4.8)])

    def test_scene_longer_than_fifteen_seconds_has_complete_coverage(self):
        self.data['breakdown']['scene'] = [{'id': 'S1', 'timeRanges': [[0, 15.167]]}]
        tasks = self.generate([segment(0, 15.167)], dropped=())
        self.assertEqual(tasks[0]['source_start'], 0)
        self.assertEqual(tasks[-1]['source_end'], 15.167)
        self.assertEqual(len(tasks), 2)
        self.assertTrue(all(t['source_end']-t['source_start'] <= 15 for t in tasks))
        self.assertEqual(tasks[0]['source_end'], tasks[1]['source_start'])

    def test_image_mode_preserves_shot_cuts(self):
        self.data['breakdown']['scene'] = [{'id': 'S1', 'timeRanges': [[0, 4.8]]}]
        tasks = self.generate([segment(0, 4.8, useSourceFootage=False)], dropped=())
        self.assertEqual([(t['source_start'], t['source_end']) for t in tasks], [(0, 2.75), (2.75, 3.833), (3.833, 4.8)])

    def test_gaps_extend_previous_first_and_last_and_recompute_speech(self):
        transcript = {'language': 'en', 'segments': [{'start': a, 'end': b, 'text': word,
            'words': [{'start': a, 'end': b, 'word': word}]} for a, b, word in
            [(0, .1, 'Head'), (1.5, 1.6, 'Middle'), (4.7, 4.8, 'Tail')]]}
        self.data['assets'] += [{'id': a, 'type': 'image', 'file': a+'.png'} for a in ['x', 'y']]
        tasks = self.generate([segment(.25, 1, kind='speaker', imageIds=['x']),
                               segment(2, 4, kind='speaker', imageIds=['y'])], transcript=transcript)
        self.assertEqual([(t['source_start'], t['source_end']) for t in tasks[:2]], [(0, 2), (2, 4.8)])
        self.assertIn('Head', tasks[0]['dialogue']); self.assertIn('Middle', tasks[0]['dialogue'])
        self.assertIn('Tail', tasks[1]['dialogue'])
        self.assertAlmostEqual(tasks[0]['target_seconds'], 2)

    def test_missing_scene_infers_kind_from_breakdown_and_recomputes_dialogue(self):
        self.data['breakdown']['dialogue'] = [{'start': 5.8, 'end': 6.2, 'text': 'Hello', 'speaker': 'C1'}]
        transcript = {'language': 'en', 'segments': [{'start': 5.8, 'end': 6.2, 'text': 'Hello',
                      'words': [{'start': 5.8, 'end': 6.2, 'word': 'Hello'}]}]}
        tasks = self.generate([segment(0, 4.8), segment(9, 12.3)], transcript=transcript)
        missing = next(t for t in tasks if t['scene_id'] == 'S3')
        self.assertEqual((missing['source_start'], missing['source_end'], missing['kind'], missing['mode']), (5.8, 9, 'speaker', 'video'))
        self.assertEqual(missing['split_reason'], '补齐：模型漏掉了这一场')
        self.assertEqual(missing['dialogue'], 'Hello')

    def test_no_model_segments_fills_every_retained_scene(self):
        tasks = self.generate([])
        self.assertEqual([t['scene_id'] for t in tasks], ['S1', 'S3', 'S4'])
        self.assertTrue(all(t['kind'] == 'broll' and t['mode'] == 'video' for t in tasks))

    def test_subsecond_scene_portion_is_not_discarded(self):
        tasks = self.generate([segment(4.65, 5), segment(5.7, 6.1), segment(8.8, 9.1)])
        self.assertEqual([(t['source_start'], t['source_end']) for t in tasks], [(0, 4.8), (5.8, 9), (9, 12.3)])

    def test_speaker_long_estimate_controls_target_and_request(self):
        self.data['replacements'].update(characters=[], product={'action': 'keep'})
        with patch.object(pipeline.evidence, 'estimate_seconds', return_value=6.2):
            tasks = self.generate([segment(0, 4.8, kind='speaker', dialogue='Long words')], mode='rewrite')
        self.assertEqual((tasks[0]['target_seconds'], tasks[0]['duration']), (6.2, 6))

    def test_prompt_explicitly_uses_scene_for_video(self):
        self.assertIn('同一场，镜头切换由参考视频保留', prompts.SCRIPT_SYSTEM)
        self.assertIn('一场', prompts.SCRIPT_SYSTEM)

    def test_keep_speech_kind_wins_over_longer_silent_kind(self):
        transcript = {'language': 'en', 'segments': [{'start': 0, 'end': .8, 'text': 'Hello',
                      'words': [{'start': 0, 'end': .8, 'word': 'Hello'}]}]}
        tasks = self.generate([segment(0, 1, kind='speaker'), segment(1, 4.8)], transcript=transcript)
        self.assertEqual((tasks[0]['kind'], tasks[0]['dialogue']), ('speaker', 'Hello'))

    def test_manual_save_recalculates_target_after_source_and_dialogue_edits(self):
        self.generate([segment(0, 4.8)])
        script = store.load(self.pid)['script']
        task = script['tasks'][0]
        task.update(source_end=2, kind='speaker', dialogue='A longer new line')
        with patch.object(pipeline, 'apply_replacement_safety'), patch.object(pipeline, '_attach_motion_evidence'), \
                patch.object(pipeline.evidence, 'estimate_seconds', return_value=5.5):
            saved = pipeline.save_script(self.pid, script)
        task = saved['script']['tasks'][0]
        self.assertEqual((task['estimate'], task['target_seconds']), (5.5, 5.5))
        task.update(kind='broll', source_end=1.25)
        with patch.object(pipeline, 'apply_replacement_safety'), patch.object(pipeline, '_attach_motion_evidence'):
            saved = pipeline.save_script(self.pid, saved['script'])
        self.assertEqual(saved['script']['tasks'][0]['target_seconds'], 1.25)

    def test_restore_original_speech_recalculates_long_rewrite_target(self):
        self.generate([segment(0, 4.8)])
        script = store.load(self.pid)['script']
        script['tasks'][0].update(kind='speaker', dialogue='A very long adopted rewrite', estimate=10, target_seconds=10)
        store.update(self.pid, lambda data: data.update(script=script))
        with patch.object(pipeline, '_dialogue_source', return_value=[{'original': 'Hello', 'start': 0, 'end': 1}]), \
                patch.object(pipeline, '_attach_motion_evidence'):
            saved = pipeline.keep_original_dialogue(self.pid, 'seg-01')
        self.assertEqual(saved['script']['tasks'][0]['target_seconds'], 4.8)

    def test_disjoint_ranges_never_merge_across_a_dropped_scene(self):
        self.data['breakdown']['scene'] = [{'id': 'S1', 'timeRanges': [[0, 2], [4, 6]]},
                                           {'id': 'S2', 'timeRanges': [[2, 4]]}]
        tasks = self.generate([segment(0, 2), segment(4, 6)])
        self.assertEqual([(t['source_start'], t['source_end']) for t in tasks], [(0, 2), (4, 6)])

    def test_image_short_cross_scene_stays_one_without_a_shot_cut(self):
        self.data['breakdown']['scene'] = [{'id': 'S1', 'timeRanges': [[0, 3]]},
                                           {'id': 'S2', 'timeRanges': [[3, 8]]}]
        parts = pipeline._script_segments([segment(0, 8, useSourceFootage=False)], {}, [], data=self.data)
        self.assertEqual([(p['sourceStart'], p['sourceEnd']) for p in parts], [(0, 8)])

    def test_real_motion_evidence_does_not_restore_words_from_dropped_scene(self):
        transcript = {'language': 'en', 'segments': [{'start': 4, 'end': 6.5, 'text': 'keep DROP after',
            'words': [{'start': 4, 'end': 4.5, 'word': 'keep'}, {'start': 5, 'end': 5.5, 'word': 'DROP'},
                      {'start': 6, 'end': 6.5, 'word': 'after'}]}]}
        tasks = self.generate([segment(0, 4.8, kind='speaker'), segment(5.8, 9, kind='speaker')],
                              transcript=transcript, attach_motion=True)
        self.assertEqual([t['dialogue'] for t in tasks[:2]], ['keep', 'after'])
        self.assertNotIn('DROP', json.dumps(tasks))
        self.assertEqual([t['dialogue_lines'][0]['text'] for t in tasks[:2]], ['keep', 'after'])
        self.assertAlmostEqual(tasks[1]['target_seconds'], 3.2)

    def test_long_speaker_requests_do_not_merge_beyond_fifteen_seconds(self):
        self.data['breakdown']['scene'] = [{'id': 'S1', 'timeRanges': [[0, 10]]}]
        self.data['replacements'].update(characters=[], product={'action': 'keep'})
        for raw in [[segment(0, 5, kind='speaker', dialogue='好'*50), segment(5, 10, kind='speaker', dialogue='好'*50)],
                    [segment(0, 10, kind='speaker', dialogue='好'*100)]]:
            with self.subTest(count=len(raw)):
                tasks = self.generate(raw, dropped=(), mode='rewrite')
                self.assertEqual(len(tasks), 2)
                self.assertTrue(all(t['estimate'] <= 15 and t['target_seconds'] <= 15 for t in tasks))
                self.assertEqual(''.join(t['dialogue'] for t in tasks), '好'*100)

    def test_long_srt_sentence_without_words_fails_clearly_instead_of_losing_speech(self):
        self.data['breakdown']['scene'] = [{'id': 'S1', 'timeRanges': [[0, 10]]}]
        transcript = {'language': 'zh', 'segments': [{'start': 0, 'end': 10, 'text': '好'*100, 'words': []}]}
        with self.assertRaisesRegex(ValueError, '逐词'):
            self.generate([segment(0, 10, kind='speaker')], dropped=(), transcript=transcript, attach_motion=True)

    def test_word_exactly_straddling_automatic_cut_is_owned_once(self):
        self.data['breakdown']['scene'] = [{'id': 'S1', 'timeRanges': [[0, 10]]}]
        words = [{'start': i*.098, 'end': (i+1)*.098, 'word': '好'} for i in range(50)]
        words += [{'start': 4.9, 'end': 5.1, 'word': '中'}]
        words += [{'start': 5.1+i*.098, 'end': 5.1+(i+1)*.098, 'word': '棒'} for i in range(50)]
        text = '好'*50+'中'+'棒'*50
        transcript = {'language': 'zh', 'segments': [{'start': 0, 'end': 10, 'text': text, 'words': words}]}
        tasks = self.generate([segment(0, 10, kind='speaker')], dropped=(), transcript=transcript, attach_motion=True)
        self.assertEqual(''.join(t['dialogue'] for t in tasks), text)
        self.assertEqual(sum(t['dialogue'].count('中') for t in tasks), 1)

    def test_save_keeps_manual_structured_dialogue_with_real_evidence(self):
        transcript = {'language': 'en', 'segments': [{'start': 0, 'end': 1, 'text': 'Hello',
                      'words': [{'start': 0, 'end': 1, 'word': 'Hello'}]}]}
        self.generate([segment(0, 4.8, kind='speaker')], transcript=transcript, attach_motion=True)
        script = store.load(self.pid)['script']
        task = script['tasks'][0]
        task['dialogue'] = 'Goodbye'; task['dialogue_lines'][0]['text'] = 'Goodbye'
        with patch.object(pipeline, 'apply_replacement_safety'):
            saved = pipeline.save_script(self.pid, script)
        self.assertEqual(saved['script']['tasks'][0]['dialogue'], 'Goodbye')
        self.assertEqual(saved['script']['tasks'][0]['dialogue_windows'][0]['text'], 'Goodbye')

    def test_source_endpoints_keep_all_decimal_precision(self):
        start, end = .1234567, 4.8123456
        self.data['breakdown']['scene'] = [{'id': 'S1', 'timeRanges': [[start, end]]}]
        task = self.generate([segment(1, 3)], dropped=())[0]
        self.assertEqual((task['source_start'], task['source_end']), (start, end))
        self.assertEqual(task['target_seconds'], end-start)


class Round24_19AssemblyTests(unittest.TestCase):
    setUp = fixture.Round24_17Tests.setUp
    persist = fixture.Round24_17Tests.persist
    frame = staticmethod(fixture.Round24_17Tests.frame)

    def source_clip(self, seconds=4, audio=True):
        self.enterContext(patch.dict(os.environ, {'PATH': str(ROOT/'runtime/ffmpeg')+os.pathsep+os.environ.get('PATH', '')}))
        clip = self.directory/'assets/generated.mp4'
        cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=64x96:r=25']
        if audio:
            cmd += ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100']
        media._run(cmd+['-t', str(seconds), '-c:v', 'libx264', '-pix_fmt', 'yuv420p']+
                   (['-c:a', 'aac'] if audio else [])+[str(clip)])
        self.data['script'] = {'tasks': [{'id': 'seg-01', 'order': 1, 'title': '自造视频', 'kind': 'broll',
            'source_start': 2.75, 'source_end': 3.83, 'duration': 4, 'target_seconds': 1.08}]}
        self.data['runs'] = {'seg-01': [{'rev': 'r1', 'state': 'completed', 'result': 'assets/generated.mp4'}]}
        self.persist()

    def test_real_trim_cuts_both_video_and_audio_to_target(self):
        self.source_clip()
        result = jimmy_bridge.assemble(self.pid)
        out = self.directory/result['final']
        self.assertAlmostEqual(media.probe(out)['duration'], 1.08, delta=.08)
        raw = json.loads(media._run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(out)]).stdout)
        durations = {s['codec_type']: float(s['duration']) for s in raw['streams']}
        starts = {s['codec_type']: float(s.get('start_time') or 0) for s in raw['streams']}
        self.assertAlmostEqual(durations['video'], durations['audio'], delta=.06)
        self.assertAlmostEqual(durations['audio'], 1.08, delta=.06)
        self.assertAlmostEqual(starts['video'], starts['audio'], delta=.05)
        (ROOT/'runtime/temp/round24_19-trim-evidence.json').write_text(json.dumps({
            'input_seconds': 4, 'target_seconds': 1.08, 'output_seconds': media.probe(out)['duration'],
            'streams_duration': durations, 'streams_start': starts}, indent=2))

    def test_unchecked_option_persists_and_concats_full_clip(self):
        self.source_clip()
        jimmy_bridge.save_assembly_options(self.pid, {'trim_to_source': False})
        self.assertFalse(store.load(self.pid)['assembly_options']['trim_to_source'])
        result = jimmy_bridge.assemble(self.pid)
        self.assertAlmostEqual(media.probe(self.directory/result['final'])['duration'], 4, delta=.08)

    def test_generated_clip_shorter_than_target_uses_full_length(self):
        self.source_clip(seconds=.6)
        result = jimmy_bridge.assemble(self.pid)
        self.assertAlmostEqual(media.probe(self.directory/result['final'])['duration'], .6, delta=.08)

    def test_silent_clip_trim_adds_matching_silence(self):
        self.source_clip(audio=False)
        result = jimmy_bridge.assemble(self.pid)
        info = media.probe(self.directory/result['final'])
        self.assertEqual(info['audio_stream_count'], 1)
        self.assertAlmostEqual(info['duration'], 1.08, delta=.08)

    def test_old_scripts_infer_source_or_speaker_estimate(self):
        self.source_clip()
        task = self.data['script']['tasks'][0]
        task.pop('target_seconds')
        for kind, estimate, expected in [('broll', 3, 1.08), ('speaker', 2, 1.08), ('speaker', 2.5, 2.5)]:
            with self.subTest(kind=kind, estimate=estimate):
                task.update(kind=kind, estimate=estimate)
                self.persist()
                result = jimmy_bridge.assemble(self.pid)
                self.assertAlmostEqual(media.probe(self.directory/result['final'])['duration'], expected, delta=.08)

    def test_empty_script_has_readable_error(self):
        self.data['script'] = {'tasks': []}; self.persist()
        with self.assertRaisesRegex(jimmy_bridge.BridgeError, '没有可拼接'):
            jimmy_bridge.assemble(self.pid)

    def test_options_reject_non_boolean_without_mutation(self):
        before = (self.directory/'project.json').read_bytes()
        with self.assertRaisesRegex(ValueError, '勾选'):
            jimmy_bridge.save_assembly_options(self.pid, {'trim_to_source': 'false'})
        self.assertEqual((self.directory/'project.json').read_bytes(), before)

    def test_save_option_http_route_without_server_or_network(self):
        from types import SimpleNamespace
        import server
        status = server.build_status()
        handler = SimpleNamespace(headers={'X-Client-Build': status['build']},
                                  _body_json=lambda: {'trim_to_source': False})
        handler._project_api = lambda *args: server.Handler._project_api(handler, *args)
        saved = server.Handler._api(handler, 'PUT', ['projects', self.pid, 'assembly-options'], {})
        self.assertFalse(saved['assembly_options']['trim_to_source'])


if __name__ == '__main__':
    unittest.main()
