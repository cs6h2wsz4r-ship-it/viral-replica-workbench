"""V8b mode switches: synthetic projects, stubbed reasoning, no paid calls."""
import copy
import json
import unittest
from unittest.mock import patch

from console import config, director, gateway, segment_boards, segment_plan, store
from console import storyboard_edits as edits
from tools import test_director_sb3 as fixtures
from tools import test_v2_segment_plan as planning
from tools.v2_fixture import configure


class V8bSuggestionTests(planning.SegmentFixture, unittest.TestCase):
    def test_generation_freezes_model_mode_and_save_preserves_original(self):
        self.fixture()
        value = self.value()
        # A response cannot invent a different historical recommendation.
        value['segments'][0]['mode_suggested'] = 'image'
        with patch.object(gateway, 'reason_checked', return_value=(value, {})) as reason:
            data = segment_plan.generate(self.pid)
        reason.assert_called_once()
        self.assertEqual([s.get('mode_suggested') for s in data['segments_plan']['segments']], ['video', 'image'])
        with patch.object(gateway, 'reason_checked', side_effect=AssertionError('save must not reason')):
            for mode in ('image', 'video', 'image'):
                data = segment_plan.save(self.pid, 'd01', {'mode': mode})
                row = segment_plan.get_segment(data, 'd01')
                self.assertEqual(row['mode'], mode)
                self.assertEqual(row['mode_suggested'], 'video')
                self.assertEqual(row['mode_reason'], value['segments'][0]['mode_reason'])
                self.assertEqual(segment_plan.get_segment(data, 'd02')['mode_suggested'], 'image')

    def test_legacy_suggestion_is_frozen_before_first_mode_save(self):
        data = self.generated()
        for row in data['segments_plan']['segments']:
            row.pop('mode_suggested', None)
        store.update(self.pid, lambda current: current.update(data))
        data = segment_plan.save(self.pid, 'd01', {'mode': 'image'})
        self.assertEqual(segment_plan.get_segment(data, 'd01').get('mode_suggested'), 'video')
        self.assertEqual(segment_plan.get_segment(data, 'd02').get('mode_suggested'), 'image')
        data = segment_plan.save(self.pid, 'd01', {'refs': ['C1']})
        self.assertEqual(segment_plan.get_segment(data, 'd01').get('mode_suggested'), 'video')

    def test_invalid_mode_or_suggestion_override_cannot_change_project(self):
        before = self.generated()
        for body in ({'mode': 'invalid'}, {'mode_suggested': 'image'}, {'mode': None}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                segment_plan.save(self.pid, 'd01', body)
            self.assertEqual(store.load(self.pid), before)


class V8bVisualBindingTests(unittest.TestCase):
    setUp = fixtures.DirectorSB3Tests.setUp
    segment = fixtures.DirectorSB3Tests.segment

    def fixture(self, legacy=False):
        data = fixtures.DirectorSB3Tests.raw_fixture(self, 4, 2)
        data = configure(data, self.d, [['c01', 'c02'], ['c03', 'c04']])
        for segment in data['segments_plan']['segments']:
            sid = segment['id']
            segment['mode_reason'] = '保留原片动作'
            if legacy:
                segment.pop('mode_suggested', None)
                # Exact pre-V8b segment and board hashes, independent of the new helper.
                ssig = segment_plan.digest({key: segment.get(key) for key in
                                           ('id', 'cells', 'mode', 'refs', 'source_start', 'source_end')})
                cells = [c for c in data['storyboard']['cells'] if c['id'] in segment['cells']]
                bsig = segment_plan.digest([ssig, data['storyboard']['built_at'], data['storyboard']['source_sig'], cells])
                state = segment_boards.state_for(data, sid)
                state['source']['segment_sig'] = ssig
                state.update(segment_sig=ssig, materials_segment_sig=ssig, storyboard_sig=bsig)
                for version in state['versions']:
                    version.update(segment_sig=ssig, storyboard_sig=bsig)
            else:
                segment['mode_suggested'] = segment['mode']
        store.update(self.pid, lambda current: current.update(data))
        value = dict(segments=[{k: v for k, v in self.segment(s['cells'], s['id']).items()
                               if k in ('id', 'what', 'check', 'prompt')} for s in data['segments_plan']['segments']])
        with patch.object(gateway, 'reason_checked', return_value=(value, {})):
            return director.generate(self.pid)

    def revise(self):
        answer = dict(changes='保留一个前景人物并补齐背景', keep='机位光线', why='删除多余人物')
        with patch.object(gateway, 'reason_checked', return_value=(answer, {})):
            return edits.revise_cell(self.pid, 'd1', {'cell_id': 'c01', 'request': '删除多出的人'})

    def test_mode_only_save_preserves_boards_plans_materials_and_cell_revision(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                self.fixture(legacy=legacy)
                self.revise()
                before = store.load(self.pid)
                state = copy.deepcopy(segment_boards.state_for(before, 'd1'))
                binding = edits._cell_revision_inputs_sig(before, 'd1', {'cell_id': 'c01', 'request': '删除多出的人'}, self.d)
                self.assertEqual(edits.warnings(before, 'd1', self.d), [])
                with patch.object(gateway, 'reason_checked', side_effect=AssertionError('mode save must not call a model')):
                    after = segment_plan.save(self.pid, 'd1', {'mode': 'image'})
                self.assertEqual(segment_boards.state_for(after, 'd1'), state)
                self.assertEqual(edits.warnings(after, 'd1', self.d), [])
                self.assertEqual(edits.cell_revision(after, 'd1', 'c01', self.d), state['cell_revisions']['c01'])
                self.assertEqual(edits._cell_revision_inputs_sig(after, 'd1', {'cell_id': 'c01', 'request': '删除多出的人'}, self.d), binding)
                self.assertEqual(segment_boards.state_for(after, 'd2'), segment_boards.state_for(before, 'd2'))
                expected = copy.deepcopy(before['director'])
                expected['segments'][0].update(inputs_sig=None, plan_sig=None)
                self.assertEqual(after['director'], expected)
                self.assertNotEqual(segment_plan.segment_sig(segment_plan.get_segment(after, 'd1')), before['director']['segments'][0]['plan_sig'])
                self.assertNotEqual(director.segment_inputs_sig(after, self.d, 'd1'), before['director']['segments'][0]['inputs_sig'])
                status = director.view(copy.deepcopy(after), self.d)
                self.assertTrue(status['ready'])
                self.assertTrue(status['segments'][0]['stale'])
                self.assertFalse(status['segments'][1]['stale'])

    def test_existing_single_cell_preview_remains_confirmable_after_mode_save(self):
        self.fixture(legacy=True)
        answer = dict(changes='删除多余人物并补齐背景', keep='机位', why='只保留一个主体')
        with patch.object(gateway, 'reason_checked', return_value=(answer, {})), \
                patch.object(config, 'load', return_value={**config.DEFAULTS, 'OPENAI_API_KEY': 'mock-only'}), \
                patch.object(gateway, 'image_confirm', side_effect=AssertionError('no paid request')):
            preview = edits.preview(self.pid, 'd1', {'cell_id': 'c01', 'request': '删除多出的人'})
        edits.verify_preview(self.pid, preview)
        segment_plan.save(self.pid, 'd1', {'mode': 'image'})
        edits.verify_preview(self.pid, preview)

    def test_mode_change_supersedes_only_pending_video_preview_and_retains_completed(self):
        data = self.fixture()
        data['runs'] = {'d1': [dict(rev='v1', state='completed', result='video/old.mp4'),
                              dict(rev='v2', state='pending_confirmation', kind='director')],
                        'd2': [dict(rev='v1', state='pending_confirmation', kind='director')]}
        data['director']['segments'][0]['selected_run'] = 'v1'
        store.update(self.pid, lambda current: current.update(data))
        same = segment_plan.save(self.pid, 'd1', {'mode': 'video'})
        self.assertEqual(same['runs'], data['runs'])
        after = segment_plan.save(self.pid, 'd1', {'mode': 'image'})
        self.assertEqual(after['runs']['d1'][1]['state'], 'superseded')
        self.assertEqual(after['runs']['d1'][0], data['runs']['d1'][0])
        self.assertEqual(after['runs']['d2'], data['runs']['d2'])
        self.assertEqual(after['director']['segments'][0]['selected_run'], 'v1')
        restored = segment_plan.save(self.pid, 'd1', {'mode': 'video'})
        self.assertEqual(restored['runs']['d1'][1]['state'], 'superseded')
        self.assertTrue(director.view(copy.deepcopy(restored), self.d)['segments'][0]['stale'])
        self.assertEqual(restored['director']['segments'][0]['prompt'], data['director']['segments'][0]['prompt'])

    def test_normalized_mode_noop_preserves_director_and_pending_preview(self):
        data = self.fixture()
        data['runs'] = {'d1': [dict(rev='v1', state='completed', result='video/old.mp4'),
                              dict(rev='v2', state='pending_confirmation', kind='director')]}
        data['director']['segments'][0]['selected_run'] = 'v1'
        store.update(self.pid, lambda current: current.update(data))
        for mode in (' VIDEO ', '\tVideo\n', 'video'):
            with self.subTest(mode=mode), \
                    patch.object(gateway, 'reason_checked', side_effect=AssertionError('no-op save must not reason')):
                store.update(self.pid, lambda current: current.update(copy.deepcopy(data)))
                after = segment_plan.save(self.pid, 'd1', {'mode': mode})
                self.assertEqual(segment_plan.get_segment(after, 'd1')['mode'], 'video')
                self.assertEqual(segment_plan.get_segment(after, 'd1')['mode_suggested'], 'video')
                self.assertEqual(after['director'], data['director'])
                self.assertEqual(after['runs'], data['runs'])
                self.assertEqual(after['segment_boards'], data['segment_boards'])
                self.assertFalse(director.view(copy.deepcopy(after), self.d)['segments'][0]['stale'])

    def test_replan_mode_only_change_reuses_visuals_and_original_legacy_hashes(self):
        data = self.fixture(legacy=True)
        self.revise()
        before = store.load(self.pid)
        plan = copy.deepcopy(data['segments_plan'])
        plan['segments'][0]['mode'] = 'image'
        # Explicit replanning assigns sequential IDs; keep the fixture IDs so
        # this case isolates the mode change from the existing ID-rebind tests.
        states = segment_boards.reuse_for_plan(before, plan, self.d)
        after = {**before, 'segments_plan': plan, 'segment_boards': {'segments': states}}
        state = segment_boards.state_for(after, 'd1')
        previous = segment_boards.state_for(before, 'd1')
        self.assertEqual(state['versions'], previous['versions'])
        self.assertEqual(state['plan'], previous['plan'])
        self.assertEqual(state['cell_revisions'], previous['cell_revisions'])
        self.assertEqual(edits.warnings(after, 'd1', self.d), [])

    def test_visual_signature_compatibility_never_accepts_changed_refs_or_source_times(self):
        original = self.fixture(legacy=True)
        for change in ({'refs': ['C1']}, {'source_start': .1}, {'source_end': 3.9}, {'cells': ['c01']}):
            with self.subTest(change=change):
                altered = copy.deepcopy(original)
                segment_plan.get_segment(altered, 'd1').update(change)
                notices = edits.warnings(altered, 'd1', self.d)
                self.assertTrue(segment_boards.STALE_WARNING in notices or edits.SOURCE_WARNING in notices)

    def test_refs_save_still_invalidates_visuals_and_cell_revision(self):
        self.fixture(legacy=True)
        self.revise()
        after = segment_plan.save(self.pid, 'd1', {'mode': 'image', 'refs': ['C1']})
        self.assertIn(segment_boards.STALE_WARNING, edits.warnings(after, 'd1', self.d))
        state = segment_boards.state_for(after, 'd1')
        self.assertNotEqual(edits.cell_revision(after, 'd1', 'c01', self.d), state['cell_revisions']['c01'])

    def test_read_only_legacy_views_preserve_project_and_director_binding(self):
        data = self.fixture(legacy=True)
        before = copy.deepcopy(data)
        with patch.object(store, 'update', side_effect=AssertionError('reads must not migrate')):
            self.assertFalse(director.view(copy.deepcopy(data), self.d)['stale'])
            self.assertEqual(edits.warnings(data, 'd1', self.d), [])
            self.assertFalse(edits.view(data, self.d)['d1']['plan_stale'])
            self.assertFalse(segment_plan.status(data, self.d)['stale'])
        self.assertEqual(data, before)
        self.assertEqual(store.load(self.pid), before)

    def test_mode_suggested_never_enters_model_messages_or_director_bindings(self):
        data = self.fixture(legacy=True)
        original = copy.deepcopy(data)
        before = (segment_plan.messages(data, self.d), director.messages(data, self.d),
                  director.messages(data, self.d, target=data['director']['segments'][0]))
        bindings = director.inputs_sig(data, self.d)
        for i, row in enumerate(data['segments_plan']['segments']):
            row['mode_suggested'] = ('image', 'video')[i]
        after = (segment_plan.messages(data, self.d), director.messages(data, self.d),
                 director.messages(data, self.d, target=data['director']['segments'][0]))
        for a, b in zip(before, after):
            self.assertEqual(json.dumps(a, ensure_ascii=False).encode(), json.dumps(b, ensure_ascii=False).encode())
            self.assertNotIn('mode_suggested', json.dumps(b, ensure_ascii=False))
        self.assertEqual(director.inputs_sig(data, self.d), bindings)
        self.assertEqual(store.load(self.pid), original)


if __name__ == '__main__':
    unittest.main()
