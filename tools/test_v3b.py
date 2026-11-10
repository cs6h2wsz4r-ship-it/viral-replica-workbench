"""V3b formatting tolerance without weakening generation prerequisites."""
import copy
import unittest
from unittest.mock import patch

from console import director, gateway, jimmy_bridge, segment_boards, segment_plan, store
from tools import test_v3_chain5 as chain5
from tools import test_director_sb3 as fixtures
from tools import director_sb3_bridge_cases as bridge_fixtures


class CheckNormalizationTests(unittest.TestCase):
    def segment(self, checks):
        return dict(mode='video', images=['c01'], prompt='图1 提供新画面，视频1 提供动作。', check=checks)

    def test_one_through_six_checks_are_valid(self):
        for count in range(1, 7):
            with self.subTest(count=count):
                row = self.segment([f'检查 {i}' for i in range(count)])
                self.assertEqual(director._prompt_problem(row), '')
                director.refresh_prompt_status(row, {})
                self.assertFalse(row['needs_rewrite'])
                self.assertEqual(len(row['check']), count)

    def test_eight_checks_keep_first_six(self):
        row = self.segment([f'检查 {i}' for i in range(8)])
        director.refresh_prompt_status(row, {})
        self.assertEqual(row['check'], [f'检查 {i}' for i in range(6)])
        self.assertFalse(row['needs_rewrite'])

    def test_blank_checks_are_removed_before_limit(self):
        row = self.segment(['', '  ', '\n', ' 检查手势 ', '', '\t检查商品\n', '检查身份'])
        director.refresh_prompt_status(row, {})
        self.assertEqual(row['check'], ['检查手势', '检查商品', '检查身份'])
        self.assertFalse(row['needs_rewrite'])

    def test_single_check_string_is_wrapped_without_changing_content(self):
        row = self.segment(' 检查商品的正面朝向镜头。\n')
        director.refresh_prompt_status(row, {})
        self.assertEqual(row['check'], ['检查商品的正面朝向镜头。'])
        self.assertFalse(row['needs_rewrite'])

    def test_empty_or_non_string_checks_still_need_rewrite(self):
        for checks in (None, ' ', [], ['', ' \n\t'], ['检查商品', 2], ['有效'] * 6 + [2]):
            with self.subTest(checks=checks):
                row = self.segment(checks)
                director.refresh_prompt_status(row, {})
                self.assertTrue(row['needs_rewrite'])
                self.assertIn('检查项', row['rewrite_reason'])

    def test_system_guides_about_three_and_at_most_six(self):
        self.assertIn('3 条左右，最多 6 条', director.SYSTEM)

    def test_real_prompt_errors_remain_hard_failures(self):
        data = {'storyboard': {'cells': [{'id': 'c01'}]}}
        for prompt, reason in ((None, '完整提示词'), (' ', '完整提示词'), ('字' * 5001, '5000'),
                               ('图1', '编号'), ('图1 图2 视频1', '编号'),
                               ('图1 视频1 视频2', '编号'), ('图1 视频1 c01', '内部格号')):
            with self.subTest(prompt=str(prompt)[:40]):
                row = {**self.segment(['检查'] * 6), 'prompt': prompt}
                director.refresh_prompt_status(row, data)
                self.assertTrue(row['needs_rewrite'])
                self.assertIn(reason, row['rewrite_reason'])


