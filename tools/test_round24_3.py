"""Round 24.3: frozen prechange decision corpus; no models or workbench server."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import config, jimmy_bridge, replacement_guard as guard


class Round24_3Tests(unittest.TestCase):
    def test_every_code_keeps_prechange_blocking_and_preview_decision(self):
        cases = json.loads((ROOT / 'tools/fixtures/round24_3/preflight-decisions.json').read_text())
        seen, decisions, warnings = set(), set(), set()
        with patch.object(config, '_parse', side_effect=AssertionError('禁止读取配置')), \
                patch.object(config, 'load', return_value=dict(config.DEFAULTS)), \
                patch('urllib.request.urlopen', side_effect=AssertionError('禁止真实网络')), \
                patch('console.motion.compare_actions', return_value=(['新增动作'], [])):
            for case in cases:
                with self.subTest(case=case['name']):
                    data, task = copy.deepcopy(case['data']), copy.deepcopy(case['task'])
                    if case['name'] == 'caption_bbox':
                        task['blank_captions'] = True  # 24.11: bbox is required only after opting in.
                    result = jimmy_bridge.replacement_check(data, task, compare_motion=case['compare_motion'])
                    actual = {'blocking': bool(result['errors']), 'can_preview': not result['errors'],
                              'errors': result['errors'], 'codes': sorted(i['code'] for i in result['issues']),
                              'dialogue_codes': sorted(i['code'] for i in result.get('dialogue_conflicts', []))}
                    expected = copy.deepcopy(case['expected'])
                    if case['name'] == 'motion_additions':
                        expected['errors'].remove('新增动作')
                        self.assertEqual(result['motion_warning']['details'], ['新增动作'])
                    self.assertEqual(actual, expected)
                    seen.update(actual['codes']); warnings.update(actual['dialogue_codes']); decisions.add(actual['blocking'])
        self.assertEqual(seen, set(guard.ISSUE_INFO) | {'camera_scene_mismatch'})
        self.assertEqual(warnings, {'dialogue_conflict'})
        self.assertEqual(decisions, {False, True})

    def test_missing_people_metadata_identifies_each_control_without_changing_blocks(self):
        from tools.test_replacement_guard import fixture, task
        data = fixture()
        data['replacements']['characters'].append({'source_id': 'C2', 'action': 'replace', 'asset_id': 'other'})
        result = guard.check(data, task(image_ids=['product']))
        issue = next(i for i in result['issues'] if i['code'] == 'person_replace_missing_image')
        self.assertEqual(issue['affected'], [{'character_id': 'C1', 'asset_id': 'person'},
                                            {'character_id': 'C2', 'asset_id': 'other'}])
        self.assertEqual(len(issue['details']), 2)
        self.assertTrue(result['errors'])

    def test_person_conflict_metadata_targets_visible_character_and_image(self):
        from tools.test_replacement_guard import fixture, task
        data = fixture()
        data['breakdown']['characters'] = [{'id':'C1','appearance':'画外'}, {'id':'C2','appearance':'出镜'}]
        data['breakdown']['shots'] = [{'start':0,'end':3,'characters':['C2']}]
        data['replacements']['characters'] = [{'source_id':'C1','action':'keep'},
            {'source_id':'C2','action':'keep','asset_id':'person'}]
        issue = next(i for i in guard.check(data,task(image_ids=['person','product']))['issues']
                     if i['code'] == 'person_keep_with_image')
        self.assertEqual(issue['affected'], [{'asset_id':'person','character_id':'C2'}])

    def test_server_page_expiry_message_names_save_and_refresh(self):
        # The existing version gate remains unchanged. Exercise the actual handler
        # under mocks instead of opening a port or changing PAGE_BUILD conditions.
        from console import runtime_env
        with patch.object(runtime_env, 'apply'):
            import server
        handler = object.__new__(server.Handler)
        handler.path = '/api/projects/offline/replacements'
        handler.headers = {'X-Client-Build': 'old'}
        with patch.object(handler, '_json') as response, \
                patch.object(server, 'build_status', return_value={'build': 'new', 'restart_pending': False}), \
                patch('console.pipeline.save_replacements', side_effect=AssertionError('过期页面不能保存')):
            handler._dispatch('PUT')
        self.assertEqual(response.call_args.args[1], 409)
        self.assertIn('拒绝本次保存', response.call_args.args[0]['error'])
        self.assertIn('请刷新页面', response.call_args.args[0]['error'])


if __name__ == '__main__':
    from tools.run_tests_offline import audit
    sys.addaudithook(audit)
    unittest.main()
