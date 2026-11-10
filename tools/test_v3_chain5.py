"""V3 authoring contracts; synthetic media and fake reasoning only."""
import copy
import importlib
import tempfile
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from console import config, jimmy_bridge, jobs, remote_query, runtime_env, segment_review, director, gateway, scene_prompt, segment_plan, segment_boards, store, storyboard_edits
from tools import test_director_sb3 as fixtures
from tools.v2_fixture import configure
from tools import director_sb3_bridge_cases as bridge_fixtures


ROOT = Path(__file__).resolve().parents[1]


class Chain5AuthoringTests(unittest.TestCase):
    setUp = fixtures.DirectorSB3Tests.setUp

    def fixture(self):
        data = fixtures.DirectorSB3Tests.raw_fixture(self, 4, .75)
        return configure(data, self.d, [['c01', 'c02'], ['c03', 'c04']], {
            'd1': {'gen_images': ['c01', 'C2', 'product']},
            'd2': {'refs': ['C1', 'C2'], 'gen_images': ['C2', 'c04']}})

    def fields(self, data):
        return {'summary': '保持动作，更新人物和商品', 'questions': [], 'segments': [
            {'id': row['id'], 'what': '人物展示商品', 'check': ['人物身份一致'],
             'prompt': '写实手机竖屏视频。' + '；'.join(f'图{i} 提供目标外观' for i in range(1, len(segment_plan.generation_images(data,row,self.d)) + 1))
                       + ('；视频1 只提供动作节奏' if row['mode'] == 'video' else '')}
            for row in data['segments_plan']['segments']]}

    def generated(self):
        data = self.fixture()
        with patch.object(gateway, 'reason_checked', return_value=(self.fields(data), {})):
            return director.generate(self.pid)

    def test_gen_images_limits_legal_keys_and_coverage(self):
        data = fixtures.DirectorSB3Tests.raw_fixture(self, 2, 1)
        row = dict(id='d1', cells=['c01', 'c02'], mode='video', refs=['C1', 'C2', 'product', 'N1'], gen_images=['c01'])
        self.assertEqual(segment_plan.validate_plan({'segments': [row]}, data, self.d)['segments'][0]['gen_images'], ['c01'])
        for keys, missing in [([], ''), (['c01', 'c02', 'C1', 'C2', 'product'], ''),
                              (['c03'], ''), (['unknown'], ''), (['c02'], '商品'), (['C1', 'product'], 'C2')]:
            with self.subTest(keys=keys), self.assertRaisesRegex(ValueError, '分段方案不完整：.*' + missing):
                segment_plan.validate_plan({'segments': [{**row, 'gen_images': keys}]}, data, self.d)
        with self.assertRaisesRegex(ValueError, '分段方案不完整'):
            segment_plan.validate_plan({'segments': [{**row, 'refs': ['C1'], 'gen_images': ['C2', 'c01']}]}, data, self.d)

    def test_later_product_requires_coverage_but_unseen_target_cannot_supply_it(self):
        data = fixtures.DirectorSB3Tests.raw_fixture(self, 1, 1)
        data['storyboard']['cells'][0].update(product='', product_later=True)
        row = dict(id='d1', cells=['c01'], mode='video', refs=['C1', 'C2'], gen_images=['c01'])
        with self.assertRaisesRegex(ValueError, '分段方案不完整：.*新商品'):
            segment_plan.validate_plan({'segments': [row]}, data, self.d)
        row.update(refs=['C1', 'C2', 'product'], gen_images=['c01', 'product'])
        self.assertEqual(segment_plan.validate_plan({'segments': [row]}, data, self.d)['segments'][0]['gen_images'], ['c01', 'product'])

    def test_gen_images_only_invalidates_director(self):
        data = self.generated(); changed = copy.deepcopy(data)
        changed['segments_plan']['segments'][0]['gen_images'] = ['product', 'c01', 'C2']
        self.assertEqual(segment_plan.segment_sig(data['segments_plan']['segments'][0]), segment_plan.segment_sig(changed['segments_plan']['segments'][0]))
        self.assertEqual(storyboard_edits.board_sig(data, 'd1'), storyboard_edits.board_sig(changed, 'd1'))
        self.assertEqual(segment_boards.source_sig(data, 'd1', self.d), segment_boards.source_sig(changed, 'd1', self.d))
        self.assertTrue(director.view(changed, self.d)['segments'][0]['stale'])
        self.assertFalse(director.view(changed, self.d)['segments'][1]['stale'])

    def test_legacy_and_put_revalidate(self):
        data = self.fixture(); legacy = copy.deepcopy(data)
        legacy['segments_plan']['segments'][0].pop('gen_images')
        state = segment_plan.status(legacy, self.d)
        self.assertTrue(state['stale'])
        self.assertEqual(state['warning'], '分段方案是旧版本做的，点“重新生成分段方案”。')
        self.assertTrue(director.is_legacy({'format_version': 2, 'segments': []}))
        saved=segment_plan.save(self.pid, 'd1', {'refs': ['C1', 'product']})
        self.assertEqual(saved['segments_plan']['segments'][0]['refs'], ['C1', 'product'])
        self.assertIn('C2',segment_plan.generation_images(saved,saved['segments_plan']['segments'][0],self.d))

    def test_request_numbering_windows_and_single_image_order(self):
        data = self.fixture(); seen = []
        def image(path):
            seen.append(path.relative_to(self.d).as_posix())
            return [{'type': 'image_url', 'image_url': {'url': 'synthetic://image'}}]
        with patch.object(director.llm, 'image_part', side_effect=image):
            request = director.messages(data, self.d)
        text = json.dumps(request, ensure_ascii=False)
        for token in ('图1', '图2', '图3', '视频1', '第 1 格的新画面（替换后的状态）', '新商品照片', '2.0', '4.0', '只给推理模型'):
            self.assertIn(token, text)
        self.assertIn('analysis/storyboard/new-c01.png', seen)
        row = director.validate_plan(self.fields(data), data, self.d)['segments'][0]
        refs = director.references(data, row, self.d)
        self.assertEqual([r['key'] for r in refs], ['c01', 'C2', 'product', 'C1'])
        self.assertEqual([r['file'] for r in refs], ['analysis/storyboard/new-c01.png', 'assets/son.png', 'assets/watch.png', 'assets/father.png'])
        data['director'] = {'format_version': 3, 'segments': [row]}
        materials = director.view(data, self.d)['segments'][0]['generation_materials']
        self.assertEqual([r['label'] for r in materials], ['图1', '图2', '图3', '图4', '视频1'])
        self.assertEqual(materials[-1]['file'], 'source/source.mp4')

    def test_invalid_prompt_and_check_mark_rewrite(self):
        data = self.fixture(); fields = self.fields(data)
        for update in ({'prompt': '图1 视频1'}, {'prompt': fields['segments'][0]['prompt'] + ' 图5'},
                       {'prompt': fields['segments'][0]['prompt'] + ' 视频2'}, {'prompt': '字' * 5001},
                       {'prompt': fields['segments'][0]['prompt'] + ' c01'}, {'check': []},
                       {'check': ['一', 2]}):
            candidate = copy.deepcopy(fields); candidate['segments'][0].update(update)
            with self.subTest(update=update):
                checked = director.validate_plan(candidate, data, self.d)
                self.assertTrue(checked['segments'][0]['needs_rewrite'])
                self.assertFalse(checked['segments'][1]['needs_rewrite'])

    def test_manual_prompt_is_canonical_and_edited(self):
        data = self.generated(); text = data['director']['segments'][0]['prompt'] + ' 保持画面连贯。'
        result = director.save(self.pid, {'action': 'prompt', 'segment_id': 'd1', 'prompt': text})
        self.assertEqual(result['director']['format_version'], 3)
        self.assertEqual(result['director']['segments'][0]['prompt'], text)
        self.assertTrue(result['director']['segments'][0]['edited'])
        self.assertFalse(result['director']['segments'][0]['needs_rewrite'])
        self.assertNotIn('brief', result['director']['segments'][0])

    def test_review_rewrite_frozen_inputs_and_no_submission(self):
        data = self.generated(); original = 'FROZEN_PROMPT_SENTINEL'
        issues = [dict(fix='image_mode', what='漏回旧外观', how='回第 04 步处理')]
        run = dict(rev='r1', state='completed', result='output/result.mp4',
                   review=dict(status='completed', verdict='fix', issues=issues),
                   review_inputs={'segment': {'prompt': original}})
        store.update(self.pid, lambda d: d.update(runs={'d1': [run]}))
        with patch.object(gateway, 'reason_checked', return_value=(self.fields(data)['segments'][0], {})) as reason:
            result = director.save(self.pid, {'action': 'rewrite', 'segment_id': 'd1', 'review_rev': 'r1'})
        request = json.dumps(reason.call_args.args[1], ensure_ascii=False)
        self.assertIn(original, request); self.assertIn(json.dumps(issues, ensure_ascii=False), request.replace('\\"', '"'))
        self.assertEqual(result['runs'], {'d1': [run]})
        self.assertEqual(result['director']['segments'][1], data['director']['segments'][1])
        with self.assertRaises(ValueError):
            director.save(self.pid, {'action': 'rewrite', 'segment_id': 'd1', 'review_rev': 'unknown'})

    def test_review_rewrite_preview_fallback_and_concurrent_result_change(self):
        data = self.generated()
        run = dict(rev='r1', state='completed', result='output/original.mp4',
                   review=dict(status='completed', verdict='fix', issues=[dict(fix='rerun', what='身份变化')]),
                   preview={'tasks': [{'prompt': 'ORIGINAL_PREVIEW_PROMPT'}]})
        store.update(self.pid, lambda d: d.update(runs={'d1': [run]}))
        def racing(*args, **kwargs):
            request = json.dumps(args[1], ensure_ascii=False)
            self.assertIn('ORIGINAL_PREVIEW_PROMPT', request)
            store.update(self.pid, lambda d: d['runs']['d1'][0].update(result='output/replaced.mp4'))
            return self.fields(data)['segments'][0], {}
        with patch.object(gateway, 'reason_checked', side_effect=racing), self.assertRaisesRegex(ValueError, '刚刚改过'):
            director.save(self.pid, {'action': 'rewrite', 'segment_id': 'd1', 'review_rev': 'r1'})
        self.assertEqual(store.load(self.pid)['director'], data['director'])

    def test_review_rewrite_only_changes_prompt_and_preserves_what_check(self):
        data = self.generated(); before = data['director']['segments'][0]
        run = dict(rev='r1', state='completed', result='output/result.mp4',
                   review=dict(status='completed', verdict='fix', issues=[dict(fix='rerun', what='身份变化')]),
                   review_inputs={'segment': copy.deepcopy(before)})
        store.update(self.pid, lambda d: d.update(runs={'d1': [run]}))
        reply = dict(id='d1', prompt=before['prompt'] + ' 锁定新人物服装和身份。',
                     what='MODEL_CHANGED_WHAT', check=['MODEL_CHANGED_CHECK'],
                     cells=['invented'], mode='image', gen_images=['invented'])
        with patch.object(gateway, 'reason_checked', return_value=(reply, {})):
            result = director.save(self.pid, {'action': 'rewrite', 'segment_id': 'd1', 'review_rev': 'r1'})
        revised = result['director']['segments'][0]
        self.assertEqual(revised['prompt'], reply['prompt'])
        for key in ('what', 'check', 'cells', 'mode', 'gen_images', 'images', 'duration', 'source_start', 'source_end'):
            self.assertEqual(revised[key], before[key], key)
        self.assertEqual(result['runs'], {'d1': [run]})

    def test_generation_retries_invalid_segment_only_once(self):
        data = self.fixture(); value = self.fields(data)
        valid = copy.deepcopy(value['segments'][0]); value['segments'][0]['prompt'] = '图1'
        with patch.object(gateway, 'reason_checked', side_effect=[(value, {}), (valid, {})]) as reason:
            result = director.generate(self.pid)
        self.assertEqual(reason.call_count, 2)
        self.assertFalse(any(s['needs_rewrite'] for s in result['director']['segments']))
        self.assertEqual(result['director']['segments'][1]['prompt'], value['segments'][1]['prompt'])
        self.assertEqual(len(list((self.d / 'analysis/director/responses').glob('*.json'))), 2)

    def test_whole_word_roles_and_new_clothing_contract(self):
        names = ['Jason', 'person', 'son', 'FATHER', 'motherboard', 'doctorate', 'older brother']
        mapped = scene_prompt.role_names({'breakdown': {'characters': [dict(id=f'C{i}', name=name) for i, name in enumerate(names)]}})
        self.assertNotEqual(mapped['C0'], 'THE SON'); self.assertNotEqual(mapped['C1'], 'THE SON')
        self.assertEqual(mapped['C2'], 'THE SON'); self.assertEqual(mapped['C3'], 'THE FATHER')
        self.assertNotEqual(mapped['C4'], 'THE MOTHER'); self.assertNotEqual(mapped['C5'], 'THE DOCTOR')
        for text in ('默认穿人物参考照里的衣服', '原片明显不同', '用户明确要求保留原衣服时照做', '袖口换成新人物的衣服'):
            self.assertIn(text, storyboard_edits.SYSTEM)
        self.assertNotIn('衣服跟原片', storyboard_edits.SYSTEM)
        self.assertIn('收到验收意见时，只针对意见修改相关内容', director.SYSTEM)