class FormattingIntegrationTests(unittest.TestCase):
    fixture = chain5.Chain5AuthoringTests.fixture
    fields = chain5.Chain5AuthoringTests.fields
    generated = chain5.Chain5AuthoringTests.generated

    def setUp(self):
        chain5.Chain5AuthoringTests.setUp(self)
        # Prompt validation does not need FFprobe/image transport preparation.
        self.enterContext(patch.object(director.llm, 'image_part', return_value=[]))

    def test_valid_checks_do_not_trigger_automatic_model_rewrite(self):
        data = self.fixture()
        value = self.fields(data)
        value['segments'][0]['check'] = ['检查'] * 8
        value['segments'][1]['check'] = [' ', ' 检查身份 ', '']
        with patch.object(gateway, 'reason_checked', return_value=(value, {})) as reason:
            saved = director.generate(self.pid)
        reason.assert_called_once()
        self.assertEqual(saved['director']['segments'][0]['check'], ['检查'] * 6)
        self.assertEqual(saved['director']['segments'][1]['check'], ['检查身份'])
        self.assertFalse(any(row['needs_rewrite'] for row in saved['director']['segments']))

    def test_single_check_preserves_display_cell_conversion(self):
        data = self.fixture()
        value = self.fields(data)
        value['segments'][0]['check'] = ' 确认 c01 商品外观正确。 '
        checked = director.validate_plan(value, data, self.d)
        self.assertEqual(checked['segments'][0]['check'], ['确认 第 1 格 商品外观正确。'])

    def misjudged(self):
        self.generated()
        def mark(data):
            row = data['director']['segments'][0]
            row.update(check=[' ', *[f' 检查 {i} ' for i in range(8)], ''],
                       needs_rewrite=True, rewrite_reason='生成后检查项必须是 1–3 条字符串')
        return store.update(self.pid, mark)

    def test_view_clears_old_false_flag_without_disk_writes_or_inference(self):
        data = self.misjudged()
        before = (self.d / 'project.json').read_bytes()
        with patch.object(store, 'update', side_effect=AssertionError('view must not write')), \
                patch.object(gateway, 'reason_checked', side_effect=AssertionError('view must not infer')):
            view = director.view(data, self.d)
        for row in (view['segments'][0], data['director']['segments'][0]):
            self.assertEqual(row['check'], [f'检查 {i}' for i in range(6)])
            self.assertFalse(row['needs_rewrite'])
            self.assertNotIn('rewrite_reason', row)
        self.assertFalse(view['stale'])
        self.assertEqual((self.d / 'project.json').read_bytes(), before)

    def test_save_and_prompt_edit_clear_old_false_flag(self):
        for action in ('save', 'prompt'):
            data = self.misjudged()
            body = dict(action=action, director=data['director'], segment_id='d1',
                        prompt=data['director']['segments'][0]['prompt'])
            with self.subTest(action=action), \
                    patch.object(gateway, 'reason_checked', side_effect=AssertionError('save must not infer')):
                saved = director.save(self.pid, body)
                row = saved['director']['segments'][0]
                self.assertFalse(row['needs_rewrite'])
                self.assertNotIn('rewrite_reason', row)
                self.assertEqual(row['check'], [f'检查 {i}' for i in range(6)])
                self.assertEqual(store.load(self.pid)['director']['segments'][0], row)

    def test_old_real_errors_and_structure_flags_are_not_cleared(self):
        base = self.misjudged()
        for changes in (dict(prompt='图1 视频1'), dict(prompt='字' * 5001),
                        dict(prompt=base['director']['segments'][0]['prompt'] + ' c01'),
                        dict(check=[' ', '']), dict(structure_needs_rewrite=True)):
            data = copy.deepcopy(base)
            data['director']['segments'][0].update(changes)
            with self.subTest(changes=str(changes)[:50]):
                self.assertTrue(director.view(data, self.d)['segments'][0]['needs_rewrite'])
        data = copy.deepcopy(base)
        data['director']['segments'][0]['inputs_sig'] = 'outdated'
        self.assertTrue(director.view(data, self.d)['segments'][0]['stale'])

    def test_step04_normalizes_mode_whitespace_and_case(self):
        data = self.fixture()
        for mode, expected in ((' VIDEO ', 'video'), ('\tImage\n', 'image')):
            candidate = copy.deepcopy(data['segments_plan'])
            candidate['segments'][0]['mode'] = mode
            with self.subTest(mode=mode):
                checked = segment_plan.validate_plan(candidate, data, self.d)
                self.assertEqual(checked['segments'][0]['mode'], expected)
                self.assertEqual(candidate['segments'][0]['mode'], mode)
        saved = segment_plan.save(self.pid, 'd1', {'mode': ' VIDEO '})
        self.assertEqual(saved['segments_plan']['segments'][0]['mode'], 'video')
        self.assertEqual(segment_plan.segment_sig(saved['segments_plan']['segments'][0]),
                         segment_plan.segment_sig(data['segments_plan']['segments'][0]))

    def test_step04_deduplicates_refs_preserving_first_occurrence(self):
        data = self.fixture()
        candidate = copy.deepcopy(data['segments_plan'])
        candidate['segments'][0]['refs'] = ['C2', 'C1', 'C2', 'product', 'C1']
        checked = segment_plan.validate_plan(candidate, data, self.d)
        self.assertEqual(checked['segments'][0]['refs'], ['C2', 'C1', 'product'])
        self.assertEqual(candidate['segments'][0]['refs'], ['C2', 'C1', 'C2', 'product', 'C1'])
        saved = segment_plan.save(self.pid, 'd1', {'refs': ['C1', 'C2', 'C1', 'product', 'C2']})
        self.assertEqual(saved['segments_plan']['segments'][0]['refs'], ['C1', 'C2', 'product'])

    def test_step04_deduplicates_gen_images_before_channel_limit(self):
        data = self.fixture()
        candidate = copy.deepcopy(data['segments_plan'])
        images = ['product', 'c01', 'product', 'C2', 'c01', 'C2']
        candidate['segments'][0]['gen_images'] = images
        self.assertEqual(segment_plan.validate_gen_images(candidate['segments'][0], data), ['product', 'c01', 'C2'])
        checked = segment_plan.validate_plan(candidate, data, self.d)
        self.assertEqual(checked['segments'][0]['gen_images'], ['product', 'c01', 'C2'])
        self.assertEqual(candidate['segments'][0]['gen_images'], images)

    def test_step04_trims_material_keys_before_deduplication(self):
        data = self.fixture()
        candidate = copy.deepcopy(data['segments_plan'])
        row = candidate['segments'][0]
        row.update(refs=[' C1 ', 'C1', '\tC2', 'product\n'],
                   gen_images=[' c01 ', 'C2\t', ' c01', '\nproduct '])
        checked = segment_plan.validate_plan(candidate, data, self.d)['segments'][0]
        self.assertEqual(checked['refs'], ['C1', 'C2', 'product'])
        self.assertEqual(checked['gen_images'], ['c01', 'C2', 'product'])
        self.assertEqual(segment_plan.validate_gen_images({**checked, 'gen_images': [' c01 ', 'C2\t']} , data), ['c01', 'C2'])

    def test_step04_invalid_materials_and_missing_fields_still_reject(self):
        data = self.fixture()
        for change in (dict(mode=''), dict(mode='other'), dict(mode=None), dict(refs=None),
                       dict(refs=['C1', 2]), dict(refs=['unknown', 'unknown']), dict(refs=[' ']), dict(gen_images=[]),
                       dict(gen_images=['c01', 'c02', 'C1', 'C2', 'product', 'c01']),
                       dict(gen_images=['c03', 'c03']), dict(gen_images=['unknown', 'unknown']),
                       dict(gen_images=['c01', 2]), dict(gen_images=[' ']), dict(gen_images=['C1', 'product'])):
            candidate = copy.deepcopy(data['segments_plan'])
            candidate['segments'][0].update(change)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, '分段方案不完整'):
                segment_plan.validate_plan(candidate, data, self.d)

    def test_step04_six_cell_and_duration_limits_remain(self):
        data = fixtures.DirectorSB3Tests.raw_fixture(self, 7, 1)
        row = dict(id='d1', cells=[c['id'] for c in data['storyboard']['cells']],
                   mode='video', refs=[], gen_images=['c01'])
        with self.assertRaisesRegex(ValueError, '6 格'):
            segment_plan.validate_plan({'segments': [row]}, data, self.d)
        with self.assertRaisesRegex(ValueError, '1–6 格'):
            segment_boards.layout(7)
        data['storyboard']['cells'] = data['storyboard']['cells'][:6]
        row['cells'] = row['cells'][:6]
        self.assertEqual(len(segment_plan.validate_plan({'segments': [row]}, data, self.d)['segments'][0]['cells']), 6)
        data['storyboard']['cells'][-1]['end'] = 15.1
        with self.assertRaisesRegex(ValueError, '15 秒'):
            segment_plan.validate_plan({'segments': [row]}, data, self.d)

    def test_director_reply_order_is_aligned_by_id(self):
        data = self.fixture()
        fields = self.fields(data)
        for row in fields['segments']:
            row['what'] = '内容属于 ' + row['id']
        fields['segments'].reverse()
        checked = director.validate_plan(fields, data, self.d)
        self.assertEqual([row['id'] for row in checked['segments']], ['d1', 'd2'])
        self.assertEqual([row['what'] for row in checked['segments']], ['内容属于 d1', '内容属于 d2'])
        self.assertEqual([row['images'] for row in checked['segments']],
                         [segment_plan.generation_images(data,row,self.d) for row in data['segments_plan']['segments']])
        self.assertEqual([row['id'] for row in fields['segments']], ['d2', 'd1'])

    def test_director_save_order_is_aligned_by_id(self):
        data = self.generated()
        supplied = copy.deepcopy(data['director'])
        supplied['segments'].reverse()
        supplied['segments'][0]['prompt'] += ' 保持第二段动作。'
        saved = director.save(self.pid, {'action': 'save', 'director': supplied})
        self.assertEqual([row['id'] for row in saved['director']['segments']], ['d1', 'd2'])
        self.assertEqual(saved['director']['segments'][0]['prompt'], data['director']['segments'][0]['prompt'])
        self.assertEqual(saved['director']['segments'][1]['prompt'], supplied['segments'][0]['prompt'])

    def test_director_wrong_missing_duplicate_or_invalid_ids_still_reject(self):
        data = self.generated()
        for ids in (['d1'], ['d1', 'd1'], ['d1', 'unknown'], ['d1', None], ['d1', []]):
            for save in (False, True):
                candidate = copy.deepcopy(data['director'] if save else self.fields(data))
                candidate['segments'] = candidate['segments'][:len(ids)]
                for row, sid in zip(candidate['segments'], ids):
                    row['id'] = sid
                with self.subTest(ids=ids, save=save), self.assertRaisesRegex(ValueError, '导演方案不完整'):
                    if save:
                        director.save(self.pid, {'action': 'save', 'director': candidate})
                    else:
                        director.validate_plan(candidate, data, self.d)


