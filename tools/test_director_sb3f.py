"""SB3f cell-local descriptions and physical constraints, with synthetic inputs only."""
import copy
import json
import unittest
from unittest.mock import patch

from console import director, gateway, store, storyboard, storyboard_edits
from tools import test_director_sb3 as fixtures
from tools.v2_fixture import configure, choose


EDGE_NOTE = '拆解文字可能混入相邻镜头的内容，以这一格的分镜图为准'
ONE_PRODUCT = ('There is exactly one product in the whole video. Once it is worn or in use, '
               "never show a second copy in anyone's hands or anywhere else.")
EXTRA_LIMBS = 'extra hands or arms beyond those described'
SECOND_PRODUCT = 'a second copy of the product'


def source_shots():
    """Same time windows and data shape as the reported c03/c06 case, constructed here."""
    rows = [
        ('K5', 3.83, 5.1, '老人坐在门廊上，儿子走近'),
        ('K6', 5.1, 5.33, '双手举着包装'),
        ('K13', 7.5, 8.0, '俯拍特写，手表戴在老人左腕'),
        ('K14', 8.0, 8.4, 'C3 的右手食指按下 SOS 按钮'),
        ('K15', 8.4, 8.8, 'C3 左手拿着的手机弹出告警'),
        ('K16', 8.8, 9.0, '老人倒在瓷砖地上'),
    ]
    return [dict(id=sid, start=start, end=end, characters=['C1', 'C3'] if sid in ('K13', 'K14', 'K15') else ['C1', 'C2'],
                 action=action, product='SOS 手环') for sid, start, end, action in rows]


EXPECTED = {
    'c03': dict(action='老人坐在门廊上，儿子走近', edge_notes='双手举着包装'),
    'c06': dict(action='俯拍特写，手表戴在老人左腕；C3 的右手食指按下 SOS 按钮；C3 左手拿着的手机弹出告警',
                edge_notes='老人倒在瓷砖地上'),
}


class CellTextSB3fTests(unittest.TestCase):
    def cell_text(self, cell, breakdown):
        helper = getattr(storyboard, 'cell_text', None)
        self.assertTrue(callable(helper), 'cell_text must recompute cell-local descriptions')
        return helper(cell, breakdown)

    def test_reported_c03_and_c06_separate_adjacent_shots(self):
        pack = {'shots': list(reversed(source_shots()))}
        for cid, start, end in (('c03', 3.833, 5.333), ('c06', 7.5, 9.0)):
            with self.subTest(cell=cid):
                self.assertEqual(self.cell_text(dict(id=cid,start=start,end=end),pack),dict(action=EXPECTED[cid]['action']+'；'+EXPECTED[cid]['edge_notes'],note=EDGE_NOTE))

    def test_start_edge_inclusive_thresholds_and_just_outside(self):
        cases = [
            ('start edge', 2.9, 3.4, True),
            ('start beyond edge', 2.9001, 3.4001, False),
            ('end edge', 4.6, 5.1, True),
            ('end before edge', 4.5999, 5.0999, False),
            ('over half second at start', 2.8999, 3.4, False),
            ('over half second at end', 4.6, 5.1001, False),
            ('middle short shot', 3.8, 4.0, False),
        ]
        for name, start, end, edge in cases:
            with self.subTest(case=name):
                result = self.cell_text(dict(start=3, end=5),
                                        {'shots': [dict(start=start, end=end, action=name)]})
                self.assertEqual(result,dict(action=name,note=EDGE_NOTE))
                self.assertNotIn('edge_notes',result)

    def test_decimal_boundary_is_inclusive_without_broadening_threshold(self):
        for start, end, edge in ((4.6, 5.1, True), (4.6, 5.1000000001, False)):
            with self.subTest(start=start, end=end):
                result = self.cell_text(dict(start=3, end=5),
                                        {'shots': [dict(start=start, end=end, action='边界描述')]})
                self.assertEqual(result,dict(action='边界描述',note=EDGE_NOTE))

    def test_long_shot_duration_is_not_clipped_to_cell_overlap(self):
        result = self.cell_text(dict(start=3, end=5), {'shots': [
            dict(start=2, end=3.2, action='长窗跨过格子开头'),
            dict(start=4.8, end=6, action='长窗跨过格子结尾'),
        ]})
        self.assertEqual(result, dict(action='长窗跨过格子开头；长窗跨过格子结尾',note=EDGE_NOTE))

    def test_empty_actions_and_nonoverlapping_shots_are_ignored(self):
        cell = dict(start=3, end=5, action='旧摘要', action_full='旧的完整描述')
        pack = {'shots': [dict(start=2, end=3, action='前一格'),
                          dict(start=5, end=6, action='后一格'),
                          dict(start=3, end=4, action='  '), dict(start=4, end=4.2)]}
        self.assertEqual(self.cell_text(cell, pack), dict(action='',note=EDGE_NOTE))
        self.assertEqual(self.cell_text(cell, {}), dict(action='',note=EDGE_NOTE))

    def test_inputs_and_legacy_cell_data_stay_unchanged(self):
        pack = {'shots': source_shots()}
        cell = dict(id='c06', start=7.5, end=9, action='保留用户摘要', action_full='保留历史完整描述')
        before = copy.deepcopy((cell, pack))
        legacy = storyboard.cell_data(cell['start'], cell['end'], pack)
        self.cell_text(cell, pack)
        self.assertEqual((cell, pack), before)
        self.assertEqual(storyboard.cell_data(cell['start'], cell['end'], pack), legacy)
        self.assertEqual(legacy['action_full'], EXPECTED['c06']['action'] + '；' + EXPECTED['c06']['edge_notes'])


