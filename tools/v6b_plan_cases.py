"""V6b plan consumption cases using synthetic projects and mocked transport."""
import copy
import importlib
import threading
import unittest
from unittest.mock import patch

from console import config, director, fix_plan, gateway, jimmy_bridge, store
from tools import director_sb3_bridge_cases, test_v6


class V6bPlanCases:
    setUp = test_v6.V6Tests.setUp
    frame = test_v6.V6Tests.frame
    report = test_v6.V6Tests.report
    run_record = test_v6.V6Tests.run_record
    fix = test_v6.V6Tests.fix
    output = test_v6.V6Tests.output
    plan = test_v6.V6Tests.plan

    def ready_preview(self):
        plan = self.plan(False)
        fix_plan.apply(self.pid, 'd1', 'r1', {'plan_id': plan['id']})
        return plan, self.video_preview()

    def video_preview(self, **kwargs):
        with patch.object(jimmy_bridge, '_call', side_effect=lambda args, cwd, **kw:
                          director_sb3_bridge_cases.DirectorBridgeTests.package(self, args, cwd, **kw)):
            return jimmy_bridge.director_preview(self.pid, 'd1', **kwargs)

    def confirm(self, preview):
        with patch.object(config, 'jimmy_configured', return_value=True), \
                patch.object(jimmy_bridge, '_call'), patch.object(jimmy_bridge, '_start_generate') as submit:
            jimmy_bridge.confirm(self.pid, 'd1', preview['rev'], preview['job_sha256'])
            submit.assert_called_once()

    def test_preview_does_not_consume_and_confirm_records_target_revision(self):
        plan, preview = self.ready_preview()
        self.assertEqual(self.run_record()['fix_plan']['status'], 'ready_video')
        self.assertEqual(preview['fix_plan_binding']['id'], plan['id'])
        self.confirm(preview)
        saved = store.load(self.pid)
        consumed = saved['runs']['d1'][0]['fix_plan']
        self.assertEqual(consumed['status'], 'video_started')
        self.assertEqual(consumed['video_rev'], preview['rev'])
        self.assertEqual(director.get_segment(saved, 'd1')['selected_run'], 'r1')
        self.assertNotIn('review', saved['runs']['d1'][-1])

    def test_historical_binding_is_derived_read_only_before_signature_checks(self):
        plan, preview = self.ready_preview()
        for state in ('submitting', 'running', 'completed', 'failed', 'unknown', 'query_pending'):
            with self.subTest(state=state):
                data = store.load(self.pid)
                data['runs']['d1'][-1]['state'] = state
                data['runs']['d1'][0]['fix_plan']['expected_sig'] = 'changed later'
                before = copy.deepcopy(data)
                viewed = fix_plan.view(data, 'd1', data['runs']['d1'][0], self.d)
                self.assertEqual(viewed['status'], 'video_started')
                self.assertEqual(viewed['video_rev'], preview['rev'])
                self.assertEqual(data, before)

    def test_unconfirmed_and_superseded_previews_never_consume(self):
        _, preview = self.ready_preview()
        for state in ('pending_confirmation', 'superseded', 'cancelled'):
            data = store.load(self.pid)
            data['runs']['d1'][-1]['state'] = state
            self.assertEqual(fix_plan.view(data, 'd1', data['runs']['d1'][0], self.d)['status'], 'ready_video')

    def test_consumed_plan_rejects_repeat_apply_and_explicit_preview_before_packaging(self):
        plan, preview = self.ready_preview()
        self.confirm(preview)
        with self.assertRaisesRegex(ValueError, '方案'):
            fix_plan.apply(self.pid, 'd1', 'r1', {'plan_id': plan['id']})
        with patch.object(jimmy_bridge, '_prepare_package') as package:
            with self.assertRaisesRegex(ValueError, '方案'):
                jimmy_bridge.director_preview(self.pid, 'd1', fix_plan={'id': plan['id'], 'rev': 'r1'})
            package.assert_not_called()
        manual = self.video_preview()
        self.assertNotIn('fix_plan_binding', manual)

    def test_legacy_consumed_plan_rejects_an_existing_paid_confirmation(self):
        plan, first = self.ready_preview()
        second = self.video_preview()
        def legacy(data):
            data['runs']['d1'][1].update(state='running')
        store.update(self.pid, legacy)
        with patch.object(config, 'jimmy_configured') as configured, patch.object(jimmy_bridge, '_call') as cli, \
                patch.object(jimmy_bridge, '_start_generate') as submit:
            with self.assertRaisesRegex(ValueError, '方案'):
                jimmy_bridge.confirm(self.pid, 'd1', second['rev'], second['job_sha256'])
            configured.assert_not_called(); cli.assert_not_called(); submit.assert_not_called()
        self.assertEqual(self.run_record()['fix_plan']['status'], 'ready_video', 'legacy inference must not rewrite data')

    def test_concurrent_existing_previews_only_submit_once(self):
        _, first = self.ready_preview()
        second = self.video_preview()
        store.update(self.pid, lambda data: data['runs']['d1'][1].update(state='pending_confirmation'))
        barrier, results = threading.Barrier(2), []
        def confirm(preview):
            barrier.wait()
            try:
                jimmy_bridge.confirm(self.pid, 'd1', preview['rev'], preview['job_sha256'])
                results.append('ok')
            except (ValueError, jimmy_bridge.BridgeError):
                results.append('rejected')
        with patch.object(config, 'jimmy_configured', return_value=True), patch.object(jimmy_bridge, '_call'), \
                patch.object(jimmy_bridge, '_start_generate') as submit:
            threads = [threading.Thread(target=confirm, args=(preview,)) for preview in (first, second)]
            for thread in threads: thread.start()
            for thread in threads: thread.join(timeout=10)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(sorted(results), ['ok', 'rejected'])
            submit.assert_called_once()

    def test_rejected_confirmation_keeps_plan_ready(self):
        _, preview = self.ready_preview()
        with patch.object(config, 'jimmy_configured', return_value=False):
            with self.assertRaises(jimmy_bridge.BridgeError):
                jimmy_bridge.confirm(self.pid, 'd1', preview['rev'], preview['job_sha256'])
        self.assertEqual(self.run_record()['fix_plan']['status'], 'ready_video')

    def test_http_and_gateway_keep_explicit_plan_marker(self):
        from console import runtime_env
        plan, _ = self.ready_preview()
        marker = {'id': plan['id'], 'rev': 'r1'}
        with patch.object(runtime_env, 'apply'):
            server = importlib.import_module('server')
        handler = server.Handler.__new__(server.Handler)
        with patch.object(handler, '_body_json', return_value={'fix_plan': marker}), \
                patch.object(gateway, 'video_preview', return_value={}) as preview:
            handler._project_api('POST', self.pid, ['tasks', 'd1', 'preview'], {})
            preview.assert_called_once_with(self.pid, 'd1', fix_plan=marker)
        with patch.object(jimmy_bridge, '_call', side_effect=lambda args, cwd, **kw:
                          director_sb3_bridge_cases.DirectorBridgeTests.package(self, args, cwd, **kw)):
            preview = gateway.video_preview(self.pid, 'd1', fix_plan=marker)
        self.assertEqual(preview['fix_plan_binding'], {**marker, 'sid': 'd1'})
        self.confirm(preview)
        with patch.object(jimmy_bridge, '_prepare_package') as package:
            with self.assertRaisesRegex(jimmy_bridge.BridgeError, '方案'):
                gateway.video_preview(self.pid, 'd1', fix_plan=marker)
            package.assert_not_called()

    def test_new_plan_id_on_same_source_can_start_another_video(self):
        original, first = self.ready_preview()
        self.confirm(first)
        new = self.plan(False)
        self.assertNotEqual(new['id'], original['id'])
        fix_plan.apply(self.pid, 'd1', 'r1', {'plan_id': new['id']})
        preview = self.video_preview()
        self.assertEqual(preview['fix_plan_binding']['id'], new['id'])
        self.confirm(preview)
        self.assertEqual(self.run_record()['fix_plan']['video_rev'], preview['rev'])


class V6bPlanTests(V6bPlanCases, unittest.TestCase):
    pass