class Chain5PackageTests(unittest.TestCase):
    setUp = bridge_fixtures.DirectorBridgeTests.setUp
    package = bridge_fixtures.DirectorBridgeTests.package
    sync_plan = bridge_fixtures.DirectorBridgeTests.sync_plan
    change = bridge_fixtures.DirectorBridgeTests.change

    def test_package_freezes_ordered_current_full_frames_and_original_photos(self):
        from console import jimmy_bridge
        for name, color in [('cell1', 'red'), ('cell2', 'green'), ('father', 'yellow'), ('product', 'purple')]:
            Image.new('RGB', (90, 160), color).save(self.d / 'assets' / f'{name}.jpg')
        keys = ['c02', 'product', 'c01', 'C1']
        files = ['assets/cell2.jpg', 'assets/product.jpg', 'assets/cell1.jpg', 'assets/father.jpg']
        self.change(images=keys, prompt='图1、图2、图3、图4 提供目标状态和外观；视频1 提供动作。')
        with patch.object(director, 'build_storyboard', side_effect=AssertionError('no collage generation')):
            run = jimmy_bridge.preview(self.pid, 'd01')
        plan = json.loads((self.d / run['package'] / 'plan.json').read_text())
        self.assertEqual(plan['tasks'][0]['image_ids'], ['image1', 'image2', 'image3', 'image4'])
        self.assertEqual([r['key'] for r in run['references']], keys)
        self.assertEqual([r['label'] for r in run['references']], ['图1', '图2', '图3', '图4'])
        for media, file in zip(plan['media'][:4], files):
            original = (self.d / file).read_bytes(); frozen = Path(media['path']).read_bytes()
            self.assertEqual(frozen, original)
            self.assertEqual(media['sha256'], hashlib.sha256(original).hexdigest())
            with Image.open(media['path']) as image:
                self.assertEqual(image.size, (90, 160))
        self.assertEqual(run['state'], 'pending_confirmation')

    def test_legacy_director_or_frozen_package_cannot_be_confirmed(self):
        run = jimmy_bridge.preview(self.pid, 'd01')
        original = store.load(self.pid)
        for legacy_plan in (True, False):
            data = copy.deepcopy(original)
            if legacy_plan:
                data['director']['format_version'] = 2
            else:
                frozen = data['runs']['d01'][0]
                frozen.pop('director_format_version', None)
                segment = frozen['review_inputs']['segment']
                segment['brief'] = segment.pop('prompt')
            store.update(self.pid, lambda d: d.update(data))
            with self.subTest(legacy_plan=legacy_plan), patch.object(jimmy_bridge, '_start_generate') as submit:
                with self.assertRaisesRegex(jimmy_bridge.BridgeError, '旧版本'):
                    jimmy_bridge.confirm(self.pid, 'd01', run['rev'], run['job_sha256'])
                submit.assert_not_called()
            self.assertEqual(store.load(self.pid)['runs']['d01'][0]['state'], 'pending_confirmation')


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.tmp))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('no credentials')))
        self.enterContext(patch.object(config, 'load', return_value=config.DEFAULTS))
        self.enterContext(patch.object(remote_query, 'schedule'))
        self.pid = store.create('V3 synthetic recovery')['id']
        self.directory = store.project_dir(self.pid)
        self.clip = self.directory / 'clips/result.mp4'
        self.clip.write_bytes(b'local result')
        self.run = dict(rev='r1', kind='director', state='failed', result=None,
                        run_dir='jimmy-runs/test', remote_task_id='remote-task')
        store.update(self.pid, lambda d: d.update(runs={'d01': [copy.deepcopy(self.run)]}))
        self.state = self.directory / self.run['run_dir'] / 'state.json'
        self.child('failed', error={'type': 'RemoteDisconnected', 'message': 'Remote end closed connection'})

    def child(self, phase, **fields):
        store.atomic_write_json(self.state, {'tasks': {'d01': {'phase': phase, 'jimmy_task_id': 'remote-task', **fields}}})

    def test_connection_errors_are_transient_in_type_or_message(self):
        for value in ('RemoteDisconnected', 'Remote end closed connection without response',
                      'Connection reset by peer', 'Connection aborted', 'ConnectionResetError',
                      'ConnectionAbortedError', 'IncompleteRead', 'EOF occurred in violation of protocol'):
            for error in ({'type': value, 'message': ''}, {'type': 'TransportError', 'message': value}):
                with self.subTest(error=error):
                    self.assertTrue(remote_query.transient(error, 'failed'))
                    self.assertFalse(remote_query.transient(error, 'rejected'))
        self.assertFalse(remote_query.transient({'type': 'RemoteError', 'message': 'content rejected'}, 'failed'))

    def test_transport_failure_keeps_paid_run_recoverable(self):
        with patch.object(segment_review, 'start') as review:
            jimmy_bridge.refresh(self.pid, 'd01', 'r1')
        saved = store.load(self.pid)['runs']['d01'][0]
        self.assertEqual(saved['state'], 'running')
        self.assertEqual(saved['remote_task_id'], 'remote-task')
        self.assertIn('query_retry', saved)
        self.assertIsNone(saved['error'])
        review.assert_not_called()

    def test_explicit_resume_and_recover_keep_result_unchecked_without_create(self):
        for action in ('resume', 'recover'):
            self.child('failed', error={'type': 'RemoteDisconnected', 'message': 'Remote end closed connection'})
            store.update(self.pid, lambda d: d.update(runs={'d01': [copy.deepcopy(self.run)]}))
            calls = []
            def fake_call(args, *unused, **kwargs):
                calls.append(args)
                if args[1] == 'resume':
                    self.child('completed', local_path=str(self.clip))
                return '{}'
            def start(pid, key, work, label):
                work()
                return {'state': 'done'}
            with self.subTest(action=action), patch.object(jobs, 'snapshot', return_value={}), \
                    patch.object(jobs, 'start', side_effect=start), patch.object(jimmy_bridge, '_call', side_effect=fake_call), \
                    patch.object(config, 'jimmy_env_file', return_value=self.tmp / 'fake.env'), \
                    patch.object(segment_review, 'start') as checked:
                if action == 'resume':
                    jimmy_bridge.resume(self.pid, 'd01', 'r1')
                else:
                    jimmy_bridge.recover(self.pid, 'd01', 'r1', 'remote-task')
                checked.assert_not_called()
                saved = store.load(self.pid)['runs']['d01'][0]
                self.assertEqual(saved['state'], 'completed')
                self.assertEqual(saved['result'], 'clips/result.mp4')
                self.assertNotIn('review', saved)
            self.assertEqual([c[1] for c in calls], ['recover-task-id', 'resume'])

    def test_cold_refresh_and_get_do_not_mutate_or_query_history(self):
        store.update(self.pid, lambda d: d['runs']['d01'].append(
            {**self.run, 'rev': 'r2', 'state': 'completed', 'result': 'clips/result.mp4', 'review': {'status': 'running'}}))
        before = (self.directory / 'project.json').read_bytes()
        with patch.object(runtime_env, 'apply'):
            server = importlib.import_module('server')
        with patch.object(jobs, 'snapshot', return_value={}), patch.object(store, 'update', side_effect=AssertionError('cold GET writes')), \
                patch.object(segment_review, 'start', side_effect=AssertionError('cold review')), \
                patch.object(gateway, 'video_refresh', side_effect=AssertionError('cold refresh')):
            jimmy_bridge.refresh_all(self.pid)
            handler = server.Handler.__new__(server.Handler)
            value = handler._project_api('GET', self.pid, [], {})
            self.assertEqual(value['id'], self.pid)
        self.assertEqual(before, (self.directory / 'project.json').read_bytes())

    def test_live_poll_ignores_unrelated_historical_review(self):
        store.update(self.pid, lambda d: d['runs']['d01'].append(
            {**self.run, 'rev': 'r2', 'state': 'completed', 'result': 'clips/result.mp4', 'review': {'status': 'running'}}))
        with patch.object(jobs, 'snapshot', return_value={'gen:d01:r1': {'state': 'running'}}), \
                patch.object(gateway, 'video_refresh') as refresh, patch.object(segment_review, 'start') as review:
            jimmy_bridge.refresh_all(self.pid)
        refresh.assert_called_once_with(self.pid, 'd01', 'r1')
        review.assert_not_called()

    def test_explicit_recovery_of_completed_result_leaves_it_unchecked(self):
        self.child('completed', local_path=str(self.clip))
        store.update(self.pid, lambda d: d['runs']['d01'][0].update(state='completed', result='clips/result.mp4'))
        with patch.object(jobs, 'snapshot', return_value={}), \
                patch.object(jobs, 'start', side_effect=lambda pid, key, fn, label: fn()), \
                patch.object(config, 'jimmy_env_file', return_value=self.tmp / 'fake.env'), \
                patch.object(jimmy_bridge, '_call', return_value='{}'), patch.object(segment_review, 'start') as review:
            jimmy_bridge.resume(self.pid, 'd01', 'r1')
        review.assert_not_called()
        self.assertEqual(store.load(self.pid)['runs']['d01'][0]['state'], 'completed')
        self.assertNotIn('review', store.load(self.pid)['runs']['d01'][0])


if __name__ == '__main__':
    unittest.main()
