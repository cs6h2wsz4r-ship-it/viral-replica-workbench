"""SB3c regressions updated to SB3d: removed metadata never returns to the UI."""
import copy
import json
import unittest
from unittest.mock import patch
from tools import test_director_sb3 as fixtures
from console import director, gateway, store


class DirectorSB3cTests(unittest.TestCase):
    setUp=fixtures.DirectorSB3Tests.setUp
    fixture=fixtures.DirectorSB3Tests.fixture
    segment=fixtures.DirectorSB3Tests.segment
    plan=fixtures.DirectorSB3Tests.plan

    def generate(self,value):
        with patch.object(gateway,'reason_checked',return_value=(value,{})):
            return director.generate(self.pid)

    def test_system_minimum_segments_replaces_effect_cost_risk_contract(self):
        for removed in ('哪怕多花钱','tradeoff','risk','risk_reason','最少段数'):
            self.assertNotIn(removed,director.SYSTEM)
        schema=json.loads(director.SYSTEM.split('输出：',1)[1].split('\n',1)[0])
        self.assertNotIn('split_reason',schema);self.assertNotIn('skip_cells',schema)
        self.assertIn('prompt',schema['segments'][0]);self.assertNotIn('brief',schema['segments'][0])
        for key in ('cells','mode','images','refs'):self.assertNotIn(key,schema['segments'][0])

    def test_new_metadata_persisted_generation_save_view(self):
        data=self.fixture(4,2);value=self.plan(data,[['c01','c02'],['c03','c04']])
        before=copy.deepcopy(data['segments_plan']);value['split_reason']='模型试图改分段理由'
        saved=self.generate(value);saved=director.save(self.pid,{'action':'save','director':saved['director']})
        self.assertEqual(saved['segments_plan'],before)
        self.assertNotIn('split_reason',saved['director'])
        for plan in (saved['director'],director.view(saved,self.d)):
            self.assertTrue(all(not s['needs_rewrite'] for s in plan['segments']))

    def test_missing_or_non_string_split_reason_defaults_without_warning(self):
        data=self.fixture(2)
        for content in (None,{},12):
            value=self.plan(data);value['split_reason']=content
            saved=self.generate(value)
            self.assertNotIn('split_reason',saved['director'])
            self.assertEqual(saved['director'].get('warnings',[]),[])
            self.assertEqual(saved['segments_plan']['why_segments'],data['segments_plan']['why_segments'])

    def test_obsolete_risk_and_tradeoff_removed_from_saved_plan(self):
        data=self.fixture(2);value=self.plan(data);value['tradeoff']='old rationale'
        value['segments'][0].update(risk='high',risk_reason='old',risk_defaulted=True,start_state='old',end_state='old')
        saved=self.generate(value)['director']
        self.assertNotIn('tradeoff',saved)
        for key in ('risk','risk_reason','risk_defaulted','start_state','end_state'):
            self.assertNotIn(key,saved['segments'][0])
        self.assertEqual(saved['segments'][0]['warnings'],[])

    def test_missing_or_invalid_optional_explanations_do_not_block(self):
        data=self.fixture(2);value=self.plan(data)
        value['segments'][0].update(what=None,check={},sound=42)
        row=self.generate(value)['director']['segments'][0]
        self.assertEqual(row['what'],'');self.assertNotIn('sound',row)
        self.assertTrue(row['needs_rewrite']);self.assertEqual(row['rewrite_reason'],'生成后检查项必须包含至少 1 条非空字符串')

    def test_rewrite_uses_new_schema_and_preserves_other_segment_and_split_reason(self):
        data=self.fixture(4,2);before=self.generate(self.plan(data,[['c01','c02'],['c03','c04']]))['director']
        reply=copy.deepcopy(before['segments'][0]);reply.update(prompt=reply['prompt']+' New identities and black watch.',what='新动作')
        with patch.object(gateway,'reason_checked',return_value=(reply,{})) as call:
            after=director.save(self.pid,{'action':'rewrite','segment_id':'d1'})['director']
        text=json.dumps(call.call_args.args[1],ensure_ascii=False)
        self.assertIn('你不改这些，只写提示词',text)
        self.assertIn('prompt',text);self.assertNotIn('risk',text)
        self.assertNotIn('split_reason',after)
        self.assertEqual(after['segments'][1],before['segments'][1])
        self.assertIn(reply['prompt'],after['segments'][0]['prompt'])

    def test_rewrite_missing_content_does_not_reuse_previous_content(self):
        data=self.fixture(2);value=self.plan(data);before=self.generate(value)
        reply=copy.deepcopy(value['segments'][0]);reply.pop('prompt')
        with patch.object(gateway,'reason_checked',return_value=(reply,{})):
            after=director.save(self.pid,{'action':'rewrite','segment_id':'d1'})
        self.assertTrue(after['director']['segments'][0]['needs_rewrite'])
        self.assertIsNone(after['director']['segments'][0]['prompt'])

    def test_many_panels_one_segment_allowed_but_three_segments_retried(self):
        data=self.fixture(8,1.5)
        for groups in ([['c01','c02','c03','c04'],['c05','c06','c07','c08']],
                       [['c01','c02'],['c03','c04','c05','c06'],['c07','c08']]):
            value=self.plan(data,groups)
            with patch.object(gateway,'reason_checked',return_value=(value,{})) as call:saved=director.generate(self.pid)
            call.assert_called_once()
            self.assertEqual([s['cells'] for s in saved['director']['segments']],groups)
            self.assertFalse(any(s['needs_rewrite'] for s in saved['director']['segments']))
            self.assertNotIn('segmentation_warning',saved['director'])


if __name__=='__main__':unittest.main()
