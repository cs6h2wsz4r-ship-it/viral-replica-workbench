"""Single-instant redraw contracts; synthetic projects and stubbed models only."""
import copy
import json
import unittest
from unittest.mock import patch

from console import config, fix_plan, gateway, store, storyboard_edits as edits
from tools import test_director_sb3 as fixtures
from tools import test_v6
from tools.v2_fixture import configure


class V8Tests(unittest.TestCase):
    def setUp(self):
        fixtures.DirectorSB3Tests.setUp(self)
        self.enterContext(patch.object(config, 'load', return_value={**config.DEFAULTS, 'OPENAI_API_KEY': 'mock-only'}))
        data = fixtures.DirectorSB3Tests.raw_fixture(self, 4)
        self.data = configure(data, self.d, [['c01', 'c02'], ['c03', 'c04']])
        self.answer = dict(changes='只保留当前前景主体，删除多出的分身并补齐背景，人物外貌按新照片。', keep='机位和光线', why='画面只保留截帧这一刻的主体。')
        self.reason = self.enterContext(patch.object(gateway, 'reason_checked', return_value=(self.answer, {})))
        self.paid = self.enterContext(patch.object(gateway, 'image_confirm', side_effect=AssertionError('no paid calls')))

    def redraw(self, cid='c01', request='出现了多个人物，请重新画一下'):
        return edits.preview(self.pid, 'd1', dict(cell_id=cid, request=request))

    def test_frame_context_and_single_instant_rules(self):
        data = copy.deepcopy(self.data)
        messages = edits.plan_messages(data, 'd1', '', edits.materials_for(data, 'd1', self.d), self.d)
        rows = json.loads(messages[1]['content'][0]['text'].split('每格信息：')[1])
        self.assertEqual(rows[0]['frame_at'], .5)
        self.assertIn('供理解前后，不全部画进图里', rows[0]['动作说明'])
        self.assertIn('只画一个瞬间', messages[0]['content'])
        self.assertIn('只描述这一格的一个瞬间', fix_plan.FIX_SYSTEM)
        # A shot-wide action is not an instantaneous annotation.
        self.assertNotIn('截帧这一刻的动作', rows[0])

    def test_frame_nearby_action_uses_only_covering_breakdown_evidence(self):
        data=copy.deepcopy(self.data)
        data['breakdown']['shots']=[dict(start=.2,end=.7,action='当前动作原文'),dict(start=.7,end=1,action='之后动作原文')]
        context=edits.frame_context(data,data['storyboard']['cells'][0])
        text=json.dumps(context['截帧这一刻的动作'],ensure_ascii=False)
        self.assertIn('当前动作原文',text)
        self.assertNotIn('之后动作原文',text)
        self.assertIn('以截帧图为准',context['瞬间动作说明'])

    def test_legacy_pending_single_preview_requires_new_revision(self):
        rec=self.redraw()
        rec['storyboard_context'].pop('cell_revision_sig')
        store.update(self.pid,lambda d:edits.state_for(d,'d1').pop('cell_revisions'))
        with self.assertRaisesRegex(ValueError,'重新'):
            edits.verify_preview(self.pid,rec)

    def test_revision_precedes_preview_user_priority_and_no_old_plan(self):
        old = edits.state_for(self.data, 'd1')['plan']['cells'][0]['changes']
        image_paths = []
        with patch.object(edits.llm, 'image_part', side_effect=lambda path: image_paths.append(path) or []):
            rec = self.redraw()
        self.reason.assert_called_once()
        self.assertEqual(self.reason.call_args.args[0], 'storyboard_plan')
        messages = self.reason.call_args.args[1]
        text = json.dumps(messages, ensure_ascii=False)
        self.assertIn('用户的要求和原方案冲突时，以用户为准', messages[0]['content'])
        for value in (old, 'changes', 'keep', 'frame_at', '出现了多个人物', '第 03 步描述'):
            self.assertIn(value, text)
        self.assertIn('历史图号不代表本次推理输入的图号', text)
        self.assertIn('图2=C1', text)
        self.assertIn(self.d / self.data['storyboard']['cells'][0]['file'], image_paths)
        self.assertIn(self.d / edits.state_for(self.data, 'd1')['versions'][0]['cells']['c01'], image_paths)
        prompt = rec['preview']['prompt']
        self.assertIn(self.answer['changes'], prompt)
        self.assertNotIn(old, prompt)
        self.assertNotIn('Also keep', prompt)
        self.assertNotIn('出现了多个人物，请重新画一下', prompt)
        self.assertIn('one instant', prompt)
        self.assertIn('only once', prompt)
        self.assertEqual(rec['storyboard_context']['cell_revision']['why'], self.answer['why'])
        self.assertNotIn('免费', json.dumps(store.load(self.pid)['log'], ensure_ascii=False))
        self.paid.assert_not_called()

    def test_generation_references_exclude_source_and_number_continuously(self):
        rec = self.redraw()
        refs = rec['storyboard_context']['references']
        self.assertEqual([r['key'] for r in refs], ['current', 'C1', 'C2', 'product'])
        self.assertIn('Edit Image 1', rec['preview']['prompt'])
        for number in range(2, len(refs) + 1):
            self.assertIn(f'- Image {number} is ', rec['preview']['prompt'])
        self.assertNotIn(f'Image {len(refs)+1}', rec['preview']['prompt'])
        self.assertNotIn('original source panel', rec['preview']['prompt'])

    def test_isolated_revision_preserves_other_previews_plan_and_versions(self):
        before = store.load(self.pid)
        a = self.redraw()
        b = self.redraw('c02', '调整这一格')
        edits.verify_preview(self.pid, a)
        edits.verify_preview(self.pid, b)
        after = store.load(self.pid)
        for key in ('plan', 'versions'):
            self.assertEqual(edits.state_for(before, 'd1')[key], edits.state_for(after, 'd1')[key])
        self.assertEqual(edits.warnings(before, 'd1', self.d), edits.warnings(after, 'd1', self.d))
        self.assertEqual(edits.state_for(before, 'd2'), edits.state_for(after, 'd2'))
        revision = edits.state_for(after, 'd1')['cell_revisions']['c01']
        self.assertEqual(revision['original_plan'], edits.state_for(before, 'd1')['plan']['cells'][0])
        self.assertEqual(revision['revised_by_request'], '出现了多个人物，请重新画一下')
        self.assertEqual(revision['sig'], a['storyboard_context']['cell_revision_sig'])
        self.reason.return_value = ({**self.answer, 'changes': '另一种画法'}, {})
        self.redraw()
        with self.assertRaisesRegex(ValueError, '重新'):
            edits.verify_preview(self.pid, a)
        edits.verify_preview(self.pid, b)

    def test_failure_and_invalid_output_create_no_preview_or_mutation(self):
        for value in (TimeoutError('Authorization: secret'), ({'changes': ''}, {})):
            with self.subTest(value=type(value).__name__):
                self.reason.side_effect = value if isinstance(value, Exception) else None
                if not isinstance(value, Exception): self.reason.return_value = value
                before = store.load(self.pid)
                files = sorted(str(p) for p in self.d.rglob('*'))
                with patch.object(gateway, 'image_preview') as preview:
                    with self.assertRaises(ValueError) as failure: self.redraw()
                    self.assertNotIn('secret', str(failure.exception))
                    preview.assert_not_called()
                self.assertEqual(store.load(self.pid), before)
                self.assertEqual(sorted(str(p) for p in self.d.rglob('*')), files)

    def test_concurrent_input_change_rejects_revision_without_preview(self):
        def changed(*args, **kwargs):
            store.update(self.pid, lambda d: edits.state_for(d, 'd1')['plan'].update(sig='changed'))
            return self.answer, {}
        self.reason.side_effect = changed
        with patch.object(gateway, 'image_preview') as preview:
            with self.assertRaisesRegex(ValueError, '改过|改变'): self.redraw()
            preview.assert_not_called()
        self.assertNotIn('cell_revisions', edits.state_for(store.load(self.pid), 'd1'))

    def test_disabled_named_photo_description_change_rejects_revision(self):
        edits.save(self.pid,'d1',{'materials':{'C1':False,'C2':True,'product':True}})
        def changed(*args,**kwargs):
            store.update(self.pid,lambda d:d['replacements']['characters'][0].update(description='已修改的目标说明'))
            return self.answer,{}
        self.reason.side_effect=changed
        with patch.object(gateway,'image_preview') as preview:
            with self.assertRaisesRegex(ValueError,'改过|改变'):self.redraw(request='把 C1 改成照片里的样子')
            preview.assert_not_called()

    def test_single_stale_errors_do_not_offer_free_reasoning_retry(self):
        rec=self.redraw()
        store.update(self.pid,lambda d:edits.state_for(d,'d1')['versions'][0].update(resliced_at='changed'))
        with self.assertRaises(ValueError) as failure:edits.verify_preview(self.pid,rec)
        self.assertNotIn('免费',str(failure.exception))

    def test_generic_new_rules(self):
        from tools.test_v2c_generic_prompts import BANNED
        for text in (edits.CELL_REVISION_SYSTEM, edits.SINGLE_INSTANT_RULE, fix_plan.FIX_SYSTEM):
            self.assertIsNone(BANNED.search(text))


