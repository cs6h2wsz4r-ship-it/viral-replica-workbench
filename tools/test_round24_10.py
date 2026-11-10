"""24.10: per-overlay caption rectangles, timing and derived-media expiry, offline."""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from console import config, jimmy_bridge, llm, media, motion, pipeline, store
from console import replacement_guard as guard
from tools.project_fixture import load_project
from tools import test_replacement_guard as fixtures

EXPECTED = [
    ([0, .55, .23, .72], 0, 2.93),
    ([0, .53, .26, .69], 2.63, 5.35),
    ([0, .71, .46, .92], 5.05, 6.91),
    ([0, .56, .31, .72], 6.61, 8.29),
    ([0, .55, .41, .72], 7.99, 10.052),
]


class CaptionMaskTests(unittest.TestCase):
    def setUp(self):
        self.data = load_project('p1028222359-9b21')
        self.task = self.data['script']['tasks'][0]
        self.task.update(mode='video', blank_captions=True)

    def assertBox(self, actual, expected):
        self.assertEqual(len(actual), 4)
        for a, b in zip(actual, expected):
            self.assertAlmostEqual(a, b)

    def test_five_subtitles_keep_their_own_boxes_and_windows(self):
        masks, missing = guard.caption_masks(self.data, self.task)
        self.assertFalse(missing)
        self.assertEqual(len(masks), 5)
        for mask, overlay, (box, start, end) in zip(masks, self.data['breakdown']['overlays'], EXPECTED):
            self.assertBox(mask['bbox'], box)
            self.assertEqual((mask['start'], mask['end']), (start, end))
            self.assertEqual(mask['source_bboxes'], [overlay['bbox']])
            self.assertEqual(mask['text'], overlay['text'])
            self.assertFalse(mask.get('motion_warning'))
            self.assertFalse(mask.get('height_limit_reason'))
        self.assertLessEqual(masks[2]['bbox'][1], .75)
        self.assertGreaterEqual(masks[2]['bbox'][3], .88)

    def test_every_instant_masks_only_current_padded_overlays(self):
        masks, _ = guard.caption_masks(self.data, self.task)
        # Include every transition and its immediate neighbours, plus a dense grid.
        times = [n / 100 for n in range(1006)]
        times += [t + offset for _, a, b in EXPECTED for t in (a, b) for offset in (-.001, 0, .001)]
        for at in times:
            active = [m for m in masks if m['start'] <= at <= m['end']]
            expected = [box for box, a, b in EXPECTED if a <= at <= b]
            self.assertEqual(len(active), len(expected), f'at={at}')
            for mask, box in zip(active, expected):
                self.assertBox(mask['bbox'], box)

    def test_segment_relative_clipping_and_fifteen_second_limit(self):
        masks, _ = guard.caption_masks(self.data, {**self.task, 'source_start': 5.2, 'source_end': 6.76})
        self.assertEqual([(m['start'], m['end']) for m in masks], [(0, .15), (0, 1.56), (1.41, 1.56)])
        for m, expected in zip(masks, EXPECTED[1:4]):
            self.assertBox(m['bbox'], expected[0])
        data = {'breakdown': {'overlays': [{'kind': 'caption', 'start': 0, 'end': 30, 'bbox': [0, 0, 1, 1]}]}}
        self.assertEqual(guard.caption_masks(data, {**self.task, 'source_start': 2, 'source_end': 30})[0][0]['end'], 15)

    def test_frame_masks_only_use_subtitles_at_the_sample_time(self):
        for at, indexes in [(1, [0]), (3.36, [1]), (5.2, [1, 2]), (6, [2]), (7.5, [3]), (9, [4]), (10.3, [])]:
            masks, missing = guard.frame_caption_masks(self.data, at)
            self.assertFalse(missing)
            self.assertEqual(len(masks), len(indexes), f'at={at}')
            for m, i in zip(masks, indexes):
                self.assertBox(m['bbox'], EXPECTED[i][0])

    def test_frame_sampling_does_not_include_a_caption_just_after_the_instant(self):
        data = {'breakdown': {'overlays': [{'kind': 'caption', 'start': 1, 'end': 2, 'bbox': [.1, .5, .3, .6]}]}}
        for at, expected in ((.8499, False), (.85, True), (2.1499, True), (2.15, False)):
            self.assertEqual(bool(guard.frame_caption_masks(data, at)[0]), expected, f'at={at}')

    def test_only_large_individual_boxes_warn_without_repositioning(self):
        self.data['breakdown']['overlays'] = [
            {'kind': 'caption', 'start': 1, 'end': 2, 'bbox': [.2, .4, .6, .8]},
            {'kind': 'caption', 'start': 3, 'end': 4, 'bbox': [.2, .1, .6, .37]},
            {'kind': 'sticker', 'start': 5, 'end': 6, 'bbox': [.01, .01, .99, .99]},
        ]
        masks, _ = guard.caption_masks(self.data, self.task)
        self.assertBox(masks[0]['bbox'], [.17, .36, .63, .84])
        self.assertEqual(masks[0]['motion_warning'], '这段字幕很大（覆盖画面高度 36–84%），这段时间里下半身动作的参考会变弱')
        self.assertNotIn('motion_warning', masks[1], 'exactly 0.35 high must not warn')
        self.assertBox(masks[2]['bbox'], [0, 0, 1, 1])
        self.assertIn('0–100%', masks[2]['motion_warning'])

    def test_opt_out_and_missing_bbox_keep_existing_meaning(self):
        self.data['breakdown']['overlays'].append({'kind': 'caption', 'start': 0, 'end': 2})
        self.assertTrue(guard.caption_masks(self.data, self.task)[1])
        self.assertEqual(guard.caption_masks(self.data, {**self.task, 'blank_captions': False}), ([], False))
        self.assertEqual(guard.caption_masks(self.data, {**self.task, 'mode': 'image'}), ([], False))

    def test_ffmpeg_filter_has_each_rectangles_own_enable(self):
        masks, _ = guard.caption_masks(self.data, self.task)
        with tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp') as tmp, \
                patch.object(media, 'probe', return_value={'width': 720, 'height': 1280}), \
                patch.object(media, '_run') as run:
            media.cut_clip(Path(tmp) / 'source.mp4', 0, 10.052, Path(tmp) / 'out.mp4', mute=True, masks=masks)
        command = run.call_args.args[0]
        filters = command[command.index('-vf') + 1]
        self.assertEqual(filters.count('delogo='), 5)
        self.assertEqual(filters.count(':enable='), 5)
        for box, a, b in EXPECTED:
            self.assertIn(media._delogo_filter(box, 720, 1280) + f":enable='between(t,{a:.3f},{b:.3f})'", filters)
        self.assertIn('-an', command)


class CaptionCacheTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('禁止配置读取')))
        self.enterContext(patch.object(config, 'load', return_value=dict(config.DEFAULTS)))
        self.enterContext(patch.object(llm, 'chat_json', side_effect=AssertionError('禁止真实模型请求')))
        self.pid, self.directory, self.data = fixtures.ReplacementGuardTests.project(self)
        self.overlays = load_project('p1028222359-9b21')['breakdown']['overlays']
        store.update(self.pid, lambda d: d['breakdown'].update(overlays=self.overlays))
        store.update(self.pid, lambda d: d['script']['tasks'][0].update(source_end=10.052))
        self.data = store.load(self.pid)
        self.task = self.data['script']['tasks'][0]

    def legacy_fingerprint(self):
        data, task, directory = self.data, self.task, self.directory
        values = [guard.fingerprint(data, directory), data.get('source'), data.get('breakdown'),
            data['script'].get('dialogue_mode'),
            {k: data['script'].get(k) for k in ('provider', 'model', 'request_model')},
            {k: (data.get('analysis') or {}).get(k) for k in ('provider', 'model', 'request_model')},
            motion.read_transcript(directory),
            {k: v for k, v in task.items() if k not in {'chosen_rev', 'replacement_safety', 'replacement_safety_key', 'replacement_safety_check'}}]
        return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def test_old_pending_preview_is_rejected_before_any_submission(self):
        old = {'rev': 'r1', 'state': 'pending_confirmation', 'job_sha256': 'old-sha',
            'preflight_fingerprint': self.legacy_fingerprint(), 'clip': 'clips/old-band.mp4'}
        store.update(self.pid, lambda d: d.update(runs={'seg-01': [old]}))
        with patch.object(jimmy_bridge, '_call', side_effect=AssertionError('禁止提交')), \
                self.assertRaisesRegex(jimmy_bridge.BridgeError, '请重新生成预览'):
            jimmy_bridge.confirm(self.pid, 'seg-01', 'r1', 'old-sha')

    def test_calculated_mask_changes_expire_preview_even_without_overlay_edits(self):
        before = jimmy_bridge.preflight_fingerprint(self.data, self.task, self.directory)
        masks, missing = guard.caption_masks(self.data, self.task)
        changed = copy.deepcopy(masks)
        changed[0]['bbox'][2] += .01
        for variant in (changed, [{**m, 'end': m['end'] - .01} for m in masks]):
            with patch.object(guard, 'caption_masks', return_value=(variant, missing)):
                self.assertNotEqual(before, jimmy_bridge.preflight_fingerprint(self.data, self.task, self.directory))
                with self.assertRaisesRegex(jimmy_bridge.BridgeError, '请重新生成预览'):
                    jimmy_bridge.verify_preview_current(self.data, self.task, {'preflight_fingerprint': before}, self.directory)

    def test_new_preview_rebuilds_clip_and_supersedes_old_band(self):
        old = {'rev': 'r1', 'state': 'pending_confirmation', 'clip': 'clips/old-band.mp4'}
        (self.directory / old['clip']).write_bytes(b'old union band')
        store.update(self.pid, lambda d: d.update(runs={'seg-01': [old]}))
        def cut(src, start, length, out, **kwargs):
            self.assertEqual(len(kwargs['masks']), 5)
            out.write_bytes(b'new individual masks')
        def package(args, cwd):
            store.atomic_write_json(cwd / 'review/jimmy-job.preview.json', {'tasks': [{'id': 'seg-01', 'model': jimmy_bridge.MINI_VIDEO}]})
            store.atomic_write_json(cwd / 'review/jimmy-handoff.json', {'job_sha256': 'mock'})
        with patch.object(media, 'cut_clip', side_effect=cut) as cutting, \
                patch.object(media, 'probe', return_value={'duration': 10.052}), \
                patch.object(media, 'caption_residue', return_value={'status': 'checked', 'suspected': []}), \
                patch.object(motion, 'compare_actions', return_value=([], [])), \
                patch.object(jimmy_bridge, '_call', side_effect=package):
            record = jimmy_bridge.preview(self.pid, 'seg-01')
        self.assertEqual(cutting.call_count, 1)
        self.assertNotEqual(record['clip'], old['clip'])
        self.assertEqual((self.directory / record['clip']).read_bytes(), b'new individual masks')
        self.assertEqual(store.load(self.pid)['runs']['seg-01'][0]['state'], 'superseded')
        self.assertEqual(len(record['clip_processing']['masks']), 5)

    def test_clean_frame_cache_uses_only_current_boxes_and_expires_on_change(self):
        frame = {'id': 'frame', 'file': 'assets/frame.jpg'}
        (self.directory / frame['file']).write_bytes(b'original')
        def clean(path, masks, out):
            out.write_bytes(json.dumps([m['bbox'] for m in masks]).encode())
            return out
        with patch.object(media, 'delogo_frame', side_effect=clean) as cleaning, \
                patch.object(media, 'caption_residue', return_value={'status': 'checked', 'suspected': []}):
            first, _ = pipeline._clean_camera_frame(self.pid, self.data, frame, 3.36)
            again, _ = pipeline._clean_camera_frame(self.pid, self.data, frame, 3.36)
            changed = copy.deepcopy(self.data)
            changed['breakdown']['overlays'][1]['bbox'][2] += .02
            new, _ = pipeline._clean_camera_frame(self.pid, changed, frame, 3.36)
        self.assertEqual(len(first['masks']), 1)
        for actual, expected in zip(first['masks'][0]['bbox'], EXPECTED[1][0]):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(first['id'], again['id'])
        self.assertNotEqual(first['caption_clean_key'], new['caption_clean_key'])
        self.assertNotEqual(first['id'], new['id'])
        self.assertEqual(cleaning.call_count, 2)


if __name__ == '__main__':
    unittest.main()
