"""V2d review contracts: synthetic media, fake reasoning, no remote generation."""
import copy
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from console import asr, config, director, final_preview, gateway, jimmy_bridge, jobs, media, store

ROOT = Path(__file__).resolve().parents[1]


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((ROOT / 'console/segment_review.py').is_file(), '缺少生成后验收模块')
        self.review = importlib.import_module('console.segment_review')
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.tmp))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('no secret reads')))
        self.enterContext(patch.object(config, 'load', return_value={**config.DEFAULTS, 'OPENAI_API_KEY': 'fake'}))
        self.enterContext(patch.object(gateway, 'reason_checked', side_effect=AssertionError('fake required')))
        self.enterContext(patch.object(asr, 'available', return_value=False))
        self.enterContext(patch.dict(os.environ, {'PATH': str(ROOT / 'runtime/ffmpeg') + os.pathsep + os.environ['PATH']}))
        self.pid = store.create('synthetic review')['id']
        self.d = store.project_dir(self.pid)
        self.segment = dict(id='d01', cells=['c01'], source_start=0, source_end=2, duration=2,
                            what='测试动作', check=['测试检查'], prompt='图1 新画面；图2 人物照；图3 商品照；视频1 提供动作。测试台词',
                            mode='video', images=['c01', 'C1', 'product'], gen_images=['c01', 'C1', 'product'])
        refs = []
        for i, key in enumerate(('c01', 'C1', 'product', 'source')):
            file = f'assets/{key}.png'
            Image.new('RGB', (64, 96), (i * 50, 20, 50)).save(self.d / file)
            if key != 'source':
                refs.append(dict(key=key, label=key, file=file, tag=f'图{i+1}',
                                 description='第 1 格的新画面（替换后的状态）' if key=='c01' else '新商品照片' if key=='product' else 'PERSON C1 的人物照'))
        data = store.update(self.pid, lambda d: d.update(
            storyboard={'cells': [dict(id='c01', file='assets/source.png')]},
            segments_plan={'format_version': 2, 'segments': [copy.deepcopy(self.segment)]},
            director={'format_version': 3, 'segments': [{**self.segment, 'selected_run': 'r1'}]}))
        self.context = self.review.freeze_inputs(data, self.segment, refs, self.d, self.d / 'packages/d01-r1')
        self.clip = self.d / 'clips/result.mp4'
        media._run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
                    'testsrc2=s=64x96:r=10:d=2', '-c:v', 'libx264', '-preset', 'ultrafast', str(self.clip)])
        self.run = dict(rev='r1', kind='director', state='completed', result='clips/result.mp4',
                        run_dir='jimmy-runs/d01-r1', review_inputs=self.context)
        store.update(self.pid, lambda d: d.update(runs={'d01': [copy.deepcopy(self.run)]}))
        self.workers = []
        self.enterContext(patch.object(jobs, 'start', side_effect=self.queue))

    def queue(self, pid, key, fn, label):
        self.workers.append(fn)
        return {'state': 'running'}

    def saved(self):
        return store.load(self.pid)['runs']['d01'][0]

    def perform(self, value=None, error=None):
        self.review.start(self.pid, 'd01', 'r1', force=True)
        with patch.object(gateway, 'reason_checked', side_effect=error,
                          return_value=(value or dict(verdict='pass', summary='通过', issues=[]), {})) as reason:
            self.workers.pop()()
        return reason

    def issue(self, **kw):
        return dict(start=.5, end=1.5, level='high', what='旧物出现', fix='image_mode', cell='c01',
                    how='回第 04 步改成只用图片后重跑', **kw)

    def test_half_second_frame_labels_and_complete_evidence(self):
        with patch.object(media, 'extract_frame', wraps=media.extract_frame) as extract:
            reason = self.perform()
        self.assertEqual([c.args[1] for c in extract.call_args_list], [0, .5, 1, 1.5])
        args, kw = reason.call_args
        self.assertEqual(args[0], 'director')
        self.assertNotIn('override', kw)
        self.assertEqual(kw['project'], self.pid)
        parts = args[1][1]['content']
        labels = [p['text'] for p in parts if p['type'] == 'text' and p['text'].startswith('t=')]
        self.assertEqual(labels, ['t=0.0s', 't=0.5s', 't=1.0s', 't=1.5s'])
        for index, p in enumerate(parts):
            if p['type'] == 'text' and p['text'].startswith('t='):
                self.assertEqual(parts[index + 1]['type'], 'image_url')
        text = '\n'.join(p['text'] for p in parts if p['type'] == 'text')
        for expected in ('第 1 格的新画面', 'PERSON C1 的人物照', '新商品照片', '原片单格 c01', 'what', 'check', 'prompt', '测试台词', '未转录'):
            self.assertIn(expected, text)
        for removed in ('timeline', 'forbid', 'brief'):
            self.assertNotIn('\"'+removed+'\"', text)
        self.assertEqual(sum(p['type'] == 'image_url' for p in parts), 8)
        review = self.saved()['review']
        self.assertEqual(review['verdict'], 'pass')
        self.assertEqual(review['status'], 'completed')
        self.assertTrue(review['generated_at'])
        self.assertEqual(len(review['inputs_sig']), 64)
        self.assertTrue(review['response_file'].startswith('analysis/segments/reviews/'))
        self.assertEqual(json.loads((self.d / review['response_file']).read_text())['verdict'], 'pass')

    def test_v3_review_priority_and_identity_rules(self):
        for rule in ('level=high 只用于优先级①②', '轻微的节奏、切点早晚', '标 low',
                     '④不报', '先对照每个人物的参考照和新画面', '衣服和发饰配饰'):
            self.assertIn(rule, self.review.SYSTEM)

    def test_local_word_times_and_unavailable_asr_fallback(self):
        with patch.object(asr, 'available', return_value=True), patch.object(media, 'probe', return_value={'duration': 2, 'audio_stream_count': 1}), \
                patch.object(media, 'extract_audio_wav'), patch.object(asr, 'transcribe_best', return_value={
                    'segments': [{'words': [{'word': '测试', 'start': .2, 'end': .6}]}]}) as transcribe:
            reason = self.perform()
        self.assertTrue(transcribe.call_args.kwargs['local_only'])
        self.assertIn('逐词时间', str(reason.call_args.args[1]))
        self.assertIn('0.6', str(reason.call_args.args[1]))
        with patch.object(asr, 'available', side_effect=ImportError('absent')):
            reason = self.perform()
        self.assertIn('未转录', str(reason.call_args.args[1]))

    def test_audio_tail_does_not_request_a_nonexistent_video_frame(self):
        media._run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
                    'testsrc2=s=64x96:r=25:d=2', '-f', 'lavfi', '-i',
                    'sine=frequency=440:sample_rate=48000:duration=2.05', '-c:v', 'libx264',
                    '-preset', 'ultrafast', '-c:a', 'aac', str(self.clip)])
        self.assertGreater(media.probe(self.clip)['duration'], 2)
        with patch.object(media, 'extract_frame', wraps=media.extract_frame) as extract:
            self.perform()
        self.assertEqual(self.saved()['review']['status'], 'completed')
        self.assertEqual([c.args[1] for c in extract.call_args_list], [0, .5, 1, 1.5])

    def test_refresh_leaves_result_unchecked_until_manual_start(self):
        store.update(self.pid, lambda d: d['runs']['d01'][0].update(state='running', result=None))
        store.atomic_write_json(self.d / self.run['run_dir'] / 'state.json',
                                {'tasks': {'d01': {'phase': 'completed', 'local_path': str(self.clip)}}})
        with patch.object(self.review, 'start', wraps=self.review.start) as start:
            jimmy_bridge.refresh(self.pid, 'd01', 'r1')
            start.assert_not_called()
        self.assertEqual(self.saved()['state'], 'completed')
        self.assertNotIn('review', self.saved())
        self.review.start(self.pid, 'd01', 'r1', force=True)
        self.assertEqual(self.saved()['review']['status'], 'running')
        with patch.object(jobs, 'snapshot', return_value={'review:d01:r1': {'state': 'running'}}):
            jimmy_bridge.refresh(self.pid, 'd01', 'r1')
        self.assertEqual(len(self.workers), 1)
        with patch.object(gateway, 'reason_checked', return_value=(dict(verdict='pass', summary='通过', issues=[]), {})):
            self.workers.pop()()
        jimmy_bridge.refresh(self.pid, 'd01', 'r1')
        self.assertEqual(self.workers, [])

    def test_failure_is_actionable_and_never_changes_result_or_selection(self):
        self.perform(error=RuntimeError('offline failure'))
        run = self.saved()
        self.assertEqual(run['state'], 'completed')
        self.assertEqual(run['result'], 'clips/result.mp4')
        self.assertEqual(run['review']['status'], 'failed')
        for fragment in ('检查没完成', '没受影响', '检查视频'):
            self.assertIn(fragment, run['review']['error'])
        self.assertEqual(store.load(self.pid)['director']['segments'][0]['selected_run'], 'r1')
        self.perform()
        self.assertEqual(self.saved()['review']['verdict'], 'pass')

    def test_invalid_fix_times_and_cells_are_rejected_and_raw_reply_archived(self):
        for changes in ({'fix': 'delete'}, {'start': -1}, {'end': 2.6}, {'start': float('nan')},
                        {'start': True}, {'cell': 'c99'}, {'level': 'fatal'}):
            value = dict(verdict='fix', summary='需要处理', issues=[{**self.issue(), **changes}])
            with self.subTest(changes=changes):
                self.perform(value)
                review = self.saved()['review']
                self.assertEqual(review['status'], 'failed')
                self.assertTrue((self.d / review['response_file']).is_file())
        self.perform(dict(verdict='fix', summary='需要处理', issues=[self.issue()]))
        self.assertEqual(self.saved()['review']['issues'][0]['fix'], 'image_mode')

    def test_bad_json_is_archived_before_validation(self):
        from console.gateway.errors import JSONResponseError
        def reason(*args, **kw):
            kw['on_response']('not valid JSON')
            raise JSONResponseError('invalid JSON')
        self.review.start(self.pid, 'd01', 'r1', force=True)
        with patch.object(gateway, 'reason_checked', side_effect=reason):
            self.workers.pop()()
        review = self.saved()['review']
        self.assertEqual(review['status'], 'failed')
        self.assertEqual((self.d / review['response_file']).read_text(), 'not valid JSON')

    def test_each_raw_attempt_is_kept_and_network_retry_does_not_reuse_old_archive(self):
        value = dict(verdict='pass', summary='通过', issues=[])
        def reason(*args, **kw):
            kw['on_response']('first incomplete reply')
            kw['on_response'](json.dumps(value, ensure_ascii=False))
            return value, {}
        self.review.start(self.pid, 'd01', 'r1', force=True)
        with patch.object(gateway, 'reason_checked', side_effect=reason):
            self.workers.pop()()
        path = self.d / self.saved()['review']['response_file']
        self.assertEqual(len(list(path.parent.glob('response*.json'))), 2)
        self.assertTrue(any(p.read_text() == 'first incomplete reply' for p in path.parent.glob('response*.json')))
        self.perform(error=RuntimeError('no response'))
        self.assertNotIn('response_file', self.saved()['review'])

    def test_refresh_all_leaves_historical_review_for_explicit_retry_and_ignores_legacy_generation(self):
        store.update(self.pid, lambda d: d['runs']['d01'][0].update(review={'status': 'running'}))
        with patch.object(jobs, 'snapshot', return_value={}):
            jimmy_bridge.refresh_all(self.pid)
        self.assertEqual(self.workers, [])
        self.review.start(self.pid, 'd01', 'r1', force=True)
        self.assertEqual(len(self.workers), 1)
        self.workers.clear()
        store.update(self.pid, lambda d: d['runs']['d01'][0].update(kind='camera'))
        self.review.start(self.pid, 'd01', 'r1')
        self.assertEqual(self.workers, [])

    def test_read_refresh_does_not_backfill_historical_completed_versions(self):
        # A GET of an old project must not submit inference for its whole history.
        store.atomic_write_json(self.d / self.run['run_dir'] / 'state.json',
                                {'tasks': {'d01': {'phase': 'completed', 'local_path': str(self.clip)}}})
        jimmy_bridge.refresh_all(self.pid)
        jimmy_bridge.refresh(self.pid, 'd01', 'r1')
        self.assertEqual(self.workers, [])
        self.assertNotIn('review', self.saved())

    def test_http_review_endpoint_returns_project_and_uses_no_generation_route(self):
        from console import runtime_env
        with patch.object(runtime_env, 'apply'):
            server = importlib.import_module('server')
        handler = server.Handler.__new__(server.Handler)
        handler.headers = {'X-Client-Build': 'v2d'}
        with patch.object(server, 'build_status', return_value={'restart_pending': False, 'build': 'v2d'}), \
                patch.object(handler, '_body_json', return_value={}), \
                patch.object(server, 'project_view', side_effect=lambda data: data), \
                patch.object(jobs, 'snapshot', side_effect=lambda pid: {'review:d01:r1': {'state': 'running'}} if self.workers else {}), \
                patch.object(gateway, 'video_confirm', side_effect=AssertionError('no generation')), \
                patch.object(self.review, 'start', wraps=self.review.start) as start:
            result = handler._api('POST', ['projects', self.pid, 'runs', 'd01', 'r1', 'review'], {})
        start.assert_called_once_with(self.pid, 'd01', 'r1', force=True)
        self.assertEqual(result['id'], self.pid)
        self.assertEqual(result['runs']['d01'][0]['review']['status'], 'running')
        self.assertEqual(result.get('jobs', {}).get('review:d01:r1', {}).get('state'), 'running')

    def test_frozen_inputs_survive_upstream_edits_and_signature_tracks_video(self):
        self.perform()
        first = self.saved()['review']['inputs_sig']
        store.update(self.pid, lambda d: d.update(director={}, storyboard={}))
        (self.d / 'assets/source.png').unlink()
        self.perform()
        self.assertEqual(self.saved()['review']['inputs_sig'], first)
        with self.clip.open('ab') as stream:
            stream.write(b'changed content')
        self.perform()
        self.assertNotEqual(self.saved()['review']['inputs_sig'], first)

    def test_high_warning_for_selected_version_does_not_block_assembly(self):
        self.perform(dict(verdict='fix', summary='需要处理', issues=[self.issue()]))
        data = store.load(self.pid)
        view = final_preview.view(data, self.d)
        self.assertTrue(view['ready'])
        self.assertEqual(view['warnings'], [])
        self.assertIn('第 1 段选用的版本检查有问题', view['review_warnings'][0])
        self.assertIn('回第 04 步（对照和修改），选中这一段，在“视频”里处理，或确认接受后再拼接。', view['review_warnings'][0])
        with patch.object(media, 'concat') as concat, patch.object(media, 'probe', return_value={'duration': 2}):
            final_preview.generate(self.pid)
        self.assertEqual(concat.call_count, 1)
        data['runs']['d01'][0]['review']['issues'][0]['level'] = 'low'
        self.assertEqual(final_preview.view(data, self.d)['review_warnings'], [])
        data['runs']['d01'].append({**self.run, 'rev': 'r2'})
        data['director']['segments'][0]['selected_run'] = 'r2'
        self.assertEqual(final_preview.view(data, self.d)['review_warnings'], [])

    def test_scheduling_failure_and_incomplete_run(self):
        with patch.object(jobs, 'start', side_effect=RuntimeError('cannot start')):
            self.review.start(self.pid, 'd01', 'r1', force=True)
        self.assertEqual(self.saved()['state'], 'completed')
        self.assertEqual(self.saved()['review']['status'], 'failed')
        store.update(self.pid, lambda d: d['runs']['d01'][0].update(state='running'))
        with self.assertRaises(ValueError):
            self.review.start(self.pid, 'd01', 'r1', force=True)


if __name__ == '__main__':
    unittest.main()