class V8FixTests(unittest.TestCase):
    setUp = test_v6.V6Tests.setUp
    report = test_v6.V6Tests.report
    frame = test_v6.V6Tests.frame
    run_record = test_v6.V6Tests.run_record
    fix = test_v6.V6Tests.fix
    output = test_v6.V6Tests.output
    plan = test_v6.V6Tests.plan

    def test_fix_request_is_final_changes_without_revision_reasoning(self):
        plan = self.plan()
        self.fix().apply(self.pid, 'd1', 'r1', {'plan_id': plan['id']})
        captured = []
        def preview(*args, **kwargs):
            captured.append(args[2])
            rec = dict(id='v8-fix-image', state='pending_confirmation')
            store.update(self.pid, lambda d: d['images'].__setitem__(rec['id'], rec))
            return rec
        with patch.object(gateway, 'reason_checked') as reason, patch.object(gateway, 'image_preview', side_effect=preview):
            rec = edits.preview(self.pid, 'd1', dict(cell_id='c01', request=plan['redraw'][0]['request'], fix_plan=dict(rev='r1', id=plan['id'])))
        reason.assert_not_called()
        self.assertIn(plan['redraw'][0]['request'], captured[0])
        self.assertNotIn('替换父子和手表', captured[0])
        self.assertNotIn('Also keep', captured[0])
        self.assertNotIn('original', [r['key'] for r in rec['storyboard_context']['references']])


if __name__ == '__main__':
    unittest.main()
