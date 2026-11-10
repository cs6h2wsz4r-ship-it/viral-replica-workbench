"""Round 13: read-only incident, mocked models, temporary projects only."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import config, jimmy_bridge, llm, motion, pipeline, prompts, store

PURE = "Delighted disbelief at the start, addressed to the viewer like a friend; stress 'good' with a quick half-smile; confident and certain by 'obsessed'."
META = {'provider': 'deepseek', 'model': 'mock', 'attempts': [{'success': True}]}
LABEL = 'PERFORMANCE DIRECTION (attitude, emphasis and facial expression only; every movement comes from SOURCE MOTION TIMELINE and @video1):'


class PerformanceOnlyTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('禁止读取配置')))
        # Gateway routing reads explicit offline settings; credential parsing stays forbidden.
        self.enterContext(patch.object(config, "load", return_value=dict(config.DEFAULTS)))
        self.enterContext(patch.object(llm, 'chat_json', side_effect=AssertionError('必须 mock 模型')))
        self.checked = self.enterContext(patch.object(llm, 'chat_json_checked', side_effect=AssertionError('必须 mock checked')))
        self.data = json.loads((ROOT / 'tools/fixtures/round13/incident-project.json').read_text())
        self.data['routes'] = {'script': {'channel': 'deepseek', 'model': 'mock'}}
        self.task = self.data['script']['tasks'][0]
        self.task['action'] = json.loads((ROOT / 'tools/fixtures/round13/incident-action.json').read_text())['action']

    def test_real_action_words_and_original_sentences(self):
        hits = motion.performance_hits(self.task)
        words = ' '.join(h['word'].lower() for h in hits)
        for word in ('turn', 'step', 'back', 'walk', 'slide'):
            self.assertIn(word, words)
        for hit in hits:
            self.assertIn(hit['quote'], self.task[hit['field']])
            self.assertIn(hit['word'], hit['quote'])

    def test_decimal_clock_keeps_the_original_sentence(self):
        # Frozen r1 prose has no decimal clock; keep this independent regression explicit.
        action = '1.0–4.7 s she keeps stepping back. Smile warmly.'
        hits = motion.performance_hits({'mode': 'video', 'action': action})
        self.assertTrue(all(h['quote'] in action for h in hits))
        self.assertTrue(any('1.0–4.7 s she keeps stepping back' in h['quote'] for h in hits))

    def test_allowed_faces_quotes_boundaries_and_references(self):
        for text in [PURE, "stress 'good'", 'a quick half-smile', 'eye contact with the lens',
                     "Emphasize 'when I walk'", '说到「转身走近」时微笑',
                     '按时间轴 [6.15–8.1 s] 的动作中强调台词',
                     'face, eyes, smile, brow, gaze; look at the lens',
                     'a warm tone, straightforward delivery, noteworthy stress',
                     "Raise an eyebrow; lifted brows; raises her eyebrows."]:
            with self.subTest(text=text):
                self.assertEqual(motion.performance_hits({'mode': 'video', 'action': text}), [])
        self.assertTrue(motion.performance_hits({'mode': 'video', 'action': "Don't turn away."}))
        self.assertTrue(motion.performance_hits({'mode': 'video', 'action': '“she turns away”', 'dialogue': 'Hello viewer.'}))
        self.assertFalse(motion.performance_hits({'mode': 'image', 'action': 'turn and walk'}))

    def test_all_fields_and_chinese(self):
        for field in ('action', 'story', 'direction'):
            for text in ('转身、走、后退、迈步、侧身、抬手、挥手、点头、摸、撩、靠近、近景、中景',
                         'rotate, stride, lean, raise a hand, lift an arm, gesture, wave, nod, touch, sweep, pose, approach, back away, close-up, mid-shot'):
                with self.subTest(field=field):
                    self.assertTrue(motion.performance_hits({'mode': 'video', field: text}))
        for left, right in [("'", "'"), ('‘', '’'), ('“', '”'), ('「', '」')]:
            self.assertEqual(motion.performance_hits({'mode': 'video', 'dialogue': 'when I walk',
                'action': f'在{left}when I walk{right}上重读'}), [])

    def generate(self, after=PURE, mode='video'):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', folder))
        pid = store.create('第十三轮离线测试')['id']
        store.update(pid, lambda d: d.update(routes={'script': {'channel': 'deepseek', 'model': 'mock'}},
            source={'probe': {'duration': 15}},
            breakdown={'overview': {'language': 'en'}, 'shots': [{'start': 0, 'end': 15, 'action': '原片转身'}]}))
        before = self.task['action']
        result = {'segments': [{'sourceStart': 0, 'sourceEnd': 15, 'kind': 'speaker',
            'useSourceFootage': mode == 'video', 'dialogue': 'good obsessed', 'action': before}]}
        self.checked.side_effect = None
        self.checked.return_value = ({'tasks': [{'id': 'seg-01', 'action': after, 'story': '', 'direction': ''}]}, META)
        with patch.object(llm, 'chat_json', return_value=(result, {'model': 'mock'})):
            pipeline.run_script(pid, 'deepseek', 'rewrite', '', None)
        return pid, store.load(pid)['script']

    def test_generation_cleans_once_and_records_actual_diff(self):
        pid, script = self.generate()
        task = script['tasks'][0]
        self.assertEqual(task['action'], PURE)
        self.assertFalse(motion.performance_hits(task))
        self.checked.assert_called_once()
        self.assertEqual(self.checked.call_args.kwargs['model'], 'mock')
        change = task['performance_cleanup'][0]
        self.assertEqual((change['field'], change['before'], change['after']), ('action', self.task['action'], PURE))
        self.assertEqual(change['provider'], 'deepseek')
        script['tasks'][0]['performance_cleanup'] = [{'before': '伪造记录'}]
        saved = pipeline.save_script(pid, script)
        self.assertEqual(saved['script']['tasks'][0]['performance_cleanup'], [change])

    def test_residual_blocks_preview_before_media_and_manual_save(self):
        pid, script = self.generate('She turns away.')
        self.assertTrue(motion.performance_hits(script['tasks'][0]))
        self.checked.return_value = ({'additions': []}, META)
        with patch.object(jimmy_bridge.media, 'cut_clip') as cut:
            with self.assertRaisesRegex(jimmy_bridge.BridgeError, '动作描述里仍有身体动作：‘She turns away.'):
                jimmy_bridge.preview(pid, 'seg-01')
            cut.assert_not_called()
        before = store.load(pid)['script']
        with self.assertRaisesRegex(ValueError, 'She turns away'):
            pipeline.save_script(pid, copy.deepcopy(script))
        self.assertEqual(store.load(pid)['script'], before)

    def test_manual_all_fields_and_post_mapping_recheck(self):
        pid, script = self.generate()
        for field in ('action', 'story', 'direction'):
            candidate = copy.deepcopy(script)
            candidate['tasks'][0][field] = 'She turns away.'
            with self.assertRaisesRegex(ValueError, 'She turns away'):
                pipeline.save_script(pid, candidate)
        def unsafe_mapping(data, candidate, previous):
            candidate['tasks'][0]['story'] = 'She walks forward.'
        with patch.object(pipeline, 'apply_replacement_safety', side_effect=unsafe_mapping):
            with self.assertRaisesRegex(ValueError, 'She walks forward'):
                pipeline.save_script(pid, copy.deepcopy(script))

    def test_image_generation_never_cleans(self):
        _, script = self.generate(mode='image')
        self.checked.assert_not_called()
        self.assertEqual(script['tasks'][0]['action'], self.task['action'])

    def test_cleanup_all_fields_and_rejects_partial_response_atomically(self):
        tasks = [{'id': 'v1', 'mode': 'video', 'action': 'Turn.', 'story': 'Walk.', 'direction': 'Wave.'},
                 {'id': 'v2', 'mode': 'video', 'direction': '转身'},
                 {'id': 'image', 'mode': 'image', 'story': 'Walk.'}]
        script = {'provider': 'deepseek', 'model': 'mock', 'tasks': tasks}
        snapshot = copy.deepcopy(script)
        self.checked.side_effect = None
        self.checked.return_value = ({'tasks': [{'id': 'v1', 'action': PURE, 'story': '', 'direction': ''}]}, META)
        with self.assertRaisesRegex(ValueError, '表演描述清理'):
            motion.cleanup_performance({}, script)
        self.assertEqual(script, snapshot)
        self.checked.return_value = ({'tasks': [
            {'id': 'v1', 'action': PURE, 'story': '', 'direction': ''},
            {'id': 'v2', 'action': '', 'story': '', 'direction': '轻松地微笑'}]}, META)
        motion.cleanup_performance({}, script)
        self.assertEqual(len(tasks[0]['performance_cleanup']), 3)
        self.assertEqual(tasks[2], snapshot['tasks'][2])
        self.assertFalse(any(motion.performance_hits(t) for t in tasks))

    def test_timeline_last_row_matches_actual_clip(self):
        data = {'source': {'probe': {'duration': 14.9}}, 'breakdown': {'shots': [
            {'start': 0, 'end': 14.7, 'action': '微笑'}]}}
        task = {'mode': 'video', 'source_start': 0, 'source_end': 15.17}
        self.assertEqual(motion.source_timeline(data, task)[-1]['end'], 14.9)

    def test_boundary_tolerance_and_original_error_quote(self):
        rows = [{'start': 0, 'end': 15, 'action': '原片微笑'}]
        self.checked.side_effect = None
        self.checked.return_value = ({'additions': []}, META)
        for end in (15.17, 15.3, 15.6):
            task = {**self.task, 'source_start': 0, 'source_end': 15.17,
                    'action': f'13.7–{end} s: confident smile.'}
            errors, warnings = motion.compare_actions(self.data, task, rows)
            self.assertFalse(warnings)
            sent = json.loads(self.checked.call_args.args[1][1]['content'])
            if end <= 15.3:
                self.assertEqual(sent['action'], '13.7–15 s: confident smile.')
                self.assertFalse(errors)
            else:
                self.assertTrue(errors)
                self.assertIn(task['action'], ' '.join(errors))
        self.assertIn('0.3', self.checked.call_args.args[1][0]['content'])
        self.assertIn('起点或终点', self.checked.call_args.args[1][0]['content'])
        self.assertEqual(motion.normalize_boundary_times('6.15–8.1 s', 15), '6.15–8.1 s')
        self.assertEqual(motion.normalize_boundary_times('在13.7–15.17秒微笑', 15), '在13.7–15秒微笑')
        self.assertEqual(motion.normalize_boundary_times('[-0.17–1 s] smile', 15), '[0–1 s] smile')

    def test_normalized_model_quote_reports_original_and_large_error_survives_failure(self):
        rows = [{'start': 0, 'end': 15, 'action': '原片微笑'}]
        task = {**self.task, 'action': '13.7–15.17 s she walks.'}
        self.checked.side_effect = None
        self.checked.return_value = ({'additions': [{'field': 'action', 'quote': '13.7–15 s she walks.', 'reason': '额外走动'}]}, META)
        errors, warnings = motion.compare_actions(self.data, task, rows)
        self.assertFalse(warnings)
        self.assertIn(task['action'], errors[0])
        task['action'] = '13.7–15.6 s: warm smile.'
        self.checked.side_effect = RuntimeError('mock unavailable')
        errors, warnings = motion.compare_actions(self.data, task, rows)
        self.assertIn('15.6', ' '.join(errors))
        self.assertIn('mock unavailable', ' '.join(warnings))

    def test_video_order_and_image_matches_before_backup(self):
        for kind, field in [('speaker', 'action'), ('broll', 'story'), ('motion', 'direction'), ('camera', 'direction')]:
            task = {**self.task, 'kind': kind, 'action': '', 'story': '', 'direction': '', field: PURE}
            prompt = jimmy_bridge.compose_prompt(self.data, task)
            self.assertLess(prompt.index('SOURCE MOTION TIMELINE:'), prompt.index(LABEL))
            self.assertNotIn('PERFORMANCE AND ACTION DIRECTION:', prompt)
            self.assertNotIn('Let the subject move naturally', prompt)
            self.assertNotIn('Let the camera move naturally', prompt)
        cases = json.loads((ROOT / 'tools/fixtures/round13/image-prompts-before.json').read_text())
        for case in cases:
            self.assertEqual(jimmy_bridge.compose_prompt(case['data'], case['task']), case['prompt'])
        self.assertIn("正例格式：At [<start>–<end> s], emphasize '<quoted source words>' with <attitude or facial expression>.", prompts.SCRIPT_SYSTEM)
        self.assertIn("反例格式：<person> <body movement> on '<quoted source words>'.", prompts.SCRIPT_SYSTEM)
        self.assertNotIn(PURE, prompts.SCRIPT_SYSTEM)
        self.assertNotIn("she turns away on 'The back' and walks toward the lens", prompts.SCRIPT_SYSTEM)


if __name__ == '__main__':
    from tools.run_tests_offline import audit
    sys.addaudithook(audit)
    unittest.main()
