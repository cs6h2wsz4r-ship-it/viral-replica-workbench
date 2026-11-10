"""24.17b regressions: self-made scenes only; no model or real project I/O."""
import copy
import unittest
from unittest.mock import patch

from console import pipeline, scene_plan, store
from tools.test_scene_plan_24_17 import example
from tools import test_round24_17 as fixtures


def sample():
    data = example()
    data['breakdown']['shots'][-2]['end'] = 10.583
    data['breakdown']['shots'][-1].update(start=10.583, end=11.18)
    data['breakdown']['shots'].append({**data['breakdown']['shots'][-1], 'start':11.18, 'end':12.3})
    data['analysis'] = {'shots':[
        {'start':0, 'end':1.5, 'frame_at':.75, 'frame':'analysis/frames/shot-001.jpg'},
        {'start':1.5, 'end':4.6, 'frame_at':3, 'frame':'analysis/frames/shot-002.jpg'},
        {'start':4.6, 'end':6, 'frame_at':5.3, 'frame':'analysis/frames/shot-003.jpg'},
        {'start':6, 'end':9, 'frame_at':7.5, 'frame':'analysis/frames/shot-004.jpg'},
        {'start':9, 'end':10.583, 'frame_at':9.8, 'frame':'analysis/frames/shot-007.jpg'},
        {'start':10.583, 'end':12.3, 'frame_at':11.4415, 'frame':'analysis/frames/shot-008.jpg'}]}
    return data


class SceneAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.data = sample()

    def test_auto_merges_contiguous_full_coverage_and_aligns_analysis_time(self):
        self.data['script'] = {'tasks':[{'scene_id':'S4','source_start':9,'source_end':12.3,'mode':'video'}]}
        frame = scene_plan.frame_choice(self.data, 'S4')
        self.assertAlmostEqual(frame['at'], 11.4415)
        self.assertTrue(11.2 <= frame['at'] <= 11.7)
        self.assertIn('父亲、儿子都在画面里', frame['reason'])
        self.assertIn('儿子 10.58 秒后进画面', frame['reason'])

    def test_caption_adjustment_stays_near_middle_and_within_full_coverage(self):
        self.data['script'] = {'tasks':[{'scene_id':'S4','source_start':9,'source_end':12.3,'mode':'video'}]}
        def masks(data, at):
            return ([{'bbox':[0, 0, 1, .5]}], False) if at < 11.6 else ([], False)
        with patch.object(scene_plan._guard(), 'frame_caption_masks', side_effect=masks):
            at = scene_plan.frame_choice(self.data, 'S4')['at']
        self.assertGreaterEqual(at, 11.6)
        self.assertLessEqual(abs(at - 11.4415), .30001)
        self.assertEqual(scene_plan.frame_choice(self.data, 'S4', 10.9)['at'], 10.9)

    def test_entry_thumbnail_cannot_pull_auto_frame_away_from_middle(self):
        self.data['script'] = {'tasks':[{'scene_id':'S4','source_start':9,'source_end':12.3,'mode':'video'}]}
        self.data['analysis']['shots'][-1].update(end=11.18, frame_at=10.8815)
        self.assertTrue(11.2 <= scene_plan.frame_choice(self.data, 'S4')['at'] <= 11.7)

    def test_old_auto_frames_recalculate_read_only_but_manual_frames_stay(self):
        for reason in ('scene_auto', 'manual'):
            with self.subTest(reason=reason):
                data = copy.deepcopy(self.data)
                data['assets'].append({'id':'frame', 'type':'image', 'file':'assets/frame.jpg',
                    'camera_scene':'S4', 'frame_at':10.8815, 'frame_reason':reason, 'scene_frame':True})
                data['replacements']['scene_plan']['S4'] = {'frame_at':10.8815, 'frame_asset_id':'frame'}
                before = copy.deepcopy(data)
                row = scene_plan.scene_cast(data)['S4']
                self.assertEqual(row['frame_mode'], 'auto' if reason == 'scene_auto' else 'manual')
                self.assertAlmostEqual(row['frame']['at'], 11.4415 if reason == 'scene_auto' else 10.8815)
                if reason == 'scene_auto':
                    self.assertIsNone(row['frame']['asset_id'])
                self.assertEqual(data, before)

    def test_original_thumbnails_map_appearance_to_analysis_and_stay_in_scene(self):
        rows = scene_plan.scene_cast(self.data)
        by_key = lambda sid: {r['key']:r['original_file'] for r in rows[sid]['subjects']}
        self.assertEqual(by_key('S4')['C1'], 'analysis/frames/shot-007.jpg')
        self.assertIsNone(by_key('S4')['C2'])  # 11.4415 is outside the first 10.583–11.18 appearance.
        self.assertIsNone(by_key('S4')['product'])  # No analysis frame near the clipped moment midpoint.
        self.assertEqual(by_key('S3')['product'], 'analysis/frames/shot-004.jpg')  # 5.3 belongs to S2; 7.5 is valid.
        self.assertEqual(rows['S4']['subjects'][0]['cast_original_file'], 'analysis/frames/shot-001.jpg')
        self.assertIsNone(scene_plan.original_file(self.data, 'C2'))

    def test_product_thumbnail_must_be_inside_even_a_narrow_moment(self):
        self.data['breakdown']['productMoments'] = [{'start':4.8, 'end':5.05}]
        for shot in self.data['breakdown']['shots']:
            shot['product'] = '无'
        self.assertIsNone(scene_plan.original_file(self.data, 'product'))

    def test_product_thumbnail_prefers_explicit_product_moment_in_this_scene(self):
        self.data['breakdown']['productMoments'] = [{'start':11.2, 'end':12.3}]
        rows = scene_plan.scene_cast(self.data)['S4']['subjects']
        self.assertIsNone(next(r for r in rows if r['key']=='product')['original_file'])  # >0.3s from midpoint.

    def test_uncaptured_frame_exposes_nearest_thumbnail_without_claiming_exact_capture(self):
        self.data['replacements']['scene_plan']['S4'] = {'frame_at':11.6, 'frame_mode':'manual'}
        frame = scene_plan.scene_cast(self.data)['S4']['frame']
        self.assertIsNone(frame['file'])
        self.assertEqual(frame['suggested_file'], 'analysis/frames/shot-008.jpg')
        self.assertEqual(frame['suggested_at'], 11.4415)


