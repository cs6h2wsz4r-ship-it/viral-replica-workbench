"""SB3e text normalization; synthetic projects and mocked reasoning only."""
import copy
import unittest
from unittest.mock import patch

from console import director, gateway, store
from tools import test_director_sb3 as fixtures
from tools.v2_fixture import configure, choose


SOUND = 'Natural outdoor and indoor ambience only, no music and no narration; in c07 THE FATHER says exactly, “Thank you, son.”'
FORBID = ['No original father or son faces, and no blending or swapping THE FATHER and THE SON.',
          'No white SENVA bracelet, red circular SOS button, SENVA logo, box or old packaging.']


class DirectorSB3eTests(unittest.TestCase):
    setUp = fixtures.DirectorSB3Tests.setUp
    fixture = fixtures.DirectorSB3Tests.fixture
    segment = fixtures.DirectorSB3Tests.segment
    plan = fixtures.DirectorSB3Tests.plan
    generated = fixtures.DirectorSB3Tests.generated

    def test_real_sound_and_forbid_example(self):
        data=self.fixture(8);plan=self.plan(data)
        extra=SOUND.replace('c07','第 7 格')+' 禁止：'+' '.join(FORBID)
        plan['segments'][0]['prompt']+=' '+extra
        before=copy.deepcopy(plan);row=director.validate_plan(plan,data,self.d)['segments'][0]
        self.assertEqual(row['prompt'],plan['segments'][0]['prompt']);self.assertIn(extra,row['prompt'])
        self.assertNotIn('sound',row);self.assertNotIn('forbid',row)
        self.assertEqual(plan,before);self.assertFalse(row['needs_rewrite'])

    def test_all_english_fields_use_local_panels_and_global_shots(self):
        data=self.fixture(8);plan=self.plan(data,[['c01','c02','c03','c04'],['c05','c06','c07','c08']])
        for internal in ('c07','c02','c05至c08','c05–c08','c01–c04','c04至c08'):
            with self.subTest(internal=internal):
                candidate=copy.deepcopy(plan);candidate['segments'][1]['prompt']+=' '+internal
                rows=director.validate_plan(candidate,data,self.d)['segments']
                self.assertTrue(rows[1]['needs_rewrite']);self.assertIn('内部格号',rows[1]['rewrite_reason'])
                self.assertFalse(rows[0]['needs_rewrite'])
                self.assertEqual(rows[1]['prompt'],candidate['segments'][1]['prompt'],'invalid evidence remains available for rewrite')

    def test_chinese_fields_are_normalized_in_validated_plan(self):
        data=self.fixture(8)
        choose(data,self.d,'d1',title='c06求助',mode_reason='c06严格先按SOS')
        plan=self.plan(data);plan.update(summary='c04至c08始终为同一款黑色圆形手表',split_reason='不应进入提示词方案')
        plan['segments'][0].update(what='c04至c08始终为同一款黑色圆形手表',
            check=['c04至c08始终为同一款黑色圆形手表','c06严格先按SOS'])
        checked=director.validate_plan(plan,data,self.d);row=checked['segments'][0]
        self.assertEqual(checked['summary'],'第 4–8 格始终为同一款黑色圆形手表')
        self.assertNotIn('split_reason',checked)
        self.assertEqual(row['title'],'第 6 格求助');self.assertEqual(row['what'],checked['summary'])
        self.assertEqual(row['mode_reason'],'第 6 格严格先按SOS')
        self.assertEqual(row['check'],['第 4–8 格始终为同一款黑色圆形手表','第 6 格严格先按SOS'])
        self.assertEqual(director.validate_plan(checked,data,self.d),checked)

    def test_only_actual_ids_match_and_ordinals_follow_storyboard_order(self):
        data=self.fixture(3)
        for cell,cid in zip(data['storyboard']['cells'],['c07','x.2','c04']):
            old=cell['id'];cell['id']=cid
            (self.d/f'analysis/storyboard/new-{cid}.png').write_bytes((self.d/f'analysis/storyboard/new-{old}.png').read_bytes())
        configure(data,self.d);plan=self.plan(data);plan['segments'][0]['check']=['c07至c04保持，x.2求助；abc07和c01不变']
        checked=director.validate_plan(plan,data,self.d)['segments'][0]
        self.assertEqual(checked['check'],['第 1–3 格保持，第 2 格求助；abc07和c01不变'])
        plan['segments'][0]['prompt']+=' x.2'
        self.assertTrue(director.validate_plan(plan,data,self.d)['segments'][0]['needs_rewrite'])
        plan['segments'][0]['prompt']=checked['prompt']+' abc07 c07x _c07 xA2'
        self.assertFalse(director.validate_plan(plan,data,self.d)['segments'][0]['needs_rewrite'])

    def test_forbid_prefixes_trailing_punctuation_and_empty_items(self):
        data=self.fixture(2);plan=self.plan(data)
        prose='  No original face.\nDo not show old packaging。 Never show old clothes； Noir lighting.'
        plan['segments'][0]['prompt']+=' '+prose
        plan['segments'][0]['forbid']=['old legacy constraint',None]
        row=director.validate_plan(plan,data,self.d)['segments'][0]
        self.assertTrue(row['prompt'].endswith(prose));self.assertEqual(row['prompt'],plan['segments'][0]['prompt'])
        self.assertNotIn('forbid',row);self.assertNotIn('old legacy constraint',row['prompt'])

    def test_full_and_rewrite_requests_share_new_system_rules(self):
        data=self.generated(8);target=data['director']['segments'][0]
        for segment in (None,target):
            system=director.messages(data,self.d,target=segment)[0]['content']
            for requirement in ('内部格号','图1…图N、视频1','完整动作和镜头','原片人物的脸','发饰配饰','旧商品','旧包装','没有旁白','不加小标题','逐字内容'):
                self.assertIn(requirement,system)
            for removed in ('replace_keep','timeline','固定 8 节','名词短语'):self.assertNotIn(removed,system)

    def test_save_reassembles_unedited_brief_but_preserves_manual_text(self):
        for edited in (False,True):
            with self.subTest(edited=edited):
                data=self.generated(8);old=copy.deepcopy(data['director']);old['summary']='c04至c08保持连续'
                old['segments'][0].update(edited=edited,check=['c04至c08始终为同一款黑色圆形手表','c06严格先按SOS'])
                old['segments'][0]['prompt']+='\n保持我的排版。'
                store.update(self.pid,lambda current:current.update(director=old))
                after=director.save(self.pid,{'action':'save','plan':copy.deepcopy(old)})
                saved=store.load(self.pid)['director'];row=saved['segments'][0]
                self.assertEqual(saved,after['director']);self.assertEqual(saved['summary'],'第 4–8 格保持连续')
                self.assertEqual(row['check'],['第 4–8 格始终为同一款黑色圆形手表','第 6 格严格先按SOS'])
                self.assertEqual(row['edited'],edited);self.assertEqual(row['prompt'],old['segments'][0]['prompt'])

    def test_second_segment_rewrite_keeps_full_project_numbering(self):
        data = self.generated(8, groups=[['c01', 'c02', 'c03', 'c04'], ['c05', 'c06', 'c07', 'c08']])
        previous = copy.deepcopy(data['director']['segments'][0])
        reply = self.segment(['c05', 'c06', 'c07', 'c08'], 'd2', sound='in c07, following c02.',
                             check=['c04至c08保持同款手表', 'c06严格先按SOS'], forbid=['No white SENVA bracelet.'])
        with patch.object(gateway, 'reason_checked', return_value=(reply, {})):
            after = director.save(self.pid, {'action': 'rewrite', 'segment_id': 'd2'})
        row = after['director']['segments'][1]
        self.assertNotIn('sound',row);self.assertEqual(row['prompt'],reply['prompt'])
        self.assertEqual(row['check'], ['第 4–8 格保持同款手表', '第 6 格严格先按SOS'])
        self.assertEqual(after['director']['segments'][0], previous)
        self.assertFalse(row['edited'])


if __name__ == '__main__':
    unittest.main()
