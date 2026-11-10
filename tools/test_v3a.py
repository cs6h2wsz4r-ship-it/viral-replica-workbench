"""V3a regression cases; synthetic projects, no remote requests."""
import copy
import json
import re
import unittest
from unittest.mock import patch

from console import director, gateway, jimmy_bridge, runtime_env, scene_prompt, segment_plan, store, storyboard_edits
from tools import test_v3_chain5 as chain5
from tools import director_sb3_bridge_cases as bridge_cases


class PromptValidationTests(unittest.TestCase):
    def test_only_real_case_sensitive_whole_cell_ids_are_rejected(self):
        data = {'storyboard': {'cells': [{'id': 'c03'}, {'id': 'shot-A'}]}}
        base = dict(images=['ref'], mode='video', check=['身份一致'])
        for token in ('PERSON C2', 'C1', 'C03', 'c99', 'xc03', 'c030', 'c03_suffix'):
            with self.subTest(token=token):
                self.assertEqual(director._prompt_problem({**base, 'prompt': '图1 视频1 ' + token}, data), '')
        for token in ('c03', '（c03）', 'c03动作', 'shot-A'):
            with self.subTest(token=token):
                self.assertIn('内部格号', director._prompt_problem({**base, 'prompt': '图1 视频1 ' + token}, data))

    def test_child_names_and_unknown_names_use_unambiguous_roles(self):
        for name in ('幼童', '儿童', '小孩', '宝宝', '婴儿', 'child', 'toddler', 'BABY'):
            with self.subTest(name=name):
                self.assertEqual(scene_prompt.role_names({'breakdown': {'characters': [dict(id='C2', name=name)]}})['C2'], 'THE CHILD')
        names = ['幼童', 'Jason', 'person', 'childhood', 'babyface', 'toddlerish']
        roles = scene_prompt.role_names({'breakdown': {'characters': [dict(id=f'C{i}', name=n) for i, n in enumerate(names)]}})
        self.assertEqual(list(roles.values()), ['THE CHILD', 'PERSON A', 'PERSON B', 'PERSON C', 'PERSON D', 'PERSON E'])

    def test_unknown_role_alphabet_continues_without_digits(self):
        roles = scene_prompt.role_names({'breakdown': {'characters': [dict(id=f'C{i}', name='') for i in range(28)]}})
        self.assertEqual(roles['C26'], 'PERSON AA')
        self.assertEqual(roles['C27'], 'PERSON AB')
        self.assertEqual(len(set(roles.values())), 28)
        self.assertFalse(any(re.search(r'\d', role) for role in roles.values()))

    def test_source_and_added_people_share_one_role_sequence(self):
        data = {'breakdown': {'characters': [dict(id='C1', name='Jason')]},
                'replacements': {'characters': [dict(source_id='N1', name='来访者', action='add')]}}
        text = storyboard_edits._references_text(data, [dict(key='C1'), dict(key='N1')])
        self.assertIn('Image 1 is PERSON A (C1)', text)
        self.assertIn('Image 2 is PERSON B (N1)', text)
        self.assertIn('different person from PERSON B', text)