class DirectorSB3fTests(unittest.TestCase):
    setUp = fixtures.DirectorSB3Tests.setUp
    fixture = fixtures.DirectorSB3Tests.fixture
    segment = fixtures.DirectorSB3Tests.segment
    plan = fixtures.DirectorSB3Tests.plan

    def reported_fixture(self):
        data = self.fixture(8)
        bounds = [0, 2, 3.833, 5.333, 6.5, 7.5, 9, 10.5, 12.3]
        data['source']['probe']['duration'] = 12.3
        data['breakdown']['overview']['duration'] = 12.3
        data['breakdown']['shots'] = source_shots()
        data['breakdown']['characters'].append({'id': 'C3', 'name': '操作人'})
        for index, cell in enumerate(data['storyboard']['cells']):
            start, end = bounds[index:index + 2]
            cell.update(start=start, end=end, frame_at=(start + end) / 2)
            if cell['id'] in EXPECTED:
                expected = EXPECTED[cell['id']]
                cell.update(action='已保存的摘要', action_full=expected['action'] + '；' + expected['edge_notes'])
            if cell['id'] == 'c06':
                cell['characters'] = ['C1', 'C3']
        for sid, state in data['storyboard_edits']['sheets'].items():
            sig = storyboard_edits.board_sig(data, sid)
            state['storyboard_sig'] = sig
            for version in state['versions']:
                version['storyboard_sig'] = sig
        return configure(data,self.d,[['c01','c02','c03'],['c04','c05','c06','c07','c08']])

    def assert_request(self,request,expected_ids):
        # This historical fixture has no reconstructed speech_lines. Its request
        # must retain the pre-V7 system bytes until explicit speech migration.
        self.assertEqual(request[0]['content'],director.SILENT_SYSTEM)
        contexts=[]
        for part in request[1]['content']:
            if part['type']=='text' and '\n每格拆解文字和改图方案：' in part['text']:
                contexts.extend(json.loads(part['text'].split('\n每格拆解文字和改图方案：',1)[1].split('\n每条完整提示词',1)[0]))
        self.assertEqual([cell['id'] for cell in contexts],expected_ids)
        for cell in contexts:
            self.assertEqual(cell['note'],EDGE_NOTE);self.assertNotIn('edge_notes',cell)
            if cell['id'] in EXPECTED:
                expected=EXPECTED[cell['id']]
                self.assertEqual(cell['action'],expected['action']+'；'+expected['edge_notes'])

    def test_full_generation_request_uses_new_context_without_rebuilding_saved_board(self):
        data = self.reported_fixture()
        before = copy.deepcopy(data['storyboard'])
        with patch.object(gateway, 'reason_checked', return_value=(self.plan(data), {})) as model:
            after = director.generate(self.pid)
        model.assert_called_once()
        self.assert_request(model.call_args.args[1],[f'c{i:02d}' for i in range(1,9)])
        self.assertEqual(after['storyboard'], before)
        self.assertEqual(store.load(self.pid)['storyboard'], before)

    def test_single_segment_rewrite_uses_new_context_and_preserves_saved_board(self):
        data = self.reported_fixture()
        plan = director.validate_plan(self.plan(data), data, self.d)
        store.update(self.pid, lambda current: current.update(director=plan))
        before = copy.deepcopy(data['storyboard'])
        reply = self.segment(data['segments_plan']['segments'][0]['cells'])
        with patch.object(gateway, 'reason_checked', return_value=(reply, {})) as model:
            after = director.save(self.pid, {'action': 'rewrite', 'segment_id': 'd1'})
        model.assert_called_once()
        self.assert_request(model.call_args.args[1],['c01','c02','c03'])
        self.assertIn('单个段落 JSON',json.dumps(model.call_args.args[1],ensure_ascii=False))
        self.assertNotIn(ONE_PRODUCT,after['director']['segments'][0]['prompt'])
        self.assertEqual(after['storyboard'], before)
        self.assertEqual(store.load(self.pid)['storyboard'], before)

    def test_full_and_rewrite_system_require_visible_panel_camera_hands_and_product(self):
        data=self.reported_fixture();data['director']=self.plan(data)
        for target in (None,data['director']['segments'][0]):
            system=director.messages(data,self.d,target=target)[0]['content']
            for phrase in ('不写素材里看不到','每格的新画面','机位或景别','人物动作','商品状态','按原片硬切','不编造原片没有的动作和过渡','手、接触和商品结构物理可信'):
                self.assertIn(phrase,system)
            for phrase in ('写清手和商品','几只手','分别是谁的',"two of C3's hands",'left hand holds the phone'):
                self.assertNotIn(phrase,system)

    def test_system_timeline_instructions_and_example_use_one_entry_per_cell(self):
        self.assertIn('用请求给出的成片时间窗',director.SYSTEM)
        self.assertIn('只写清每个时间窗在做什么和关键状态',director.SYSTEM)
        example=json.loads(director.SYSTEM.split('输出：',1)[1].split('\n',1)[0])['segments'][0]
        self.assertEqual(set(example),{'id','what','check','prompt'})
        self.assertNotIn('timeline',example);self.assertNotIn('cells',example)

    def test_assembled_constraints_follow_this_segments_product_reference(self):
        data=self.reported_fixture()
        for refs in (['C1','product'],['C1']):
            choose(data,self.d,'d1',refs=refs);plan=self.plan(data);before=copy.deepcopy(plan)
            segment=director.validate_plan(plan,data,self.d)['segments'][0]
            for removed in (ONE_PRODUCT,SECOND_PRODUCT,EXTRA_LIMBS):self.assertNotIn(removed,segment['prompt'])
            self.assertIn('手、接触和商品结构物理可信',director.SYSTEM)
            self.assertEqual(segment['images'],list(dict.fromkeys(['c01',*refs,'C1','C2','product'])));self.assertEqual(plan,before)
            materials=director.references(data,segment,self.d)
            self.assertEqual([m['key'] for m in materials],list(dict.fromkeys(['c01',*refs,'C1','C2','product'])))


if __name__ == '__main__':
    unittest.main()
