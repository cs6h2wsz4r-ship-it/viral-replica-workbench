"""V2 prompt ownership and fixed assembly contracts, all model calls mocked."""
import copy
import json
import unittest
from unittest.mock import patch

from console import director, gateway, store, storyboard, segment_plan
from tools import test_director_sb3 as fixtures


class DirectorV2Tests(unittest.TestCase):
    setUp = fixtures.DirectorSB3Tests.setUp
    segment = fixtures.DirectorSB3Tests.segment

    def fixture(self):
        from console import segment_plan, segment_boards, storyboard_edits
        data = fixtures.DirectorSB3Tests.raw_fixture(self, 8, 12.3 / 8)
        cells = data['storyboard']['cells']
        for i, c in enumerate(cells):
            c.update(start=i * 1.8 if i < 3 else 5.4 + (i - 3) * 1.38,
                     end=(i + 1) * 1.8 if i < 3 else 5.4 + (i - 2) * 1.38)
        raw = dict(summary='门廊到室内', why_segments='地点和商品状态一起变化', skip_cells=[], questions=[], segments=[
            dict(id='d01', cells=[c['id'] for c in cells[:3]], title='门廊跌倒', mode='video', refs=['C1'], gen_images=['c01']),
            dict(id='d02', cells=[c['id'] for c in cells[3:]], title='室内求助', mode='image', refs=['C1', 'C2', 'product'], gen_images=['c04','C1','C2','product'])])
        data['segments_plan'] = segment_plan.validate_plan(raw, data, self.d)
        data['segments_plan']['inputs_sig'] = segment_plan.inputs_sig(data, self.d)
        data['segment_boards'] = {'segments': segment_boards.build_sources(data, self.d)}
        for source in segment_boards.sheets(data):
            sid = source['id']; state = segment_boards.state_for(data, sid)
            files = {cid: f'analysis/storyboard/new-{cid}.png' for cid in source['cells']}
            sig = storyboard_edits.board_sig(data, sid)
            state.update(current='v1', storyboard_sig=sig,
                         plan={'sig': 'fixture', 'cells': [{'cell': cid, 'changes': '换人', 'keep': '场景'} for cid in source['cells']]},
                         versions=[dict(id='v1', file=source['file'], cells=files, storyboard_sig=sig, plan_sig='fixture')])
        return store.update(self.pid, lambda d: d.update(data))

    def fields(self, data):
        return {'summary': '逐段复刻', 'segments': [
            {k: v for k, v in self.segment(s['cells'], s['id']).items()
             if k in ('id', 'what', 'check', 'prompt')}
            for s in data['segments_plan']['segments']]}

    def test_prompt_step_has_no_segmentation_or_hand_count_patch(self):
        for removed in ('全片 15 秒以内', '每段原片不少于 3 秒', '时间轴每一行都要交代画面里能看到几只手', 'edge_notes'):
            self.assertNotIn(removed, director.SYSTEM)
        self.assertFalse(hasattr(director, 'fragmentation'))
        self.assertIn('第 04 步', director.SYSTEM)
        self.assertIn('镜头之间按原片硬切，不编造原片没有的动作和过渡', director.SYSTEM)

    def test_cell_text_uniform_note_and_no_edge_heuristic(self):
        text = storyboard.cell_text({'start': 0, 'end': 3}, {'shots': [
            {'start': 0, 'end': 2.8, 'action': '跌倒'}, {'start': 2.8, 'end': 3.1, 'action': '室内展示'}]})
        self.assertEqual(text['action'], '跌倒；室内展示')
        self.assertEqual(text['note'], '拆解文字可能混入相邻镜头的内容，以这一格的分镜图为准')
        self.assertNotIn('edge_notes', text)

    def test_old_timeline_format_is_still_legacy(self):
        self.assertTrue(director.is_legacy({'segments': [{'id': 'd1', 'timeline': []}]}))
        self.assertEqual(director.LEGACY_WARNING, '这个方案是旧版本做的，点“全部写提示词”重新生成。')

    def test_ownership_layout_context_and_one_inference(self):
        data=self.fixture();value=self.fields(data)
        with patch.object(gateway,'reason_checked',return_value=(value,{})) as reasoning:result=director.generate(self.pid)
        reasoning.assert_called_once();rows=result['director']['segments'];self.assertEqual(result['director']['format_version'],3)
        for source,row,content in zip(data['segments_plan']['segments'],rows,value['segments']):
            self.assertEqual(row['cells'],source['cells']);self.assertEqual(row['mode'],source['mode'])
            self.assertEqual(row['images'],segment_plan.generation_images(data,source,self.d));self.assertEqual(row['prompt'],content['prompt'])
            for removed in ('There is exactly one product','a second copy of the product','extra hands or arms beyond those described'):
                self.assertNotIn(removed,row['prompt'])
        request=json.dumps(reasoning.call_args.args[1],ensure_ascii=False)
        self.assertIn('拆解文字可能混入相邻镜头的内容，以这一格的分镜图为准',request)
        self.assertIn('本段',request);self.assertIn('硬上限 5000 字符',request)

    def test_model_cannot_change_plan_and_rewrite_preserves_other_part(self):
        data = self.fixture(); fields = self.fields(data)
        injected = copy.deepcopy(fields)
        injected['segments'][0].update(cells=['c08'], mode='image', images=['storyboard', 'product'], refs=['product'])
        checked = director.validate_plan(injected, data, self.d)
        self.assertEqual(checked['segments'][0]['cells'], ['c01', 'c02', 'c03'])
        self.assertEqual(checked['segments'][0]['mode'], 'video')
        self.assertEqual(checked['segments'][0]['images'], ['c01','C1','C2','product'])
        with patch.object(gateway, 'reason_checked', return_value=(fields, {})):
            saved = director.generate(self.pid)
        old = copy.deepcopy(saved['director']['segments'][1])
        with patch.object(gateway, 'reason_checked', return_value=(fields['segments'][0], {})):
            result = director.save(self.pid, {'action': 'rewrite', 'segment_id': 'd01'})
        self.assertEqual(result['director']['segments'][1], old)
        before = store.load(self.pid)
        for action, change in [('mode', {'mode': 'image'}), ('images', {'images': ['storyboard']})]:
            with self.assertRaises(ValueError):
                director.save(self.pid, {'action': action, 'segment_id': 'd01', **change})
            self.assertEqual(store.load(self.pid), before)

    def test_only_changed_segment_becomes_stale(self):
        data = self.fixture()
        with patch.object(gateway, 'reason_checked', return_value=(self.fields(data), {})):
            saved = director.generate(self.pid)
        saved['segment_boards']['segments']['d01']['current'] = 'v2'
        status = director.view(saved, self.d)
        self.assertTrue(status['segments'][0]['stale'])
        self.assertFalse(status['segments'][1]['stale'])

    def test_one_timeline_entry_per_cell_and_no_model_selected_run(self):
        data = self.fixture(); fields = self.fields(data)
        fields['segments'][0]['prompt'] += ' c01'
        fields['segments'][0]['selected_run'] = 'model-invented'
        checked = director.validate_plan(fields, data, self.d)
        self.assertTrue(checked['segments'][0]['needs_rewrite'])
        self.assertNotIn('selected_run', checked['segments'][0])
        self.assertFalse(checked['segments'][1]['needs_rewrite'])

    def test_source_cells_removed_stays_viewable_and_all_dependents_stale(self):
        from console import segment_plan, storyboard_edits
        data = self.fixture()
        with patch.object(gateway, 'reason_checked', return_value=(self.fields(data), {})):
            data = director.generate(self.pid)
        data['storyboard']['cells'].pop()
        self.assertTrue(segment_plan.status(data, self.d)['stale'])
        status = storyboard_edits.view(data, self.d)
        self.assertTrue(status['d02']['source_stale'])
        self.assertTrue(status['d02']['plan_stale'])
        self.assertTrue(director.view(data, self.d)['stale'])

    def test_preview_rejects_old_mode_or_board_before_packaging(self):
        from console import segment_plan, jimmy_bridge
        data = self.fixture()
        with patch.object(gateway, 'reason_checked', return_value=(self.fields(data), {})):
            director.generate(self.pid)
        segment_plan.save(self.pid, 'd01', {'mode': 'image'})
        with patch.object(jimmy_bridge, '_prepare_package') as package:
            with self.assertRaisesRegex(jimmy_bridge.BridgeError, '第 04 步（对照和修改）.*(?:新分镜|提示词)'):
                jimmy_bridge.director_preview(self.pid, 'd01')
            package.assert_not_called()
        data = self.fixture()
        with patch.object(gateway, 'reason_checked', return_value=(self.fields(data), {})):
            director.generate(self.pid)
        def change(d):
            state = d['segment_boards']['segments']['d01']
            state['versions'].append({**state['versions'][0], 'id': 'v2'})
            state['current'] = 'v2'
        store.update(self.pid, change)
        with patch.object(jimmy_bridge, '_prepare_package') as package:
            with self.assertRaisesRegex(jimmy_bridge.BridgeError, '第 04 步（对照和修改）.*提示词'):
                jimmy_bridge.director_preview(self.pid, 'd01')
            package.assert_not_called()


if __name__ == '__main__':
    unittest.main()