class FramePersistenceTests(unittest.TestCase):
    setUp = fixtures.Round24_17Tests.setUp
    persist = fixtures.Round24_17Tests.persist
    frame = staticmethod(fixtures.Round24_17Tests.frame)

    def test_capture_records_auto_or_manual_and_draft_respects_provenance(self):
        frame = pipeline.capture_scene_frame(self.pid, 'S4')
        self.assertEqual(frame['frame_mode'], 'auto')
        row = store.load(self.pid)['replacements']['scene_plan']['S4']
        self.assertEqual(row['frame_mode'], 'auto')
        with patch.object(pipeline, 'capture_scene_frame', wraps=pipeline.capture_scene_frame) as capture:
            pipeline.draft_scene_camera_image(self.pid, 'openai', 'S4')
            self.assertIsNone(capture.call_args.args[2])
        pipeline.capture_scene_frame(self.pid, 'S4', 11.7)
        self.assertEqual(store.load(self.pid)['replacements']['scene_plan']['S4']['frame_mode'], 'manual')
        with patch.object(pipeline, 'capture_scene_frame', wraps=pipeline.capture_scene_frame) as capture:
            pipeline.draft_scene_camera_image(self.pid, 'openai', 'S4')
            self.assertEqual(capture.call_args.args[2], 11.7)

    def test_reused_frame_can_switch_mode_and_stale_form_cannot_overwrite_it(self):
        frame = pipeline.capture_scene_frame(self.pid, 'S4')
        pipeline.capture_scene_frame(self.pid, 'S4', frame['at'])
        stale = copy.deepcopy(self.data['replacements'])
        stale['scene_plan']['S4'] = {'frame_mode':'auto'}
        result = pipeline.save_replacements(self.pid, stale)
        self.assertEqual(result['replacements']['scene_plan']['S4']['frame_mode'], 'manual')
        self.assertEqual(scene_plan.scene_cast(result, self.directory)['S4']['frame_mode'], 'manual')
        self.assertTrue(scene_plan.scene_cast(result, self.directory)['S4']['frame']['reason'].startswith('手动选择'))
        self.assertEqual(pipeline.draft_scene_camera_image(self.pid, 'openai', 'S4')['frame_reason_code'], 'manual')


if __name__ == '__main__':
    unittest.main()
