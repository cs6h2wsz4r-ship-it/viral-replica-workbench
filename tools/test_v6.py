"""V6 manual checks and explicit repair lifecycle; only synthetic local evidence."""
import copy
import importlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from console import director, final_preview, gateway, jimmy_bridge, jobs, llm, media, segment_boards, segment_plan, segment_review, store, storyboard_edits, storyboard_images
from console.gateway import errors, usage
from tools import test_director_sb3 as fixtures
from tools import test_v5a

ROOT = Path(__file__).resolve().parents[1]


class V6Tests(unittest.TestCase):
    def setUp(self):
        fixtures.DirectorSB3Tests.setUp(self)
        data = test_v5a.LiveCastingTests.fixture(self)
        rows = [test_v5a.LiveCastingTests.fields(self, data, s['id']) for s in data['segments_plan']['segments']]
        for row in rows:
            row['prompt'] += '。'
        data['director'] = director.validate_plan({'segments': rows}, data, self.d)
        self.frames = []
        self.workers = []
        self.enterContext(patch.object(gateway, 'reason_checked', side_effect=AssertionError('mock the model')))
        self.enterContext(patch.object(jobs, 'start', side_effect=lambda pid, key, fn, label: self.workers.append(fn) or {'state': 'running'}))
        self.enterContext(patch.object(jobs, 'snapshot', return_value={}))
        self.enterContext(patch.object(media, 'probe', return_value={'duration': 4, 'video_duration': 4, 'audio_stream_count': 0}))
        self.enterContext(patch.object(media, 'extract_frame', side_effect=self.frame))
        for row in data['director']['segments']:
            path = self.d / f'clips/{row["id"]}.mp4'; path.write_bytes(b'fake video')
            refs = director.references(data, row, self.d)
            frozen = segment_review.freeze_inputs(data, row, refs, self.d, self.d / f'packages/{row["id"]}')
            row['selected_run'] = 'r1'
            data.setdefault('runs', {})[row['id']] = [dict(rev='r1', kind='director', state='completed', result=f'clips/{row["id"]}.mp4',
                billing_seconds=4, billing_note='输出 4s', review_inputs=frozen, review=self.report())]
        store.update(self.pid, lambda d: d.update(data))

    assertIsNotNone = unittest.TestCase.assertIsNotNone

    def frame(self, clip, second, output):
        self.frames.append(second)
        Image.new('RGB', (12, 20), 'gray').save(output)
        return output

    def report(self):
        return dict(status='completed', verdict='fix', summary='需要修改', issues=[dict(start=.5, end=1.5, level='high', what='目标外观没出现', fix='redraw_cell', cell='c01', how='出修改方案')])

    def run_record(self):
        return store.load(self.pid)['runs']['d1'][0]

    def fix(self):
        self.assertTrue((ROOT / 'console/fix_plan.py').is_file(), '缺少修改方案模块')
        return importlib.import_module('console.fix_plan')

    def output(self, redraw=True):
        return dict(redraw=[dict(cell='c01', why='目标外观要明确入镜', request='让目标外观明确入镜')] if redraw else [],
                    prompt=self.run_record()['review_inputs']['segment']['prompt'] + '动作结束后保持稳定。', why='补齐目标外观')

    def plan(self, redraw=True):
        with patch.object(gateway, 'reason_checked', return_value=(self.output(redraw), {})):
            return self.fix().generate(self.pid, 'd1', 'r1', {'issues': [0], 'note': '结尾保持稳定'})['runs']['d1'][0]['fix_plan']

    def review(self, side_effect=None, value=None):
        segment_review.start(self.pid, 'd1', 'r1', force=True)
        with patch.object(gateway, 'reason_checked', side_effect=side_effect, return_value=(value or self.report(), {})) as reason:
            self.workers.pop()()
        return reason

    def test_review_normalizes_real_numeric_strings_and_minor_overrun(self):
        issue = {**self.report()['issues'][0], 'start': ' 2.9 ', 'end': '4.4', 'cell': ' C01 ', 'level': ' HIGH ', 'fix': ' REDRAW_CELL '}
        report = segment_review.validate(dict(verdict='fix', summary='问题', issues=[issue]), 4, ['c01'])
        self.assertEqual(report['issues'][0], {**issue, 'start': 2.9, 'end': 4, 'cell': 'c01', 'level': 'high', 'fix': 'redraw_cell'})

    def test_review_swaps_times_drops_only_bad_rows_and_counts(self):
        good = {**self.report()['issues'][0], 'start': '3', 'end': '1'}
        report = segment_review.validate(dict(verdict='fix', summary='问题', issues=[good, {**good, 'what': ''}, {**good, 'start': 'bad'}]), 4, ['c01'])
        self.assertEqual((report['issues'][0]['start'], report['issues'][0]['end']), (1, 3))
        self.assertEqual(report['dropped_issues'], 2)
        with self.assertRaises(ValueError):
            segment_review.validate(dict(verdict='fix', summary='问题', issues=[{**good, 'start': 'bad'}]), 4, ['c01'])

    def test_video_completion_does_not_schedule_a_check(self):
        store.update(self.pid, lambda d: d['runs']['d1'][0].update(state='running', review={}))
        def finish(*args):
            store.update(self.pid, lambda d: d['runs']['d1'][0].update(state='completed'))
        with patch.object(jimmy_bridge, '_refresh', side_effect=finish), patch.object(jimmy_bridge, '_update_retryability'), patch.object(jimmy_bridge, '_schedule_video_query'), patch.object(segment_review, 'start') as start:
            jimmy_bridge.refresh(self.pid, 'd1', 'r1', review_completed=True)
        start.assert_not_called()

    def test_only_explicitly_running_check_can_resume_without_force(self):
        store.update(self.pid, lambda d: d['runs']['d1'][0].pop('review'))
        segment_review.start(self.pid, 'd1', 'r1')
        self.assertEqual(self.workers, [])
        store.update(self.pid, lambda d: d['runs']['d1'][0].update(review={'status': 'running'}))
        segment_review.start(self.pid, 'd1', 'r1')
        self.assertEqual(len(self.workers), 1)
        with patch.object(gateway, 'reason_checked', return_value=(self.report(), {})):
            self.workers.pop()()
        self.assertEqual(self.run_record()['review']['status'], 'completed')

    def test_transient_retry_once_and_safe_failure_reason(self):
        failure = TimeoutError('Authorization: Bearer sk-sensitive request={secret: hidden}')
        reason = self.review(side_effect=failure)
        self.assertEqual(reason.call_count, 2)
        review = self.run_record()['review']
        self.assertEqual(review['error_reason'], '网络超时')
        self.assertEqual(review['error_kind'], 'transient')
        self.assertNotIn('sensitive', json.dumps(store.load(self.pid)))
        self.assertNotIn('hidden', json.dumps(store.load(self.pid)))
        self.assertIn('检查视频', review['error'])

    def test_retry_can_succeed_without_changing_generation_selection(self):
        reason = self.review(side_effect=[ConnectionError('connection reset'), (self.report(), {})])
        self.assertEqual(reason.call_count, 2)
        self.assertEqual(self.run_record()['review']['status'], 'completed')
        self.assertEqual(director.get_segment(store.load(self.pid), 'd1')['selected_run'], 'r1')

    def test_format_failure_is_not_retried_and_usage_classifies_disconnect(self):
        reason = self.review(side_effect=ValueError('invalid JSON secret=hidden'))
        self.assertEqual(reason.call_count, 1)
        self.assertEqual(self.run_record()['review']['error_reason'], '模型返回格式无法识别')
        for failure in (RuntimeError('Remote end closed connection without response'), RuntimeError('HTTP 503 unavailable')):
            self.assertEqual(errors.classify_error(failure), 'transient')
            state, token = usage.begin(self.pid, 'director', 'official', 'fake', 'reason')
            try: usage.finish(state, error=failure)
            finally: usage.reset(token)
        self.assertTrue(all(row['error_kind'] == 'transient' for row in store.load(self.pid)['usage']))

    def test_accept_undo_and_new_check_clears_acceptance_and_plan(self):
        self.assertTrue(hasattr(segment_review, 'accept'), '缺少接受接口')
        segment_review.accept(self.pid, 'd1', 'r1', {'accepted': True})
        self.assertTrue(self.run_record()['review']['accepted']['at'])
        self.assertEqual(final_preview.review_warnings(store.load(self.pid), [{'id': 'd1', 'rev': 'r1'}]), [])
        segment_review.accept(self.pid, 'd1', 'r1', {'accepted': False})
        self.assertNotIn('accepted', self.run_record()['review'])
        segment_review.accept(self.pid, 'd1', 'r1', {'accepted': True})
        store.update(self.pid, lambda d: d['runs']['d1'][0].update(fix_plan={'id': 'old'}))
        self.review()
        self.assertNotIn('accepted', self.run_record()['review'])
        self.assertNotIn('fix_plan', self.run_record())

    def test_failed_recheck_preserves_acceptance_and_can_undo_it(self):
        segment_review.accept(self.pid, 'd1', 'r1', {'accepted': True})
        self.review(side_effect=TimeoutError('offline timeout'))
        self.assertTrue(self.run_record()['review']['accepted']['at'])
        segment_review.accept(self.pid, 'd1', 'r1', {'accepted': False})
        self.assertNotIn('accepted', self.run_record()['review'])

    def test_real_gateway_orchestration_records_safe_usage_for_both_attempts(self):
        segment_review.start(self.pid, 'd1', 'r1', force=True)
        with patch.object(gateway, 'reason_checked', side_effect=lambda step, request, **kw: gateway._reason(step, request, checked=True, **kw)), \
                patch.object(llm, 'chat_json_checked', side_effect=TimeoutError('network timed out Authorization: Bearer sk-hidden request={secret: sensitive}')):
            self.workers.pop()()
        data = store.load(self.pid)
        self.assertEqual(len(data['usage']), 2)
        self.assertTrue(all(row['error_kind'] == 'transient' for row in data['usage']))
        serialized = json.dumps(data)
        for secret in ('sk-hidden', 'sensitive', 'Authorization', 'request={'):
            self.assertNotIn(secret, serialized)

    def test_wrapped_network_timeout_usage_is_transient(self):
        from urllib.error import URLError
        self.assertEqual(errors.classify_error(URLError(TimeoutError('timed out'))), 'transient')

    def test_fix_inputs_include_selected_issue_note_current_cells_and_frozen_prompt(self):
        value = self.output()
        with patch.object(gateway, 'reason_checked', return_value=(value, {})) as reason:
            saved = self.fix().generate(self.pid, 'd1', 'r1', {'issues': [0], 'note': '结尾保持稳定'})
        reason.assert_called_once()
        self.assertEqual(reason.call_args.args[0], 'director')
        system = reason.call_args.args[1][0]['content']
        self.assertTrue(system.endswith(self.fix().FIX_SYSTEM))
        self.assertNotIn('"segments":[', system)
        self.assertNotIn('check 写', system)
        text = json.dumps(reason.call_args.args[1], ensure_ascii=False)
        for expected in ('目标外观没出现', '结尾保持稳定', 'c01', 'c02', 'gen_images', '相邻段'):
            self.assertIn(expected, text)
        plan = saved['runs']['d1'][0]['fix_plan']
        self.assertEqual(plan['prompt_changes'], [{'before': '', 'after': '动作结束后保持稳定。'}])
        self.assertEqual(plan['base_prompt'], self.run_record()['review_inputs']['segment']['prompt'])
        self.assertEqual(plan['base_board'], 'v1')
        self.assertEqual(plan['video_seconds'], 4)

    def test_fix_rejects_foreign_redraw_and_invalid_prompt(self):
        for change in ({'redraw': [dict(cell='c04', why='修改', request='修改')]}, {'prompt': '图99 不存在的图片'}):
            with patch.object(gateway, 'reason_checked', return_value=({**self.output(), **change}, {})):
                with self.assertRaises(ValueError): self.fix().generate(self.pid, 'd1', 'r1', {'issues': [0], 'note': ''})

    def test_note_only_plan_and_issue_selection_validation(self):
        with patch.object(gateway, 'reason_checked', return_value=(self.output(False), {})):
            self.fix().generate(self.pid, 'd1', 'r1', {'issues': [], 'note': '补充意见'})
        for body in ({'issues': [], 'note': ''}, {'issues': [-1], 'note': ''}, {'issues': [True], 'note': ''}, {'issues': [99], 'note': ''}):
            with self.assertRaises(ValueError): self.fix().generate(self.pid, 'd1', 'r1', body)

    def test_selection_invalidation_prevents_stale_response_commit(self):
        fix = self.fix()
        def reason(*args, **kwargs):
            fix.update(self.pid, 'd1', 'r1', {'issues': [], 'note': '改了意见'})
            return self.output(), {}
        with patch.object(gateway, 'reason_checked', side_effect=reason):
            with self.assertRaises(ValueError): fix.generate(self.pid, 'd1', 'r1', {'issues': [0], 'note': ''})
        self.assertNotIn('fix_plan', self.run_record())

    def test_changed_board_invalidates_plan_and_view_is_read_only(self):
        plan = self.plan(False)
        state = segment_boards.state_for(store.load(self.pid), 'd1')
        image = self.d / state['versions'][0]['cells']['c02']; Image.new('RGB', (30, 50), 'red').save(image)
        before = store.load(self.pid)
        self.assertEqual(self.fix().view(before, 'd1', before['runs']['d1'][0], self.d)['status'], 'stale')
        self.assertEqual(store.load(self.pid), before)
        with self.assertRaises(ValueError): self.fix().apply(self.pid, 'd1', 'r1', {'plan_id': plan['id']})

    def test_apply_without_redraw_preserves_selection_and_is_idempotent(self):
        plan = self.plan(False)
        saved = self.fix().apply(self.pid, 'd1', 'r1', {'plan_id': plan['id']})
        row = director.get_segment(saved, 'd1')
        self.assertEqual(row['prompt'], plan['prompt']); self.assertEqual(row['prompt_source'], 'fix_plan')
        self.assertEqual(row['selected_run'], 'r1')
        self.assertEqual(saved['runs']['d1'][0]['fix_plan']['status'], 'ready_video')
        self.assertEqual(self.fix().apply(self.pid, 'd1', 'r1', {'plan_id': plan['id']}), saved)

    def test_apply_binds_prompt_to_current_board_when_old_authoring_is_stale(self):
        state = segment_boards.state_for(store.load(self.pid), 'd1')
        Image.new('RGB', (30, 50), 'red').save(self.d / state['versions'][0]['cells']['c02'])
        self.assertTrue(director.view(store.load(self.pid), self.d)['segments'][0]['stale'])
        plan = self.plan(False)
        saved = self.fix().apply(self.pid, 'd1', 'r1', {'plan_id': plan['id']})
        self.assertFalse(director.view(saved, self.d)['segments'][0]['stale'])

    def test_apply_clears_obsolete_structure_rewrite_marker(self):
        store.update(self.pid, lambda d: director.get_segment(d, 'd1').update(structure_needs_rewrite=True, needs_rewrite=True))
        plan = self.plan(False)
        saved = self.fix().apply(self.pid, 'd1', 'r1', {'plan_id': plan['id']})
        row = director.get_segment(saved, 'd1')
        self.assertFalse(row['needs_rewrite'])
        self.assertNotIn('structure_needs_rewrite', row)

    def test_paid_video_confirmation_rejects_invalidated_or_cancelled_fix_plan(self):
        from tools import director_sb3_bridge_cases as bridge_fixtures
        from console import config
        for action in ('invalidate', 'cancel', 'replace'):
            with self.subTest(action=action):
                plan = self.plan(False)
                self.fix().apply(self.pid, 'd1', 'r1', {'plan_id': plan['id']})
                with patch.object(jimmy_bridge, '_call', side_effect=lambda args, cwd, **kw: bridge_fixtures.DirectorBridgeTests.package(self, args, cwd, **kw)):
                    preview = jimmy_bridge.director_preview(self.pid, 'd1')
                if action == 'replace':
                    self.plan(False)
                else:
                    self.fix().update(self.pid, 'd1', 'r1', {'action': action, 'plan_id': plan['id'], 'issues': [], 'note': '改了意见'})
                with patch.object(config, 'jimmy_configured', return_value=True), patch.object(jimmy_bridge, '_call') as cli, \
                        patch.object(jimmy_bridge, '_start_generate') as submit:
                    with self.assertRaisesRegex(ValueError, '方案'):
                        jimmy_bridge.confirm(self.pid, 'd1', preview['rev'], preview['job_sha256'])
                    cli.assert_not_called(); submit.assert_not_called()
                with patch.object(jimmy_bridge, '_call', side_effect=lambda args, cwd, **kw: bridge_fixtures.DirectorBridgeTests.package(self, args, cwd, **kw)):
                    manual = jimmy_bridge.director_preview(self.pid, 'd1')
                self.assertNotIn('fix_plan_binding', manual)

    def test_fix_generation_does_not_commit_over_changed_board(self):
        def reason(*args, **kwargs):
            state = segment_boards.state_for(store.load(self.pid), 'd1')
            Image.new('RGB', (30, 50), 'red').save(self.d / state['versions'][0]['cells']['c02'])
            return self.output(), {}
        with patch.object(gateway, 'reason_checked', side_effect=reason):
            with self.assertRaises(ValueError): self.fix().generate(self.pid, 'd1', 'r1', {'issues': [0], 'note': ''})
        self.assertNotIn('fix_plan', self.run_record())

    def test_fix_generation_does_not_commit_over_changed_result_file(self):
        def reason(*args, **kwargs):
            (self.d / self.run_record()['result']).write_bytes(b'changed video')
            return self.output(), {}
        with patch.object(gateway, 'reason_checked', side_effect=reason):
            with self.assertRaises(ValueError): self.fix().generate(self.pid, 'd1', 'r1', {'issues': [0], 'note': ''})
        self.assertNotIn('fix_plan', self.run_record())

    def test_stale_review_worker_cannot_replace_new_result(self):
        segment_review.start(self.pid, 'd1', 'r1', force=True)
        old = self.workers.pop()
        segment_review.start(self.pid, 'd1', 'r1', force=True)
        new = self.workers.pop()
        with patch.object(gateway, 'reason_checked', return_value=(dict(verdict='pass', summary='新结果', issues=[]), {})):
            new()
        with patch.object(gateway, 'reason_checked', return_value=(self.report(), {})):
            old()
        self.assertEqual(self.run_record()['review']['summary'], '新结果')

    def test_http_contracts_return_projects_without_paid_requests(self):
        from console import runtime_env
        with patch.object(runtime_env, 'apply'):
            server = importlib.import_module('server')
        handler = server.Handler.__new__(server.Handler)
        endpoints = [
            ('POST', ['runs', 'd1', 'r1', 'fix-plan'], self.fix(), 'generate'),
            ('PUT', ['runs', 'd1', 'r1', 'fix-plan'], self.fix(), 'update'),
            ('POST', ['runs', 'd1', 'r1', 'fix-plan', 'apply'], self.fix(), 'apply'),
            ('POST', ['runs', 'd1', 'r1', 'review', 'accept'], segment_review, 'accept'),
            ('POST', ['final-preview', 'check'], final_preview, 'check'),
            ('POST', ['final-preview', 'check', 'accept'], final_preview, 'accept_check')]
        for method, rest, module, action in endpoints:
            with self.subTest(endpoint=rest), patch.object(handler, '_body_json', return_value={}), \
                    patch.object(server, 'project_view', side_effect=lambda data: data), \
                    patch.object(module, action, return_value={'id': self.pid}) as operation, \
                    patch.object(gateway, 'image_confirm', side_effect=AssertionError('paid call')), \
                    patch.object(gateway, 'video_confirm', side_effect=AssertionError('paid call')):
                self.assertEqual(handler._project_api(method, self.pid, rest, {})['id'], self.pid)
                operation.assert_called_once()

    def image_preview(self, plan, cid='c01'):
        def preview(*args, **kwargs):
            iid = store.new_id('img'); rec = {'id': iid, 'state': 'pending_confirmation'}
            store.update(self.pid, lambda d: d.setdefault('images', {}).__setitem__(iid, rec))
            return rec
        request = next(r['request'] for r in plan['redraw'] if r['cell'] == cid)
        with patch.object(gateway, 'image_preview', side_effect=preview):
            return storyboard_edits.preview(self.pid, 'd1', {'cell_id': cid, 'request': request, 'fix_plan': {'rev': 'r1', 'id': plan['id']}})

    def complete_image(self, rec):
        output = self.d / 'assets/fake-result.png'; Image.new('RGB', (120, 180), 'blue').save(output)
        storyboard_images.complete(self.pid, rec, output)

    def test_own_redraw_resigns_prompt_then_manual_redraw_invalidates(self):
        plan = self.plan(); self.fix().apply(self.pid, 'd1', 'r1', {'plan_id': plan['id']})
        rec = self.image_preview(plan); self.complete_image(rec)
        data = store.load(self.pid); row = director.get_segment(data, 'd1'); current = self.run_record()['fix_plan']
        self.assertEqual(row['prompt'], plan['prompt'])
        self.assertEqual(row['inputs_sig'], director.segment_inputs_sig(data, self.d, 'd1'))
        self.assertFalse(director.view(data, self.d)['segments'][0]['stale'])
        self.assertEqual(current['completed_cells'], ['c01']); self.assertEqual(current['status'], 'ready_video')
        self.assertEqual(current['base_board'], 'v1'); self.assertEqual(current['expected_board'], 'v2')
        manual = copy.deepcopy(rec); manual['id'] = 'manual'; manual['storyboard_context'].pop('fix_plan')
        manual['storyboard_context']['base_current'] = 'v2'; manual['storyboard_context']['base_cells'] = segment_boards.current(data, 'd1')['cells']
        self.complete_image(manual)
        data = store.load(self.pid)
        self.assertTrue(director.view(data, self.d)['segments'][0]['stale'])
        self.assertEqual(self.fix().view(data, 'd1', data['runs']['d1'][0], self.d)['status'], 'stale')

    def test_partial_redraw_failure_retry_cancel_keeps_successful_cells(self):
        output = self.output(); output['redraw'].append(dict(cell='c02', why='补齐', request='补齐结尾'))
        with patch.object(gateway, 'reason_checked', return_value=(output, {})):
            plan = self.fix().generate(self.pid, 'd1', 'r1', {'issues': [0], 'note': ''})['runs']['d1'][0]['fix_plan']
        self.fix().apply(self.pid, 'd1', 'r1', {'plan_id': plan['id']})
        self.complete_image(self.image_preview(plan))
        rec = self.image_preview(plan, 'c02')
        store.update(self.pid, lambda d: d['images'][rec['id']].update(state='failed', error='request body secret'))
        data = store.load(self.pid); status = self.fix().view(data, 'd1', data['runs']['d1'][0], self.d)
        self.assertEqual(status['status'], 'failed'); self.assertEqual(status['completed_cells'], ['c01'])
        self.assertNotIn('secret', status.get('error_reason', ''))
        retry = self.image_preview(plan, 'c02'); self.assertNotEqual(retry['id'], rec['id'])
        saved = self.fix().update(self.pid, 'd1', 'r1', {'action': 'cancel', 'plan_id': plan['id']})
        self.assertEqual(saved['runs']['d1'][0]['fix_plan']['status'], 'cancelled')
        self.assertEqual(segment_boards.current(saved, 'd1')['id'], 'v2')
        self.complete_image(retry)
        data = store.load(self.pid)
        self.assertTrue(director.view(data, self.d)['segments'][0]['stale'])

    def test_old_fix_image_completion_cannot_resign_after_step03_change(self):
        plan = self.plan(); self.fix().apply(self.pid, 'd1', 'r1', {'plan_id': plan['id']})
        rec = self.image_preview(plan)
        store.update(self.pid, lambda d: d['breakdown'].update(scene='已改动'))
        self.complete_image(rec)
        data = store.load(self.pid)
        self.assertTrue(director.view(data, self.d)['segments'][0]['stale'])

    def final(self):
        self.assertTrue(hasattr(final_preview, 'check'), '缺少整片检查')
        path = self.d / 'final/preview.mp4'; path.write_bytes(b'fake final')
        store.update(self.pid, lambda d: d.update(final_preview=dict(file='final/preview.mp4', duration=8, segments=[{'id': 'd1', 'rev': 'r1'}, {'id': 'd2', 'rev': 'r1'}])))

    def final_value(self, segment='d2'):
        return dict(verdict='fix', summary='接缝需要处理', issues=[dict(start='3.9', end='4.4', level='HIGH', segment=segment, what='接缝外观不一致')])

    def test_final_check_inputs_have_seam_frames_and_segment_boundaries(self):
        self.final()
        with patch.object(media, 'probe', side_effect=lambda p: {'duration': 8 if p.name == 'preview.mp4' else 4, 'video_duration': 8 if p.name == 'preview.mp4' else 4}):
            final_preview.check(self.pid)
            with patch.object(gateway, 'reason_checked', return_value=(self.final_value(), {})) as reason: self.workers.pop()()
        for second in (2.5, 2.75, 3.75, 4, 4.25, 5.5, 7): self.assertIn(second, self.frames)
        text = json.dumps(reason.call_args.args[1], ensure_ascii=False)
        for expected in ('d1', 'd2', 'what', 'start', 'end', '接缝'): self.assertIn(expected, text)
        check = store.load(self.pid)['final_preview']['check']
        self.assertEqual(check['status'], 'completed'); self.assertEqual(check['issues'][0]['segment'], 'd2')

    def test_final_invalid_segment_drops_one_issue_and_rejects_all_bad(self):
        self.final(); value = self.final_value('missing')
        final_preview.check(self.pid)
        with patch.object(gateway, 'reason_checked', return_value=(value, {})): self.workers.pop()()
        self.assertEqual(store.load(self.pid)['final_preview']['check']['status'], 'failed')

    def test_final_accept_undo_file_guard_and_reassembly_invalidation(self):
        self.final(); final_preview.check(self.pid)
        with patch.object(gateway, 'reason_checked', return_value=(self.final_value(), {})): self.workers.pop()()
        final_preview.accept_check(self.pid, {'accepted': True, 'file': 'final/preview.mp4'})
        self.assertTrue(store.load(self.pid)['final_preview']['check']['accepted']['at'])
        final_preview.accept_check(self.pid, {'accepted': False, 'file': 'final/preview.mp4'})
        self.assertNotIn('accepted', store.load(self.pid)['final_preview']['check'])
        with self.assertRaises(ValueError): final_preview.accept_check(self.pid, {'accepted': True, 'file': 'old.mp4'})
        with patch.object(media, 'concat'): final_preview.generate(self.pid)
        self.assertNotIn('check', store.load(self.pid)['final_preview'])

    def test_final_result_cannot_attach_to_replaced_file(self):
        self.final(); final_preview.check(self.pid)
        store.update(self.pid, lambda d: d.update(final_preview={'file': 'another.mp4', 'duration': 8}))
        with patch.object(gateway, 'reason_checked', return_value=(self.final_value(), {})): self.workers.pop()()
        self.assertNotIn('check', store.load(self.pid)['final_preview'])

    def test_final_recheck_failure_keeps_acceptance_and_undo_remains_available(self):
        self.final(); final_preview.check(self.pid)
        with patch.object(gateway, 'reason_checked', return_value=(self.final_value(), {})): self.workers.pop()()
        final_preview.accept_check(self.pid, {'accepted': True, 'file': 'final/preview.mp4'})
        with self.assertRaises(ValueError): final_preview.check(self.pid, {'file': 'old.mp4'})
        final_preview.check(self.pid, {'file': 'final/preview.mp4'})
        with patch.object(gateway, 'reason_checked', side_effect=TimeoutError('offline timeout')): self.workers.pop()()
        self.assertTrue(store.load(self.pid)['final_preview']['check']['accepted']['at'])
        final_preview.accept_check(self.pid, {'accepted': False, 'file': 'final/preview.mp4'})
        self.assertNotIn('accepted', store.load(self.pid)['final_preview']['check'])

    def test_final_normalization_drops_invalid_segment_only(self):
        self.assertTrue(hasattr(final_preview, 'validate_check'))
        good = {**self.final_value()['issues'][0], 'start': '8.2', 'end': '3.9'}
        value = self.final_value(); value['issues'] = [good, {**good, 'segment': 'missing'}]
        checked = final_preview.validate_check(value, 8, ['d1', 'd2'])
        self.assertEqual(checked['dropped_issues'], 1)
        self.assertEqual((checked['issues'][0]['start'], checked['issues'][0]['end']), (3.9, 8))

    def test_new_systems_and_actual_empty_context_requests_are_generic(self):
        from tools import test_v2c_generic_prompts as generic
        from console import config
        self.directory = self.d
        with patch.object(config, 'load', return_value={**config.DEFAULTS, 'OPENAI_API_KEY': 'mock-only'}):
            data, _ = generic.GenericPromptTests.fixture(self)
        content = dict(id='d01', what='', check=['外观一致'], prompt='图1 新画面；图2 人物；图3 人物；图4 商品。')
        data['director'] = director.validate_plan({'segments': [content]}, data, self.d)
        run = {'review_inputs': {'segment': director.get_segment(data, 'd01')}, 'review': self.report()}
        requests = [self.fix().FIX_SYSTEM, final_preview.FINAL_SYSTEM,
                    self.fix().messages(data, 'd01', run, [0], '', self.d)]
        folder = self.d / 'analysis/generic-final'; folder.mkdir()
        saved = {'timeline': [dict(id='d01', rev='r1', start=0, end=4, what='')], 'segments': [{'id': 'd01', 'rev': 'r1'}]}
        request, _, _ = final_preview.check_messages(data, saved, self.d, self.d / 'clips/d1.mp4', folder)
        requests.append(request)
        for request in requests:
            text = generic.text_only(request)
            self.assertIsNone(generic.BANNED.search(text), generic.BANNED.findall(text))


if __name__ == '__main__':
    unittest.main()
