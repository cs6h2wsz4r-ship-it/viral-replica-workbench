"""Round 15: offline structured preflight; incident copied before inspection."""
import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.project_fixture import fixture_path
from console import config, jimmy_bridge, llm, replacement_guard as guard


def incident_copy(directory):
    target = directory / 'project.json'
    shutil.copyfile(fixture_path('p1027180021-c16a'), target)
    return json.loads(target.read_text())


class FixWizardTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('禁止读取配置')))
        # Gateway routing reads explicit offline settings; credential parsing stays forbidden.
        self.enterContext(patch.object(config, "load", return_value=dict(config.DEFAULTS)))
        self.enterContext(patch.object(llm, 'chat_json', side_effect=AssertionError('禁止真实模型')))
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp')))
        self.data = incident_copy(self.temp)
        self.task = self.data['script']['tasks'][0]

    def test_incident_grouping_order_and_dependencies(self):
        check = guard.check(self.data, self.task)
        self.assertEqual(len([e for e in check['errors'] if '动作描述里仍有身体动作' in e]), 4)
        self.assertIn('issues', check)
        issues = check['issues']
        self.assertEqual([i['step'] for i in issues], sorted(i['step'] for i in issues))
        body = [i for i in issues if i['code'] == 'performance_has_motion']
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]['title'], '动作描述里有 4 处身体动作')
        self.assertEqual(len(body[0]['details']), 4)
        self.assertTrue(all(quote in self.task['action'] for quote in body[0]['details']))
        for issue in issues:
            self.assertTrue(all(issue.get(k) for k in ('code', 'step', 'title', 'fix')))
            if issue['step'] == '04':
                self.assertIn('完成前面的步骤后，重新生成新脚本即可', issue['fix'])
        check = jimmy_bridge.replacement_check(self.data, self.task)
        reversed_task = {**self.task, 'image_ids': list(reversed(self.task['image_ids']))}
        reversed_check = jimmy_bridge.replacement_check(self.data, reversed_task)
        self.assertIn('image1_not_person', [i['code'] for i in reversed_check['issues']])
        reversed_data = copy.deepcopy(self.data)
        reversed_data['script']['tasks'][0] = reversed_task
        reversed_data['replacement_checks'] = {self.task['id']: reversed_check}
        (ROOT / 'runtime/temp/fix-wizard-reversed-project.json').write_text(json.dumps(reversed_data, ensure_ascii=False))
        (ROOT / 'runtime/temp/fix-wizard-project.json').write_text(json.dumps(
            {**self.data, 'replacement_checks': {self.task['id']: check}}, ensure_ascii=False))

    def test_preview_error_preserves_http_400_and_issues(self):
        from console import runtime_env
        with patch.object(runtime_env, "apply"):
            import server
        check = jimmy_bridge.replacement_check(self.data, self.task)
        with patch.object(jimmy_bridge, 'replacement_check', return_value=check):
            with self.assertRaises(jimmy_bridge.BridgeError) as caught:
                jimmy_bridge._check_kind(self.task, self.data)
        exc = caught.exception
        self.assertEqual(str(exc), '\n'.join(check['errors']))
        self.assertEqual(getattr(exc, 'issues', None), check.get('issues'))
        handler = object.__new__(server.Handler)
        handler.path = '/api/projects/offline/tasks/seg-01/preview'
        with patch.object(handler, '_api', side_effect=exc), patch.object(handler, '_json') as output:
            handler._dispatch('POST')
        self.assertEqual(output.call_args.args[1], 400)
        self.assertEqual(output.call_args.args[0]['issues'], check['issues'])

    def test_group_all_script_error_families(self):
        task = copy.deepcopy(self.task)
        task.update(dialogue='The back, the slit, yeah.', action="On 'yeah' walk. On 'the slit' turn. On 'The back' step.")
        with patch.object(guard, 'conflicts', return_value=['black', 'dress']), patch('console.motion.compare_actions',
                return_value=(['新增动作甲', '新增动作乙'], [])):
            check = guard.check(self.data, task, compare_motion=True)
        for code, count in [('anchor_order', 2), ('motion_additions', 2), ('appearance_conflict', 2)]:
            issues = [i for i in check['issues'] if i['code'] == code]
            self.assertEqual(len(issues), 1)
            self.assertEqual(len(issues[0]['details']), count)


    def test_reference_order_save_reuses_existing_rebase_in_all_fields(self):
        from console import pipeline, store
        from tools.test_replacement_guard import fixture, task
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.temp))
        pid = store.create('离线参考图排序')['id']
        data = fixture()
        original = task(mode='image', image_ids=['product', 'camera'],
                        action='Smile at @image1 beside @image2.',
                        story='@image2 and @image1', direction='@image1 beside @image2')
        data['script']['tasks'] = [original]
        data['script']['replacements_fingerprint'] = guard.fingerprint(data)
        store.update(pid, lambda current: current.update(data))
        candidate = copy.deepcopy(data['script'])
        candidate['tasks'][0]['image_ids'] = ['camera', 'product']
        candidate['tasks'][0]['action'] = 'Smile warmly at @image1 beside @image2.'
        answer = {'tasks': [{'id': 'seg-01', 'action': 'Smile warmly at @image2 beside @image1.',
                            'story': '@image1 and @image2', 'direction': '@image2 beside @image1', 'changes': []}]}
        meta = {'provider': 'jimmy', 'model': 'mock-model', 'attempts': [{'success': True}]}
        with patch.object(llm, 'chat_json_checked', return_value=(answer, meta)):
            saved = pipeline.save_script(pid, candidate, reference_orders={'seg-01': ['product', 'camera']})['script']
        self.assertEqual(saved['tasks'][0]['image_ids'], ['camera', 'product'])
        self.assertEqual(saved['tasks'][0]['action'], 'Smile warmly at @image2 beside @image1.')
        self.assertEqual(saved['tasks'][0]['story'], '@image1 and @image2')
        self.assertEqual(saved['tasks'][0]['direction'], '@image2 beside @image1')
        self.assertEqual(saved['replacements_fingerprint'], data['script']['replacements_fingerprint'])
        self.assertEqual(store.load(pid)['script'], saved)

    def test_explicit_reference_order_remaps_edited_fields_and_rejects_non_permutations(self):
        from console import pipeline
        old = {'image_ids': ['product', 'person'], 'action': 'Saved text'}
        candidate = {'image_ids': ['person', 'product'], 'action': 'Smile warmly at @image2 beside @image1.',
                     'story': '@image2 then @image1', 'direction': '@image1 beside @image2'}
        pipeline.rebase_task_references(candidate, old, order_before=['product', 'person'])
        self.assertEqual(candidate['action'], 'Smile warmly at @image1 beside @image2.')
        self.assertEqual(candidate['story'], '@image1 then @image2')
        self.assertEqual(candidate['direction'], '@image2 beside @image1')
        for invalid in (['product'], ['person', 'person'], ['wrong', 'product'], 'product,person', [None, []]):
            with self.subTest(order=invalid), self.assertRaisesRegex(ValueError, '参考图排序'):
                pipeline.rebase_task_references(copy.deepcopy(candidate), old, order_before=invalid)

    def test_existing_motion_save_rejection_is_not_relaxed_for_order_fix(self):
        from console import pipeline, store
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.temp))
        pid = store.create('旧脚本仍须重新生成')['id']
        store.update(pid, lambda current: current.update(script=self.data['script']))
        before = store.load(pid)['script']
        candidate = copy.deepcopy(before)
        candidate['tasks'][0]['image_ids'].reverse()
        with self.assertRaisesRegex(ValueError, '动作描述里仍有身体动作'):
            pipeline.save_script(pid, candidate)
        self.assertEqual(store.load(pid)['script'], before)

    def test_cached_motion_issues_and_dependencies(self):
        data = copy.deepcopy(self.data)
        data['action_comparisons'] = {self.task['id']: {'fingerprint': 'same',
            'comparison': {'errors': ['缓存动作甲', '缓存动作乙'], 'warnings': []}}}
        with patch.object(jimmy_bridge, 'preflight_fingerprint', return_value='same'):
            check = jimmy_bridge.replacement_check(data, self.task, self.temp)
        issue = next(i for i in check['issues'] if i['code'] == 'motion_additions')
        self.assertEqual(issue['details'], ['缓存动作甲', '缓存动作乙'])
        self.assertEqual(issue['severity'], 'warning')
        self.assertIn('不用改，继续', issue['fix'])
        self.assertFalse(any(e in check['errors'] for e in issue['details']))

    def test_every_guard_error_has_structured_metadata(self):
        from tools.test_replacement_guard import fixture, task
        seen = set()
        cases = []
        def add(data, candidate):
            cases.append((copy.deepcopy(data), copy.deepcopy(candidate)))
        data = fixture()
        add(data, task(image_ids=[]))
        add(data, task(image_ids=['unknown']))
        add(data, task(kind='broll', mode='image', image_ids=['unknown']))
        add(data, task(image_ids=['other']))
        add(data, task(image_ids=['product', 'person']))
        add(data, task(image_ids=['camera']))
        add(data, task(action='A black dress.'))
        add(data, task(action='Smile at @image8.'))
        add(data, task(action='Smile.', reference_mapping_errors=[{'field':'action', 'text':'Smile.', 'message':'已移除参考图'}]))
        add(data, task(kind='motion', mode='image'))
        add(data, task(dialogue=''))
        data['assets'][1].update(camera_character='C1', camera_binding='old')
        add(data, task())
        data['replacements']['product']['action'] = 'keep'
        data['replacements']['characters'][0]['action'] = 'keep'
        add(data, task(image_ids=['person', 'product']))
        for data, candidate in cases:
            check = jimmy_bridge.replacement_check(data, candidate)
            details = [d for issue in check['issues'] for d in issue.get('details', [])]
            for error in check['errors']:
                self.assertTrue(error in details or ('原片外观冲突' in error and any(i['code']=='appearance_conflict' for i in check['issues'])), error)
            seen.update(i['code'] for i in check['issues'])
        expected = set(guard.ISSUE_INFO) - {'analysis_no_caption_bbox', 'performance_has_motion', 'anchor_order', 'motion_additions'}
        self.assertEqual(seen, expected)


if __name__ == '__main__':
    from tools.run_tests_offline import audit
    sys.addaudithook(audit)
    unittest.main()
