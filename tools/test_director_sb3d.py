"""SB3d structured director plans; all reasoning is mocked and media is local."""
import copy
import json
import unittest
from unittest.mock import patch

from tools import test_director_sb3 as fixtures
from console import director, gateway, store, storyboard_edits
from tools.director_sb3d_media_cases import DirectorSB3dMediaCases
from tools.v2_fixture import configure, choose


def structured_segment(ids, sid='d1', **changes):
    row = dict(id=sid, title='父子求助', cells=ids, what='父亲抬起手腕，儿子走近。',
               mode='video', mode_reason='保留原片动作节奏', images=['storyboard','C1','C2','product'],
               check=['手表戴在左手腕，表盘朝外'],
               replace_keep='Replace the father and son with the supplied identities. Keep the black watch, its round face and strap, and the original room.',
               timeline=[dict(cells=[cid], text='The father raises his left wrist. The son approaches and says exactly "别担心". Keep the watch face outward.') for cid in ids],
               continuity='Keep the watch on the left wrist while the son supports the father.',
               sound='Room ambience and the son’s confirmed dialogue only; no narration or music.',
               forbid=['the original elderly face', 'the white SOS bracelet', 'SENVIA packaging'])
    row.update(changes)
    return row


class DirectorSB3dTests(DirectorSB3dMediaCases, unittest.TestCase):
    setUp = fixtures.DirectorSB3Tests.setUp
    fixture = fixtures.DirectorSB3Tests.fixture
    plan = fixtures.DirectorSB3Tests.plan
    generated = fixtures.DirectorSB3Tests.generated
    segment = fixtures.DirectorSB3Tests.segment

    def invoke(self, replies):
        with patch.object(gateway, 'reason_checked', side_effect=[(v,{}) for v in replies]) as model:
            result = director.generate(self.pid)
        return result, model

    def test_system_minimum_segments_modes_skip_and_partial_people(self):
        from console import segment_plan
        for text in ('较少段数','镜头切点只是候选','多次硬切','旧包装','skip_cells','15 秒','6 格'):
            self.assertIn(text,segment_plan.SYSTEM)
        for text in ('分段：','两段够用绝不分三段','每段原片不少于 3 秒','哪怕多花钱','tradeoff','risk','start_state','end_state'):
            self.assertNotIn(text,director.SYSTEM)
        for text in ('手、手臂、腿、背影或衣角','也算','肤色','袖口','不能留空'):
            self.assertIn(text,storyboard_edits.SYSTEM)

    def test_eight_sections_exact_material_order_and_stretched_timeline(self):
        data=self.fixture(3,4.1);choose(data,self.d,'d1',refs=['product','C2','C1'])
        value=self.plan(data);row=director.validate_plan(value,data,self.d)['segments'][0]
        self.assertEqual(row['prompt'],value['segments'][0]['prompt'])
        refs=director.generation_materials(data,row,self.d)
        self.assertEqual([r['key'] for r in refs],['c01','product','C2','C1','source_video'])
        self.assertEqual([r['label'] for r in refs],['图1','图2','图3','图4','视频1'])
        self.assertEqual([r['description'] for r in refs[1:4]],['新商品照片','THE SON 的人物照','THE FATHER 的人物照'])
        self.assertEqual(refs[-1]['description'],'原片 0–12.3 秒，均匀拉伸到 13 秒')
        text=json.dumps(director.messages(data,self.d),ensure_ascii=False)
        for timing in ('4.3','8.7','13.0'):self.assertIn(timing,text)
        self.assertAlmostEqual(row['stretch'],13/12.3);self.assertEqual(row['target_seconds'],13)
        choose(data,self.d,'d1',mode='image');image=director.validate_plan(self.plan(data),data,self.d)['segments'][0]
        self.assertNotIn('视频1',image['prompt']);self.assertFalse(image['needs_rewrite'])

    def test_grouped_timeline_relative_panel_numbers_and_nonzero_source(self):
        data=self.fixture(4,2);plan=self.plan(data,[['c01','c02'],['c03','c04']])
        row=director.validate_plan(plan,data,self.d)['segments'][1]
        self.assertEqual(row['source_start'],4);self.assertEqual(row['source_end'],8)
        parts=director.messages(data,self.d,target=row)[1]['content']
        text=next(p['text'] for p in parts if p['type']=='text' and '每格成片时间窗：' in p['text'])
        windows=json.loads(text.split('每格成片时间窗：',1)[1].split('\n',1)[0])
        self.assertEqual([(w['cell'],w['start'],w['end']) for w in windows],[('c03',0.0,2.0),('c04',2.0,4.0)])
        self.assertFalse(row['needs_rewrite'])

    def test_content_errors_only_mark_affected_segment(self):
        data=self.fixture(4,2)
        for change in ({'prompt':''},{'prompt':None},{'check':[]},{'check':[1]},{'prompt':'图1 视频1'}, {'prompt':'图1 图2 图3 图4 图5 视频1'}, {'prompt':'x'*5100}):
            with self.subTest(change=change):
                plan=self.plan(data,[['c01','c02'],['c03','c04']]);plan['segments'][0].update(change)
                rows=director.validate_plan(plan,data,self.d)['segments']
                self.assertTrue(rows[0]['needs_rewrite']);self.assertFalse(rows[1]['needs_rewrite'])

    def test_optional_fields_default_empty_without_old_advisories(self):
        data=self.fixture(2);value=self.plan(data)
        value['segments'][0].pop('what');value['segments'][0].pop('check')
        row=director.validate_plan(value,data,self.d)['segments'][0]
        self.assertEqual(row['what'],'');self.assertIsNone(row['check']);self.assertNotIn('sound',row)
        self.assertTrue(row['needs_rewrite']);self.assertEqual(row['warnings'],[])

    def test_skip_suffix_and_structural_errors_fail_whole_plan(self):
        from console import segment_plan
        data=self.fixture(4,2);configure(data,self.d,[['c01','c02']],skip_cells=['c03','c04'])
        checked=director.validate_plan(self.plan(data),data,self.d)
        self.assertEqual([s['cells'] for s in checked['segments']],[['c01','c02']])
        self.assertEqual(data['segments_plan']['skip_cells'],['c03','c04'])
        for skip in (['c03'],['c04','c03'],['c02','c04'],['c03','c03'],['c99'],None):
            bad=copy.deepcopy(data['segments_plan']);bad['skip_cells']=skip
            with self.subTest(skip=skip),self.assertRaisesRegex(ValueError,'分段方案不完整'):
                segment_plan.validate_plan(bad,data,self.d)
        for change in ({'mode':'bad'},{'refs':['absent']},{'cells':['c02','c01']}):
            bad=copy.deepcopy(data['segments_plan']);bad['segments'][0].update(change)
            with self.assertRaises(ValueError):segment_plan.validate_plan(bad,data,self.d)

    def test_three_segments_retry_second_is_saved_and_both_archived(self):
        data=self.fixture(6,2.05)
        first=self.plan(data,[['c01','c02'],['c03','c04'],['c05','c06']])
        after,model=self.invoke([first])
        model.assert_called_once();self.assertEqual(len(after['director']['segments']),3)
        self.assertNotIn('上一版分成了',json.dumps(model.call_args.args[1],ensure_ascii=False))
        files=sorted((self.d/'analysis/director/responses').glob('*-full.json'))
        self.assertEqual([json.loads(p.read_text()) for p in files],[first])

    def test_two_fragmented_replies_save_warning_from_second_plan(self):
        data=self.fixture(6,2)
        value=self.plan(data,[['c01'],['c02','c03'],['c04'],['c05','c06']])
        after,model=self.invoke([value]);model.assert_called_once()
        self.assertEqual(len(after['director']['segments']),4)
        self.assertNotIn('segmentation_warning',after['director'])
        self.assertEqual(director.view(after,self.d)['warnings'],[])
        saved=director.save(self.pid,{'action':'save','plan':after['director']})
        self.assertNotIn('segmentation_warning',saved['director'])

    def test_short_segment_retries_but_twenty_two_seconds_two_segments_do_not(self):
        for seconds,groups in ((2,[['c01'],['c02','c03','c04']]),(5.5,[['c01','c02'],['c03','c04']])):
            data=self.fixture(4,seconds);value=self.plan(data,groups)
            after,model=self.invoke([value]);model.assert_called_once()
            self.assertEqual([s['cells'] for s in after['director']['segments']],groups)
            self.assertNotIn('每段原片不少于 3 秒',json.dumps(model.call_args.args[1],ensure_ascii=False))

    def test_skipped_time_excluded_and_short_whole_video_allowed(self):
        data=self.fixture(6,3)
        configure(data,self.d,[['c01'],['c02'],['c03']],skip_cells=['c04','c05','c06'])
        after,model=self.invoke([self.plan(data)]);model.assert_called_once()
        self.assertEqual(sum(s['source_seconds'] for s in after['director']['segments']),9)
        data=self.fixture(1,2);after,model=self.invoke([self.plan(data)])
        model.assert_called_once();self.assertEqual(after['director']['segments'][0]['duration'],4)

    def test_auto_rewrite_each_bad_segment_once_after_plan_saved(self):
        data=self.fixture(4,2);value=self.plan(data,[['c01','c02'],['c03','c04']])
        good=copy.deepcopy(value['segments'][0]);value['segments'][0]['prompt']=''
        value['segments'][1]['prompt']='x'*5100
        still_bad=copy.deepcopy(value['segments'][1])
        replies=iter([value,good,still_bad])
        def reply(*args,**kwargs):
            result=next(replies)
            if kwargs.get('max_tokens')==6000:self.assertIn('director',store.load(self.pid))
            return result,{}
        with patch.object(gateway,'reason_checked',side_effect=reply) as model:
            after=director.generate(self.pid)
        self.assertEqual(model.call_count,3)
        self.assertFalse(after['director']['segments'][0]['needs_rewrite'])
        self.assertTrue(after['director']['segments'][1]['needs_rewrite'])
        self.assertIn('硬上限 5000 字符',json.dumps(model.call_args.args[1],ensure_ascii=False))
        self.assertEqual(len(list((self.d/'analysis/director/responses').glob('*.json'))),3)

    def test_auto_rewrite_failure_keeps_saved_plan(self):
        data=self.fixture(2);value=self.plan(data);value['segments'][0]['prompt']=''
        with patch.object(gateway,'reason_checked',side_effect=[(value,{}),ValueError('mock unavailable')]):
            after=director.generate(self.pid)
        self.assertTrue(after['director']['segments'][0]['needs_rewrite'])
        self.assertTrue(after['log'])

    def test_auto_rewrite_stops_if_another_tab_edits_plan_or_inputs(self):
        for edit in ('manual', 'still_invalid_manual', 'inputs'):
            with self.subTest(edit=edit):
                data=self.fixture(4,2)
                value=self.plan(data,[['c01','c02'],['c03','c04']])
                replies=copy.deepcopy(value['segments'])
                for row in value['segments']:row['prompt']=''
                calls=[]
                manual='Manual text from another tab.' if edit=='manual' else 'x'*5001
                def reply(*args,**kw):
                    calls.append(kw)
                    if len(calls)==1:return value,{}
                    if len(calls)==2:
                        if edit=='inputs':
                            store.update(self.pid,lambda d:d['replacements']['product'].update(description='Updated in another tab'))
                        else:
                            director.save(self.pid,{'action':'prompt','segment_id':'d2','prompt':manual})
                        return replies[0],{}
                    return replies[1],{}
                with patch.object(gateway,'reason_checked',side_effect=reply):
                    after=director.generate(self.pid)
                self.assertEqual(len(calls),2,'changed plan/input must stop remaining automatic calls')
                if edit!='inputs':
                    self.assertEqual(after['director']['segments'][1]['prompt'],manual)
                    self.assertTrue(after['director']['segments'][1]['edited'])

    def test_manual_brief_survives_save_rewrite_reassembles_and_clears_edit(self):
        data=self.fixture(2);after,_=self.invoke([self.plan(data)])
        plan=after['director'];plan['segments'][0]['prompt']='Manual English instructions.'
        after=director.save(self.pid,{'action':'save','plan':plan})
        self.assertEqual(after['director']['segments'][0]['prompt'],'Manual English instructions.')
        self.assertTrue(after['director']['segments'][0]['edited'])
        reply=self.segment(['c01','c02']);reply['prompt']+=' New replacement description.'
        with patch.object(gateway,'reason_checked',return_value=(reply,{})):
            after=director.save(self.pid,{'action':'rewrite','segment_id':'d1'})
        row=after['director']['segments'][0]
        self.assertFalse(row['edited']);self.assertIn('New replacement description.',row['prompt'])
        self.assertNotIn('Manual English instructions.',row['prompt'])

    def test_merge_split_rejected_and_legacy_requires_regeneration(self):
        self.generated(2)
        for action in ('merge','split'):
            with self.assertRaisesRegex(ValueError,'未知的导演方案操作'):
                director.save(self.pid,{'action':action,'segment_id':'d1','before_cell':'c02'})
        data=store.update(self.pid,lambda d:d['director'].pop('format_version'))
        view=director.view(data,self.d)
        self.assertTrue(view['legacy']);self.assertTrue(view['stale'])
        value=self.plan(data)['segments'][0]
        with patch.object(gateway,'reason_checked',return_value=(value,{})) as model:
            saved=director.save(self.pid,{'action':'rewrite','segment_id':'d1'})
            model.assert_called_once()
        self.assertEqual(saved['director']['format_version'],3)
        self.assertEqual(saved['director']['segments'][0]['prompt'],value['prompt'])
        self.assertFalse(saved['director']['segments'][0]['needs_rewrite'])


if __name__ == '__main__':
    unittest.main()
