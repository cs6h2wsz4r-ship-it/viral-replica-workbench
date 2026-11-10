"""24.17c: synthetic source/scene boundaries, local media and mocked models only."""
import copy
import json
import os
import subprocess
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from console import config, media, pipeline, prompts, runtime_env, scene_plan, store
from tools import test_round24_17 as fixtures


class Round24_17cTests(unittest.TestCase):
    setUp = fixtures.Round24_17Tests.setUp
    persist = fixtures.Round24_17Tests.persist
    frame = staticmethod(fixtures.Round24_17Tests.frame)

    def misaligned(self):
        self.data['breakdown']['characters'][0].update(appearance='白发老人', outfit='深蓝开衫')
        self.data['breakdown']['characters'][1].update(appearance='军绿外套年轻男子')
        self.data['breakdown']['productMoments'] = [{'start': 5.1, 'end': 5.8, 'product': '白色 SENVIA SOS 手环'}]
        self.data['analysis'] = {'shots': [
            {'start': 3.833, 'end': 5.333, 'frame_at': 4.583, 'frame': 'analysis/frames/porch.jpg'},
            {'start': 5.333, 'end': 6.333, 'frame_at': 5.833, 'frame': 'analysis/frames/next-scene.jpg'}]}
        for shot in self.data['analysis']['shots']:
            file = self.directory / shot['frame']
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b'analysis')
        self.data['replacements']['scene_plan']['S2'] = {'frame_at': 5.749999999999999, 'frame_mode': 'manual'}
        self.persist()

    def test_first_product_thumbnail_uses_its_moment_midpoint_not_portrait(self):
        self.misaligned()
        row = next(s for s in scene_plan.scene_cast(self.data, self.directory)['S2']['subjects'] if s['key'] == 'product')
        self.assertIsNone(row['cast_original_file'])
        self.assertIn('at=5.45', row['cast_original_url'])

    def test_first_character_thumbnail_does_not_leak_into_later_appearance(self):
        self.misaligned()
        # C1 is present in the analysis frame too, but not in its FIRST appearance interval.
        self.data['breakdown']['shots'][0]['end'] = 1
        self.assertIsNone(scene_plan.original_file(self.data, 'C1', self.directory))
        row = scene_plan.scene_cast(self.data, self.directory)['S1']['subjects'][0]
        self.assertIn('at=0.50', row['cast_original_url'])

    def test_suggested_and_optional_frames_stay_inside_scene_and_close_to_target(self):
        self.misaligned()
        row = scene_plan.scene_cast(self.data, self.directory)['S2']
        self.assertNotIn('suggested_file', row['frame'])
        self.assertIn('at=5.75', row['frame']['suggested_url'])
        for frame in row['frame_choices']:
            self.assertTrue(4.8 <= frame['at'] < 5.8)
            self.assertNotIn(frame.get('file'), ['analysis/frames/porch.jpg', 'analysis/frames/next-scene.jpg'])

    def test_valid_nearby_analysis_thumbnail_is_reused(self):
        self.misaligned()
        shot = self.data['analysis']['shots'][1]
        shot['frame_at'] = 5.70
        frame = scene_plan.scene_cast(self.data, self.directory)['S2']['frame']
        self.assertEqual(frame['suggested_file'], shot['frame'])
        self.assertEqual(frame['suggested_at'], 5.70)
        self.assertNotIn('suggested_url', frame)

    def test_nearby_frame_before_son_enters_cannot_represent_both_people(self):
        self.data['analysis'] = {'shots': [{'start': 9, 'end': 11, 'frame_at': 10.45,
                                          'frame': 'assets/father.png'}]}
        self.data['replacements']['scene_plan']['S4'] = {'frame_at': 10.6, 'frame_mode': 'manual'}
        frame = scene_plan.scene_cast(self.data, self.directory)['S4']['frame']
        self.assertNotIn('suggested_file', frame)
        self.assertIn('at=10.60', frame['suggested_url'])

    def test_thumbnail_get_is_cached_concurrently_and_never_writes_project_json(self):
        self.misaligned()
        with patch.object(runtime_env, 'apply'):
            import server
        handler = object.__new__(server.Handler)
        handler.headers = {}
        before = (self.directory / 'project.json').read_bytes()
        def get():
            return handler._api('GET', ['projects', self.pid, 'thumb'], {'at': '5.75'})
        with ThreadPoolExecutor(max_workers=4) as pool:
            files = list(pool.map(lambda _: get(), range(4)))
        self.assertEqual(len(set(files)), 1)
        self.assertTrue(files[0].is_relative_to(self.directory / 'analysis/thumbs'))
        self.assertEqual(self.extract.call_count, 1)
        self.assertEqual(self.extract.call_args.args[1], 5.75)
        self.assertEqual(self.extract.call_args.kwargs['width'], 360)
        self.assertEqual(self.reason.call_count, 0)
        self.assertEqual((self.directory / 'project.json').read_bytes(), before)
        # Dispatch must return JPEG bytes instead of trying to JSON-serialize a Path.
        handler.path = f'/api/projects/{self.pid}/thumb?at=5.75'
        handler._serve_file = lambda file: file.read_bytes()
        self.assertEqual(handler._dispatch('GET'), b'frame:5.75')

    def test_thumbnail_rejects_bad_times_unsafe_source_and_cache_paths(self):
        with patch.object(runtime_env, 'apply'):
            import server
        handler = object.__new__(server.Handler)
        for at in ('nan', 'inf', '-1', '12.3', 'oops', None):
            with self.subTest(at=at), self.assertRaisesRegex(ValueError, '秒数|时间'):
                handler._project_api('GET', self.pid, ['thumb'], {'at': at})
        self.data['source']['file'] = '../escape.mp4'
        self.persist()
        with self.assertRaisesRegex(ValueError, '原片'):
            handler._project_api('GET', self.pid, ['thumb'], {'at': '5.75'})

    def test_actual_local_thumbnail_dimensions_and_cache(self):
        self.enterContext(patch.dict(os.environ, {'PATH': str(config.ROOT / 'runtime/ffmpeg') + os.pathsep + os.environ.get('PATH', '')}))
        self.misaligned()
        video = self.directory / 'source/source.mp4'
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
            '-i', 'color=c=navy:s=720x1280:r=10:d=1', '-c:v', 'libx264', '-threads', '1', str(video)], check=True)
        self.data['source']['probe']['duration'] = 1
        self.persist()
        with patch.object(media, 'extract_frame', wraps=fixtures.EXTRACT_FRAME) as extract:
            file = scene_plan.cached_thumbnail(self.pid, .5)
            self.assertEqual(media.probe(file)['width'], 360)
            self.assertEqual(scene_plan.cached_thumbnail(self.pid, .5), file)
            self.assertEqual(extract.call_count, 1)

    def test_frame_text_includes_original_appearance_outfit_and_product(self):
        self.misaligned()
        row = scene_plan.scene_cast(self.data, self.directory)['S2']
        self.assertIn('父亲（白发老人', row['frame']['present_text'])
        self.assertIn('商品（白色 SENVIA SOS 手环）', row['frame']['present_text'])
        self.assertEqual(row['subjects'][0]['original_description'], '白发老人，深蓝开衫')
        self.assertEqual(row['subjects'][-1]['original_description'], '白色 SENVIA SOS 手环')

    def test_seconds_persist_two_decimals_and_known_thumbnail_keeps_exact_correspondence(self):
        frame = pipeline.capture_scene_frame(self.pid, 'S2', 5.749999999999999)
        saved = store.load(self.pid)
        self.assertEqual(frame['at'], 5.75)
        self.assertEqual(saved['replacements']['scene_plan']['S2']['frame_at'], 5.75)
        self.data = saved
        self.data['analysis'] = {'shots': [{'start': 4.8, 'end': 5.8, 'frame_at': 5.733,
            'frame': frame['file']}]}
        self.persist()
        exact = pipeline.capture_scene_frame(self.pid, 'S2', 5.733)
        self.assertEqual(exact['at'], 5.733)
        saved = store.load(self.pid)
        self.assertEqual(saved['replacements']['scene_plan']['S2']['frame_at'], 5.73)
        row = scene_plan.scene_cast(saved, self.directory)['S2']
        self.assertEqual(row['frame']['asset_id'], exact['asset_id'])
        self.assertEqual(row['frame']['at'], 5.733)
        self.assertEqual(pipeline.draft_scene_camera_image(self.pid, 'openai', 'S2')['frame_asset_id'], exact['asset_id'])
        typed = pipeline.capture_scene_frame(self.pid, 'S2', 5.73)
        self.assertNotEqual(typed['asset_id'], exact['asset_id'])
        self.assertEqual(typed['at'], 5.73)

    def test_rounded_auto_frame_remains_selected_after_read_and_preview(self):
        self.data['breakdown']['shots'][-1]['start'] = 10.583
        self.persist()
        frame = pipeline.capture_scene_frame(self.pid, 'S4')
        saved = store.load(self.pid)
        row = scene_plan.scene_cast(saved, self.directory)['S4']
        self.assertEqual(row['frame']['asset_id'], frame['asset_id'])
        self.assertEqual(pipeline.draft_scene_camera_image(self.pid, 'openai', 'S4')['frame_asset_id'], frame['asset_id'])

    def test_thumbnail_rounding_cannot_cross_scene_or_appearance_end(self):
        self.misaligned()
        self.data['replacements']['scene_plan']['S2']['frame_at'] = 5.799
        frame = scene_plan.scene_cast(self.data, self.directory)['S2']['frame']
        self.assertLess(float(frame['suggested_url'].split('at=')[-1]), 5.8)

    def test_thumbnail_failed_capture_retries_and_source_change_invalidates_cache(self):
        self.misaligned()
        with patch.object(media, 'extract_frame', side_effect=media.MediaError('offline failure')):
            with self.assertRaises(media.MediaError):
                scene_plan.cached_thumbnail(self.pid, 5.75)
        self.assertEqual(list((self.directory / 'analysis/thumbs').iterdir()), [])
        first = scene_plan.cached_thumbnail(self.pid, 5.75)
        (self.directory / self.data['source']['file']).write_bytes(b'changed synthetic source')
        second = scene_plan.cached_thumbnail(self.pid, 5.75)
        self.assertNotEqual(first, second)
        self.assertEqual(self.extract.call_count, 2)

    def test_thumbnail_refuses_symlink_cache_outside_project(self):
        external = self.temp / 'outside-cache'
        external.mkdir()
        analysis = self.directory / 'analysis'
        analysis.mkdir(exist_ok=True)
        (analysis / 'thumbs').symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '缓存位置'):
            scene_plan.cached_thumbnail(self.pid, 5.75)
        self.assertEqual(list(external.iterdir()), [])

    def test_thumbnail_does_not_publish_empty_ffmpeg_output(self):
        with patch.object(media, 'extract_frame', return_value=None):
            with self.assertRaisesRegex(media.MediaError, '取帧失败'):
                scene_plan.cached_thumbnail(self.pid, 5.75)
        self.assertEqual(list((self.directory / 'analysis/thumbs').iterdir()), [])

    def test_known_frame_at_scene_start_is_valid_before_rounding_in_single_prompt(self):
        self.data['breakdown']['scene'][1]['timeRanges'] = [[4.803, 5.8]]
        self.data['analysis'] = {'shots': [{'start': 4.803, 'end': 5.8, 'frame_at': 4.804,
                                          'frame': 'assets/watch.png'}]}
        self.persist()
        frame = pipeline.capture_scene_frame(self.pid, 'S2', 4.804)
        draft = pipeline.draft_scene_camera_image(self.pid, 'openai', 'S2')
        self.assertEqual(draft['frame_at'], 4.804)
        self.assertEqual(draft['frame_asset_id'], frame['asset_id'])

    def test_three_prompt_variants_share_product_use_action_and_age_rules(self):
        for kind, off in [('single', ['C2']), ('multiple', []), ('product', ['C1', 'C2'])]:
            with self.subTest(kind=kind):
                self.data['replacements']['scene_plan']['S4'] = {'subjects': {key: {'on': False} for key in off}}
                self.persist()
                draft = pipeline.draft_scene_camera_image(self.pid, 'openai', 'S4')
                for text in ('多角度拼图或说明图', '只出现这一件商品', '文字标注', '正常使用方式'):
                    self.assertIn(text, draft['prompt'])
                system = self.reason.call_args.args[1][0]['content']
                for text in ('谁在做什么', '手在哪里', '背景', '包装', '35', 'girl/boy', 'woman/man',
                             '商品按真实用法使用或摆放', '正面或功能面朝向镜头',
                             '比例真实', '不穿插皮肤或衣物'):
                    self.assertIn(text, system)
                self.assertNotIn('背影时，改为正面', system)
                self.assertIn('谁在做什么', draft['prompt'])


if __name__ == '__main__':
    unittest.main()