class PersistedPromptTests(unittest.TestCase):
    setUp = chain5.Chain5AuthoringTests.setUp
    fixture = chain5.Chain5AuthoringTests.fixture
    fields = chain5.Chain5AuthoringTests.fields
    generated = chain5.Chain5AuthoringTests.generated

    def misjudged(self):
        self.generated()
        def change(data):
            row = data['director']['segments'][0]
            row.update(prompt=row['prompt'] + ' PERSON C2 与 C1 保持身份。', needs_rewrite=True,
                       rewrite_reason='提示词不能包含内部格号')
        return store.update(self.pid, change)

    def test_model_time_windows_have_one_decimal_place(self):
        data = self.fixture()
        data['storyboard']['cells'][0]['end'] = .6158055
        data['storyboard']['cells'][1]['start'] = .6158055
        with patch.object(director.llm, 'image_part', return_value=[]):
            messages = director.messages(data, self.d)
        windows = []
        for part in messages[1]['content']:
            text = part.get('text', '')
            if '每格成片时间窗：' in text:
                encoded = text.split('每格成片时间窗：')[1].split('\n')[0]
                self.assertTrue(all(re.fullmatch(r'\d+\.\d', token) for token in re.findall(r'"(?:start|end)": ([\d.]+)', encoded)))
                windows.extend(json.loads(encoded))
        self.assertEqual(windows[0]['end'], 1.6)
        self.assertEqual(windows[1]['start'], 1.6)
        self.assertEqual(windows[1]['end'], 4.0)

    def test_view_clears_false_flag_in_response_without_disk_writes(self):
        data = self.misjudged()
        before = (self.d / 'project.json').read_bytes()
        with patch.object(store, 'update', side_effect=AssertionError('view must not write')), \
                patch.object(gateway, 'reason_checked', side_effect=AssertionError('view must not infer')):
            view = director.view(data, self.d)
        for row in (view['segments'][0], data['director']['segments'][0]):
            self.assertFalse(row['needs_rewrite'])
            self.assertNotIn('rewrite_reason', row)
        self.assertFalse(view['stale'])
        self.assertEqual((self.d / 'project.json').read_bytes(), before)

    def test_save_and_manual_prompt_clear_old_false_flag(self):
        for action in ('save', 'prompt'):
            data = self.misjudged()
            body = dict(action=action, director=data['director'], segment_id='d1', prompt=data['director']['segments'][0]['prompt'])
            with self.subTest(action=action):
                row = director.save(self.pid, body)['director']['segments'][0]
                self.assertFalse(row['needs_rewrite'])
                self.assertNotIn('rewrite_reason', row)
                self.assertFalse(store.load(self.pid)['director']['segments'][0]['needs_rewrite'])

    def test_view_keeps_real_validation_and_upstream_failures(self):
        data = self.misjudged()
        data['director']['segments'][0]['prompt'] += ' c03'
        row = director.view(data, self.d)['segments'][0]
        self.assertTrue(row['needs_rewrite'])
        self.assertIn('内部格号', row['rewrite_reason'])
        data = self.misjudged()
        data['director']['segments'][0]['structure_needs_rewrite'] = True
        self.assertTrue(director.view(data, self.d)['segments'][0]['needs_rewrite'])
        data = self.misjudged()
        data['director']['segments'][0]['inputs_sig'] = 'outdated'
        self.assertTrue(director.view(data, self.d)['segments'][0]['stale'])

    def test_real_v3_role_signatures_remain_current_without_masking_changes(self):
        data = self.fixture()
        data['breakdown']['characters'] = [dict(id='C1', name='Jason'), dict(id='C2', name='幼童')]
        # Simulate persisted V3 signatures computed before the role mapping fix.
        with patch.object(scene_prompt, 'role_names', return_value={'C1': 'PERSON C1', 'C2': 'PERSON C2', 'N1': 'PERSON N1'}):
            data['segments_plan']['inputs_sig'] = segment_plan.inputs_sig(data, self.d)
            for sid,state in data['segment_boards']['segments'].items():
                state['settings_sig']=storyboard_edits.settings_sig(data,sid,self.d,storyboard_edits.materials_for(data,sid,self.d))
            data['director'] = director.validate_plan(self.fields(data), data, self.d)
        row = data['director']['segments'][0]
        row.update(prompt=row['prompt'] + ' PERSON C2 与 C1。', needs_rewrite=True, rewrite_reason='提示词不能包含内部格号')
        store.update(self.pid, lambda current: current.update(data))
        before = (self.d / 'project.json').read_bytes()
        self.assertFalse(segment_plan.status(data, self.d)['stale'])
        view = director.view(data, self.d)
        self.assertTrue(view['ready'])
        self.assertFalse(view['stale'])
        self.assertFalse(view['segments'][0]['needs_rewrite'])
        self.assertEqual((self.d / 'project.json').read_bytes(), before)
        saved = director.save(self.pid, dict(action='save', director=data['director']))
        self.assertFalse(director.view(saved, self.d)['stale'])
        for options in (segment_plan.material_options(saved, self.d), director.material_options(saved, self.d)):
            roles = {r['key']: r['role'] for r in options}
            self.assertEqual(roles['C1'], 'PERSON A')
            self.assertEqual(roles['C2'], 'THE CHILD')
        for field in ('name', 'description'):
            changed = copy.deepcopy(saved)
            if field == 'name':changed['breakdown']['characters'][0]['name'] = '新人物'
            else:changed['replacements']['characters'][0]['description'] = '新的外观说明'
            with self.subTest(field=field):
                self.assertEqual(segment_plan.status(changed, self.d)['stale'],field=='name')
                self.assertTrue(director.view(changed, self.d)['stale'])
        (self.d / 'assets/son.png').write_bytes(b'changed photo')
        self.assertFalse(segment_plan.status(saved, self.d)['stale'])
        self.assertTrue(director.view(saved, self.d)['stale'])


class PreviewRecoveryTests(unittest.TestCase):
    setUp = bridge_cases.DirectorBridgeTests.setUp
    sync_plan = bridge_cases.DirectorBridgeTests.sync_plan
    package = bridge_cases.DirectorBridgeTests.package

    def test_legacy_role_upgrade_allows_get_then_preview_without_rewriting(self):
        with patch.object(runtime_env, 'apply'):
            import server
        data = store.load(self.pid)
        data['breakdown']['characters'][0]['name'] = '幼童'
        row = data['director']['segments'][0]
        row.update(prompt=row['prompt'] + ' PERSON C1。', needs_rewrite=True, rewrite_reason='提示词不能包含内部格号')
        with patch.object(scene_prompt, 'role_names', return_value={'C1': 'PERSON C1'}):
            data['segments_plan']['inputs_sig'] = segment_plan.inputs_sig(data, self.d)
            row['inputs_sig'] = director.segment_inputs_sig(data, self.d, row['id'])
        store.update(self.pid, lambda current: current.update(data))
        before = (self.d / 'project.json').read_bytes()
        with patch.object(store, 'update', side_effect=AssertionError('GET cannot write')), \
                patch.object(server.scene_plan, 'enabled', return_value=False), \
                patch.object(gateway, 'reason_checked', side_effect=AssertionError('GET cannot infer')):
            response = server.project_view(store.load(self.pid))
        self.assertFalse(response['segments_plan_status']['stale'])
        self.assertTrue(response['director_status']['ready'])
        self.assertFalse(response['director_status']['stale'])
        self.assertFalse(response['director']['segments'][0]['needs_rewrite'])
        self.assertEqual((self.d / 'project.json').read_bytes(), before)
        run = jimmy_bridge.preview(self.pid, 'd01')
        self.assertEqual(run['state'], 'pending_confirmation')
        self.assertEqual(run['preview']['tasks'][0]['prompt'], row['prompt'])

    def test_preview_and_confirmation_recheck_old_false_flag(self):
        def mark(data):
            row = data['director']['segments'][0]
            row.update(prompt=row['prompt'] + ' PERSON C2 和 C1。', needs_rewrite=True, rewrite_reason='提示词不能包含内部格号')
        data = store.update(self.pid, mark)
        with patch.object(jimmy_bridge, '_start_generate') as submit:
            run = jimmy_bridge.preview(self.pid, 'd01')
            self.assertEqual(run['state'], 'pending_confirmation')
            jimmy_bridge.verify_preview_current(data, data['director']['segments'][0], run, self.d)
            submit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
