"""SB3h length budgets and panel labels, with synthetic media and mocked reasoning."""
import copy
import itertools
import json
import unittest
from unittest.mock import patch

from console import director, gateway, store
from tools import test_director_sb3 as fixtures
from tools.v2_fixture import choose


def budget_sentence(fixed, budget):
    return (f'你写的 replace_keep、timeline、continuity、sound、forbid 合计不超过 {budget} 字符'
            f'（程序还会加约 {fixed} 字符的固定说明，全文上限 5000）。')


def field_length(row):
    return (sum(len(row[key]) for key in ('replace_keep', 'continuity', 'sound'))
            + sum(len(item['text']) for item in row['timeline'])
            + sum(map(len, row['forbid'])))


def empty_fields(row):
    empty = copy.deepcopy(row)
    empty.update(replace_keep='', continuity='', sound='', forbid=[])
    empty['timeline'] = [dict(cells=[cid], text='') for cid in row['cells']]
    return empty


class DirectorSB3hTests(unittest.TestCase):
    setUp = fixtures.DirectorSB3Tests.setUp
    fixture = fixtures.DirectorSB3Tests.fixture
    segment = fixtures.DirectorSB3Tests.segment
    plan = fixtures.DirectorSB3Tests.plan

    def budget(self, data, row):
        self.assertFalse(hasattr(director,'brief_budget'))
        return 0,5000

    def long_fixture(self):
        data=self.fixture(6,13/6);value=self.plan(data)
        value['segments'][0]['prompt']+='长'*(5100-len(value['segments'][0]['prompt']))
        self.assertEqual(len(value['segments'][0]['prompt']),5100)
        return data,value

    def test_fixed_cost_matches_empty_assembly_and_exact_field_accounting(self):
        data=self.fixture(6,13/6)
        for mode,product in itertools.product(('image','video'),(False,True)):
            with self.subTest(mode=mode,product=product):
                choose(data,self.d,'d1',mode=mode,refs=['C1','C2','product' if product else 'N1'])
                value=self.plan(data);base=value['segments'][0]['prompt']
                for length in (4500,5000,5001):
                    value['segments'][0]['prompt']=base+'字'*(length-len(base));before=copy.deepcopy((data,value))
                    row=director.validate_plan(value,data,self.d)['segments'][0]
                    self.assertEqual(len(row['prompt']),length)
                    self.assertEqual(row['needs_rewrite'],length>5000)
                    self.assertEqual((data,value),before)

    def test_mode_product_duration_panel_count_and_reference_roles_affect_cost(self):
        for count,seconds in ((1,.7),(3,1.7),(8,13/8),(12,1.25)):
            sample=self.fixture(count,seconds)
            for mode in ('image','video'):
                choose(sample,self.d,'d1',mode=mode)
                value=self.plan(sample);prefix=value['segments'][0]['prompt']
                value['segments'][0]['prompt']=prefix+'字'*(5000-len(prefix))
                checked=director.validate_plan(value,sample,self.d)['segments'][0]
                self.assertFalse(checked['needs_rewrite']);self.assertEqual(len(checked['prompt']),5000)
                self.assertEqual(checked['images'],sample['segments_plan']['segments'][0]['gen_images'])
                self.assertEqual(checked['mode'],mode)
                self.assertEqual(checked['cells'],sample['segments_plan']['segments'][0]['cells'])

    def test_full_generation_request_has_conservative_budget_before_segmentation(self):
        data=self.fixture(8,13/8);choose(data,self.d,'d2',mode='image',refs=['C2']);value=self.plan(data)
        with patch.object(gateway,'reason_checked',return_value=(value,{})) as model:director.generate(self.pid)
        model.assert_called_once();request=json.dumps(model.call_args.args[1],ensure_ascii=False)
        self.assertEqual(request.count('每条完整提示词不超过 4500 字，硬上限 5000 字符'),len(value['segments']))
        self.assertIn('本段',request);self.assertNotIn('固定说明',request)

    def test_rewrite_request_uses_actual_segment_and_reports_previous_model_length(self):
        data,value=self.long_fixture()
        for mode in ('image','video'):
            choose(data,self.d,'d1',mode=mode);value=self.plan(data)
            value['segments'][0]['prompt']+='长'*(5100-len(value['segments'][0]['prompt']))
            plan=director.validate_plan(value,data,self.d);store.update(self.pid,lambda current:current.update(director=plan))
            target=plan['segments'][0]
            with patch.object(gateway,'reason_checked',return_value=(value['segments'][0],{})) as model:
                result=director.save(self.pid,{'action':'rewrite','segment_id':target['id']})
            request=json.dumps(model.call_args.args[1],ensure_ascii=False)
            self.assertIn('硬上限 5000 字符',request);self.assertIn('5100 字符，超过 5000 上限',request)
            self.assertTrue(result['director']['segments'][0]['needs_rewrite'])
            self.assertEqual(result['director']['segments'][0]['prompt'],value['segments'][0]['prompt'])

    def test_rewrite_budget_uses_only_selected_cells_and_materials(self):
        data=self.fixture(8,13/8);self.plan(data,[['c01','c02','c03','c04'],['c05','c06','c07','c08']])
        choose(data,self.d,'d2',mode='image',refs=['C2']);value=self.plan(data)
        data['director']=director.validate_plan(value,data,self.d);target=data['director']['segments'][1]
        parts=director.messages(data,self.d,target=target)[1]['content']
        numbered=[p['text'] for p in parts if p['type']=='text' and p['text'].startswith(('图','视频'))]
        self.assertEqual(numbered,['图1：第 5 格的新画面（替换后的状态）','图2：THE SON 的人物照','图3：THE FATHER 的人物照'])
        request=json.dumps(parts,ensure_ascii=False);self.assertIn('硬上限 5000 字符',request)
        self.assertNotIn('上一版需要修改',request)

    def test_timeline_leading_panel_prefixes_removed_case_insensitively(self):
        data=self.fixture(8,13/8);value=self.plan(data);base=value['segments'][0]['prompt']
        body='The son holds the watch.'
        for prefix in ('Panel 4, ','Panel 4: ','Panel 4. ','panel 4 - ','  PANEL 4:  '):
            with self.subTest(prefix=prefix):
                value['segments'][0]['prompt']=base+'\n'+prefix+body
                before=copy.deepcopy(value);row=director.validate_plan(value,data,self.d)['segments'][0]
                self.assertEqual(row['prompt'],value['segments'][0]['prompt'])
                self.assertTrue(row['prompt'].endswith(prefix+body));self.assertEqual(value,before)
                self.assertFalse(row['needs_rewrite'])

    def test_rewrite_missing_current_cell_keeps_actionable_error_before_model(self):
        data = self.fixture(6, 2)
        data['director'] = director.validate_plan(self.plan(data, [['c01', 'c02', 'c03'], ['c04', 'c05', 'c06']]), data, self.d)
        for missing in ('c04', 'c05', 'c06'):
            with self.subTest(missing=missing):
                stale = copy.deepcopy(data)
                stale['storyboard']['cells'] = [cell for cell in data['storyboard']['cells'] if cell['id'] != missing]
                store.update(self.pid, lambda current: current.update(stale))
                with patch.object(gateway, 'reason_checked') as model:
                    try:
                        director.save(self.pid, {'action': 'rewrite', 'segment_id': 'd2'})
                    except Exception as error:
                        self.assertIsInstance(error, ValueError)
                        self.assertEqual(str(error),'先在第 04 步（对照和修改）生成或更新分段方案')
                    else:
                        self.fail('A stale segment cannot be budgeted against missing cells')
                model.assert_not_called()
                self.assertEqual(store.load(self.pid)['director'], data['director'])

    def test_panel_mentions_in_body_and_non_label_openings_are_preserved(self):
        data=self.fixture(1);value=self.plan(data);base=value['segments'][0]['prompt']
        for body in ('The son points to panel 4, then lowers his wrist.','Panel 4 is visible in the background.',
                     'The panel opens.','Panel 4.5 is a measurement.','Panel 12: Keep the motion from panel 4, then stop.'):
            value['segments'][0]['prompt']=base+' '+body
            checked=director.validate_plan(value,data,self.d)['segments'][0]
            self.assertTrue(checked['prompt'].endswith(body));self.assertFalse(checked['needs_rewrite'])
        self.assertIn('连贯的中文段落',director.SYSTEM)

    def test_still_long_auto_rewrite_keeps_existing_flag_and_no_extra_warning(self):
        data, value = self.long_fixture()
        with patch.object(gateway, 'reason_checked', side_effect=[(value, {}), (value['segments'][0], {})]) as model:
            result = director.generate(self.pid)
        self.assertEqual(model.call_count, 2)
        row = result['director']['segments'][0]
        self.assertTrue(row['needs_rewrite'])
        self.assertEqual(row['rewrite_reason'], f'这一段的提示词有 {len(row["prompt"])} 字符，超过 5000 上限')
        self.assertEqual(row['warnings'], [])
        self.assertEqual(director.view(result,self.d)['warnings'], [])
        self.assertEqual(director.REWRITE_WARNING, '这一段的说明没写好，点“重新写这一段”再试一次。')


if __name__ == '__main__':
    unittest.main()
