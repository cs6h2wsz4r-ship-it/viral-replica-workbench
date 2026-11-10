"""24.11: offline defaults, real dialogue anchors and motion search boundaries."""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import asr, config, gateway, jimmy_bridge, jobs, media, motion, pipeline, replacement_guard as guard, store

LINES = [dict(start=a, end=b, text=text) for a, b, text in [
    (.162, 1.979, 'Excuse me, your skirt is beautiful.'),
    (2.782, 3.937, 'Oh, thank you so much.'),
    (5.201, 6.56, "It's knit and comfortable."),
    (6.76, 7.578, 'I love the skirt.'),
    (8.14, 9.72, 'The fabric is perfect for this weather.')]]


class Round24_11Tests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.folder))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('禁止读取密钥')))
        self.enterContext(patch.object(config, 'load', return_value=dict(config.DEFAULTS)))
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('禁止真实请求')))

    def test_missing_or_false_caption_flag_skips_masks_but_saved_true_and_stills_keep_them(self):
        from tools.test_round24_10 import EXPECTED
        from tools.project_fixture import load_project
        data = load_project('p1028222359-9b21')
        task = copy.deepcopy(data['script']['tasks'][0])
        task.update(mode='video');task.pop('blank_captions', None)
        for flag in (None, False):
            if flag is not None: task['blank_captions'] = flag
            self.assertEqual(guard.caption_masks(data, task), ([], False))
        task['blank_captions'] = True
        masks, missing = guard.caption_masks(data, task)
        self.assertFalse(missing)
        self.assertEqual([(m['bbox'], m['start'], m['end']) for m in masks], EXPECTED)
        frames, _ = guard.frame_caption_masks(data, 5.8)
        self.assertEqual([m['bbox'] for m in frames], [[0, .71, .46, .92]])

    def test_save_defaults_missing_to_false_and_keeps_explicit_true(self):
        pid = store.create('24.11 保存默认')['id']
        tasks = [dict(id=f'seg-{i}', kind='broll', mode='video', source_start=0, source_end=4,
                      duration=4, ratio='9:16', image_ids=[], action='', story='') for i in range(3)]
        tasks[1]['blank_captions'] = True;tasks[2]['blank_captions'] = False
        store.update(pid, lambda d: d.update(script={'tasks': copy.deepcopy(tasks)}))
        with patch.object(pipeline, 'apply_replacement_safety'), patch.object(motion, 'cleanup_performance'):
            pipeline.save_script(pid, {'tasks': tasks})
        self.assertEqual([t['blank_captions'] for t in store.load(pid)['script']['tasks']], [False, True, False])

    def test_generated_video_task_defaults_to_false(self):
        pid = store.create('24.11 新脚本默认')['id']
        store.update(pid, lambda d: d.update(breakdown={'overview': {'language': 'en'}},
                                            source={'probe': {'ratio_hint': '9:16'}}))
        answer = {'segments': [{'kind': 'broll', 'mode': 'video', 'sourceStart': 0, 'sourceEnd': 4}]}
        with patch.object(gateway, 'reason', return_value=(answer, {'model': 'offline'})), \
                patch.object(pipeline, 'apply_replacement_safety'), patch.object(motion, 'cleanup_performance'):
            pipeline.run_script(pid, 'openai', 'keep', '', None)
        tasks = store.load(pid)['script']['tasks']
        self.assertEqual(len(tasks), 1);self.assertEqual(tasks[0]['mode'], 'video')
        self.assertIs(tasks[0]['blank_captions'], False)

    def test_five_rewritten_lines_use_absolute_source_times_and_no_original_words(self):
        source = {'segments': [{**l, 'text': t} for l, t in zip(LINES, [
            'Excuse me, your dress is beautiful.', 'Oh, thank you so much.',
            "It's so flowy and comfortable.", 'I love the open back.', LINES[-1]['text']])]}
        # Offset the entire segment: dialogue line times remain source absolute.
        lines = [{**l, 'start': l['start'] + 20, 'end': l['end'] + 20} for l in LINES]
        generated = {'segments': [{**l, 'start': l['start'] - .04} for l in LINES]}
        rows, warnings = media._speech_lags(source, generated, 20, 30.052, dialogue_lines=lines)
        self.assertEqual(warnings, [])
        self.assertEqual([r['text'] for r in rows], [l['text'] for l in LINES])
        self.assertEqual([r['lag_seconds'] for r in rows], [-.04] * 5)
        self.assertEqual(rows[2]['source_start'], 25.201)
        self.assertEqual(rows[2]['expected_start'], 5.201)
        fallback, _ = media._speech_lags({'segments': LINES}, generated, 0, 10.052, dialogue_lines=[])
        self.assertEqual(len(fallback), 5)

    def test_boundary_is_inconclusive_and_interior_keeps_original_thresholds(self):
        energy = np.random.default_rng(2411).random(150)
        generated = {'segments': copy.deepcopy(LINES)}
        for offset, expected in [(-20, 'inconclusive'), (20, 'inconclusive'),
                                 (-19, 'inconclusive'), (19, 'inconclusive'),
                                 (-3, 'aligned'), (-6, 'misaligned')]:
            with self.subTest(offset=offset), patch.object(media, '_motion_energy', side_effect=[energy, np.roll(energy, offset)]), \
                    patch.object(media, 'extract_audio_wav'), patch.object(asr, 'transcribe_best', return_value=generated):
                report = media.motion_alignment(Path('result'), Path('source'), 0, 10.052,
                                                {'segments': LINES}, self.folder / 'check', dialogue_lines=LINES)
                self.assertEqual(report['status'], expected)
                self.assertEqual(report['motion_lag_seconds'], offset / 10)
                self.assertEqual(report['thresholds'], {'max_lag_seconds': .5, 'min_correlation': .45})
                self.assertEqual(report['speech_reference'], 'dialogue_lines')
                if abs(offset) >= 19:
                    self.assertEqual(report['motion_status'], 'inconclusive')
                    self.assertTrue(any('这一项不作判断' in w for w in report['warnings']))
                    self.assertFalse(any('提前约' in w or '延后约' in w for w in report['warnings']))

    def test_recorded_r2_asr_matches_all_five_current_lines_without_model_requests(self):
        folder = ROOT / 'tools/fixtures/round24_11'
        manifest = json.loads((folder / 'r2-evidence-manifest.json').read_text())
        for file, digest in manifest['fixture_sha256'].items():
            self.assertEqual(hashlib.sha256((folder / file).read_bytes()).hexdigest(), digest)
        saved = json.loads((folder / 'r2-motion-report.json').read_text())
        generated = json.loads((folder / 'r2-generated-transcript.json').read_text())
        rows, warnings = media._speech_lags(saved['run']['motion_source']['transcript'], generated,
                                          0, 10.052, dialogue_lines=saved['task']['dialogue_lines'])
        self.assertEqual(warnings, [])
        self.assertEqual([r['text'] for r in rows], [l['text'] for l in LINES])
        self.assertEqual([r['lag_seconds'] for r in rows], [-.081, -.622, -1.121, -.439, -.04])

    def test_unchecked_preview_passes_no_masks_and_does_not_run_residue_detection(self):
        from tools.test_replacement_guard import ReplacementGuardTests
        pid, directory, data = ReplacementGuardTests.project(self)
        store.update(pid, lambda d: d['breakdown'].update(overlays=[
            {'kind': 'caption', 'start': 0, 'end': 2, 'bbox': [0, .6, .4, .8]}]))
        def package(args, cwd):
            store.atomic_write_json(cwd / 'review/jimmy-job.preview.json', {'tasks': [{'id': 'seg-01', 'model': jimmy_bridge.MINI_VIDEO}]})
            store.atomic_write_json(cwd / 'review/jimmy-handoff.json', {'job_sha256': 'offline'})
        for flag in (None, False):
            def change(d):
                t = d['script']['tasks'][0]
                if flag is None: t.pop('blank_captions', None)
                else: t['blank_captions'] = flag
            store.update(pid, change)
            with patch.object(media, 'cut_clip', side_effect=lambda src, a, b, out, **kw: out.write_bytes(b'clip')) as cut, \
                    patch.object(media, 'probe', return_value={'duration': 3}), \
                    patch.object(media, 'caption_residue', side_effect=AssertionError('未勾选不得检测残留')) as residue, \
                    patch.object(jimmy_bridge, '_call', side_effect=package):
                result = jimmy_bridge.preview(pid, 'seg-01')
                self.assertEqual(cut.call_args.kwargs['masks'], [])
                residue.assert_not_called()
                self.assertIs(result['clip_processing']['blank_captions'], False)

    def test_background_check_receives_current_lines_without_recomputing_completed_reports(self):
        pid = store.create('24.11 检查台词')['id']; directory = store.project_dir(pid)
        (directory / 'source.mp4').write_bytes(b'source');(directory / 'result.mp4').write_bytes(b'result')
        run = {'rev': 'r2', 'state': 'completed', 'mode': 'video', 'result': 'result.mp4',
               'source_range': [0, 10.052], 'run_dir': 'run',
               'motion_source': {'file': 'source.mp4', 'transcript': {'segments': []}}}
        store.update(pid, lambda d: d.update(script={'tasks': [{'id': 'seg-01', 'dialogue_lines': LINES}]}, runs={'seg-01': [run]}))
        def start(pid, key, fn, label): fn()
        with patch.object(jobs, 'start', side_effect=start), \
                patch.object(media, 'motion_alignment', return_value={'status': 'aligned'}) as calculate:
            jimmy_bridge._schedule_motion_check(pid, 'seg-01', 'r2')
            self.assertEqual(calculate.call_args.kwargs.get('dialogue_lines'), LINES)
            jimmy_bridge._schedule_motion_check(pid, 'seg-01', 'r2')
            calculate.assert_called_once()


if __name__ == '__main__':
    from tools.run_tests_offline import audit
    sys.addaudithook(audit)
    unittest.main()