class PreviewRecoveryTests(unittest.TestCase):
    setUp = bridge_fixtures.DirectorBridgeTests.setUp
    sync_plan = bridge_fixtures.DirectorBridgeTests.sync_plan
    package = bridge_fixtures.DirectorBridgeTests.package

    def test_preview_and_confirmation_refresh_old_check_flag_without_rewriting(self):
        for count in (4, 6, 8):
            checks = [' ', *[f' 检查 {i} ' for i in range(count)], '']
            data = store.update(self.pid, lambda current: current['director']['segments'][0].update(
                check=checks, needs_rewrite=True, rewrite_reason='生成后检查项必须是 1–3 条字符串'))
            with self.subTest(count=count), \
                    patch.object(gateway, 'reason_checked', side_effect=AssertionError('preview must not infer')), \
                    patch.object(jimmy_bridge, '_start_generate') as submit:
                run = jimmy_bridge.preview(self.pid, 'd01')
                self.assertEqual(run['state'], 'pending_confirmation')
                frozen = run['review_inputs']['segment']
                self.assertEqual(frozen['check'], [f'检查 {i}' for i in range(min(count, 6))])
                self.assertEqual(run['preview']['tasks'][0]['prompt'], data['director']['segments'][0]['prompt'])
                row = data['director']['segments'][0]
                jimmy_bridge.verify_preview_current(data, row, run, self.d)
                self.assertFalse(row['needs_rewrite'])
                self.assertNotIn('rewrite_reason', row)
                submit.assert_not_called()

    def test_preview_still_rejects_empty_checks_before_preparing_package(self):
        store.update(self.pid, lambda current: current['director']['segments'][0].update(
            check=['', ' \n'], needs_rewrite=True))
        with self.assertRaisesRegex(jimmy_bridge.BridgeError, '检查项'):
            jimmy_bridge.preview(self.pid, 'd01')
        self.cli.assert_not_called()
        self.cut.assert_not_called()


if __name__ == '__main__':
    unittest.main()
