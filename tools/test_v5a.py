"""Live saved casting and partial director authoring; all media/LLM calls are fake."""
import copy
import json
import unittest
from unittest.mock import patch

from PIL import Image
from console import director, gateway, jimmy_bridge, pipeline, scene_prompt, segment_plan, segment_boards, store, storyboard, storyboard_edits
from tools import test_director_sb3 as fixtures
from tools import director_sb3_bridge_cases as bridge_fixtures
from tools.v2_fixture import configure


class LiveCastingTests(unittest.TestCase):
    setUp = fixtures.DirectorSB3Tests.setUp

    def fixture(self):
        data = fixtures.DirectorSB3Tests.raw_fixture(self, 4, 1)
        for row in data['replacements']['characters']:
            row['action'] = 'keep'
        data['replacements']['product']['action'] = 'keep'
        return configure(data, self.d, [['c01', 'c02'], ['c03', 'c04']], {
            'd1': {'mode': 'image', 'refs': [], 'gen_images': ['c01']},
            'd2': {'mode': 'image', 'refs': [], 'gen_images': ['c03']}})

    def replace(self, data, key='C1'):
        row = (data['replacements']['product'] if key == 'product' else
               next(r for r in data['replacements']['characters'] if r['source_id'] == key))
        row['action'] = 'replace'
        store.update(self.pid, lambda d: d.update(copy.deepcopy(data)))
        return data

    def fields(self, data, sid):
        segment = segment_plan.get_segment(data, sid)
        images = segment_plan.generation_images(data, segment, self.d)
        return {'id': sid, 'what': '完成本段动作', 'check': ['外观一致'],
                'prompt': '；'.join(f'图{i} 提供目标外观' for i in range(1, len(images) + 1))}

    def test_saved_cast_does_not_expire_segmentation_but_updates_materials_and_edit_request(self):
        data = self.fixture(); signature = segment_plan.inputs_sig(data, self.d)
        self.replace(data)
        self.assertEqual(signature, segment_plan.inputs_sig(data, self.d))
        self.assertFalse(segment_plan.status(data, self.d)['stale'])
        self.assertEqual(storyboard_edits.materials_for(data, 'd1', self.d), {'C1': True})
        self.assertTrue(storyboard_edits.view(data, self.d)['d1']['casting_stale'])
        request = storyboard_edits.plan_messages(data, 'd1', '', {'C1': True}, self.d)
        self.assertIn('C1', json.dumps(request, ensure_ascii=False))
        seen = []
        with patch.object(storyboard_edits.llm, 'image_part', side_effect=lambda p: seen.append(p) or []):
            storyboard_edits.plan_messages(data, 'd1', '', {'C1': True}, self.d)
        self.assertIn(self.d / 'assets/father.png', seen)
        data['replacements']['characters'][0]['action'] = 'keep'
        self.assertEqual(storyboard_edits.materials_for(data, 'd1', self.d), {})
        self.assertEqual(segment_plan.generation_images(data, data['segments_plan']['segments'][0], self.d), ['c01'])

    def test_manual_off_survives_only_unchanged_photo(self):
        data = self.replace(self.fixture())
        storyboard_edits.save(self.pid, 'd1', {'materials': {'C1': False}})
        data = store.load(self.pid)
        self.assertEqual(storyboard_edits.materials_for(data, 'd1', self.d), {'C1': False})
        Image.new('RGB', (20, 30), 'red').save(self.d / 'assets/father.png')
        self.assertEqual(storyboard_edits.materials_for(data, 'd1', self.d), {'C1': True})

    def test_live_photos_order_presence_product_later_and_coverage_budget(self):
        data = self.fixture(); self.replace(data); self.replace(data, 'C2'); self.replace(data, 'product')
        cells = data['storyboard']['cells']
        cells[0].update(characters=['C2', 'C1'], product='', product_later=True)
        segment = data['segments_plan']['segments'][0]
        self.assertEqual([r['key'] for r in segment_plan.segment_photos(data, segment, self.d)], ['C2', 'C1', 'product'])
        self.assertNotIn('product', [r['key'] for r in segment_plan.segment_photos(data, data['segments_plan']['segments'][1], self.d)])
        segment.update(cells=['c01', 'c02', 'c03', 'c04'], gen_images=['c01', 'c02', 'c03', 'c04'])
        self.assertEqual(segment_plan.generation_images(data, segment, self.d), ['c01', 'C2', 'C1', 'product'])
        self.assertNotIn('N1', [r['key'] for r in segment_plan.segment_photos(data, segment, self.d)])

    def test_single_segment_initializes_without_other_boards_and_can_preview(self):
        data = self.fixture()
        data['segment_boards']['segments']['d2'].update(current=None, versions=[])
        store.update(self.pid, lambda d: d.update(data))
        value = self.fields(data, 'd1')
        with patch.object(gateway, 'reason_checked', return_value=(value, {})) as reason:
            result = director.save(self.pid, {'action': 'rewrite', 'segment_id': 'd1'})
        first, second = result['director']['segments']
        self.assertFalse(first['needs_rewrite'])
        self.assertEqual(second['prompt'], '')
        self.assertTrue(second['needs_rewrite'])
        request = reason.call_args.args[1]
        self.assertEqual(request[0]['content'], director.SILENT_SYSTEM)
        self.assertIn('c03', json.dumps(request, ensure_ascii=False))
        self.assertEqual(first['prompt'], director.validate_plan({'segments': [value]}, data, self.d, target_id='d1')['segments'][0]['prompt'])
        with patch.object(jimmy_bridge, '_call', side_effect=lambda args, cwd, **kw: bridge_fixtures.DirectorBridgeTests.package(self, args, cwd, **kw)):
            preview = jimmy_bridge.director_preview(self.pid, 'd1')
        self.assertEqual(preview['state'], 'pending_confirmation')
        self.assertEqual(store.load(self.pid)['director']['segments'][1], second)
        draft=copy.deepcopy(result['director']);draft['segments'][0]['prompt']+='；保持动作'
        saved=director.save(self.pid,dict(action='save',director=draft))
        self.assertEqual(saved['director']['segments'][0]['prompt'],draft['segments'][0]['prompt'])
        self.assertEqual(saved['director']['segments'][0]['inputs_sig'],first['inputs_sig'])
        self.assertEqual(saved['director']['segments'][1]['prompt'],'')

    def test_new_package_and_review_use_latest_photos(self):
        data = self.replace(self.fixture())
        # Simulate completing a board under the latest saved selection.
        state = data['segment_boards']['segments']['d1']
        state['settings_sig'] = storyboard_edits.settings_sig(data, 'd1', self.d, {'C1': True})
        state['versions'][0]['settings_sig'] = state['settings_sig']
        store.update(self.pid, lambda d: d.update(data))
        with patch.object(gateway, 'reason_checked', return_value=(self.fields(data, 'd1'), {})):
            director.save(self.pid, {'action': 'rewrite', 'segment_id': 'd1'})
        with patch.object(jimmy_bridge, '_call', side_effect=lambda args, cwd, **kw: bridge_fixtures.DirectorBridgeTests.package(self, args, cwd, **kw)):
            preview = jimmy_bridge.director_preview(self.pid, 'd1')
        self.assertEqual([r['key'] for r in preview['references']], ['c01', 'C1'])
        self.assertEqual([r['key'] for r in preview['review_inputs']['references']], ['c01', 'C1'])
        photo = self.d / preview['references'][1]['file']
        self.assertEqual(photo.read_bytes(), (self.d / 'assets/father.png').read_bytes())

    def test_repreparing_plan_does_not_refresh_an_old_paid_board(self):
        data=self.replace(self.fixture())
        value=dict(summary='更新选角',remove_text=True,questions=[],cells=[
            dict(cell=cid,changes='使用新照片',keep='动作') for cid in ['c01','c02']])
        with patch.object(gateway,'reason_checked',return_value=(value,{})):
            prepared=storyboard_edits.generate_plan(self.pid,'d1',{})
        status=storyboard_edits.view(prepared,self.d)['d1']
        self.assertFalse(status['plan_stale'])
        self.assertTrue(status['casting_stale'])
        with self.assertRaisesRegex(ValueError,'旧选角'):
            director.require_ready(prepared,self.d,'d1')

    def test_legacy_off_switches_and_unused_false_keys_do_not_expire_boards(self):
        data=fixtures.DirectorSB3Tests.raw_fixture(self,2,1)
        data=configure(data,self.d,[['c01','c02']])
        state=data['segment_boards']['segments']['d1']
        for off in (False,True):
            materials={'C1':not off,'C2':True,'N1':False,'product':True}
            options=storyboard_edits.material_options(data,'d1',self.d)
            selected=[{**m,'hash':store.sha256_file(self.d/m['file'])} for m in options if materials.get(m['key'])]
            state.update(materials=materials,settings_sig=storyboard_edits.digest([
                selected,materials,data['replacements']['product']['story_checks']]))
            state.pop('materials_photos',None)
            self.assertEqual(storyboard_edits.materials_for(data,'d1',self.d)['C1'],not off)
            self.assertFalse(storyboard_edits.casting_stale(data,'d1',self.d))
            self.assertNotIn(storyboard_edits.SETTINGS_WARNING,storyboard_edits.warnings(data,'d1',self.d))
        store.update(self.pid,lambda d:d.update(data))
        saved=storyboard_edits.save(self.pid,'d1',{})
        self.assertFalse(storyboard_edits.materials_for(saved,'d1',self.d)['C1'])
        self.assertFalse(storyboard_edits.casting_stale(saved,'d1',self.d))
        Image.new('RGB',(20,30),'orange').save(self.d/'assets/father.png')
        self.assertTrue(storyboard_edits.materials_for(saved,'d1',self.d)['C1'])

    def test_legacy_off_override_resets_when_photo_changed_via_step03_save(self):
        data=self.replace(self.fixture());segment=data['segments_plan']['segments'][0]
        segment['refs']=['C1'];state=data['segment_boards']['segments']['d1']
        state.update(materials={'C1':False},materials_segment_sig=segment_plan.segment_sig(segment),
                     settings_sig=storyboard_edits.digest([[],{'C1':False},data['replacements']['product']['story_checks']]))
        store.update(self.pid,lambda d:d.update(data))
        changed=copy.deepcopy(data['replacements']);changed['characters'][0]['asset_id']='doctor'
        saved=pipeline.save_replacements(self.pid,changed)
        self.assertTrue(storyboard_edits.materials_for(saved,'d1',self.d)['C1'])

    def test_legacy_wrong_image_default_off_is_not_mistaken_for_manual_off(self):
        data=self.replace(self.fixture());state=data['segment_boards']['segments']['d1']
        # V5 stored this default False after refs=[] was followed by a cast save.
        state['materials']={'C1':False}
        state['settings_sig']=storyboard_edits.digest([[],{'C1':False},data['replacements']['product']['story_checks']])
        self.assertEqual(storyboard_edits.materials_for(data,'d1',self.d),{'C1':True})
        self.assertTrue(storyboard_edits.casting_stale(data,'d1',self.d))

    def test_unready_neighbor_with_impossible_coverage_does_not_block_target(self):
        data=self.fixture()
        data['storyboard']['cells'][2]['characters']=[]
        data['storyboard']['cells'][3]['characters']=[f'X{i}' for i in range(5)]
        data['replacements']['characters'] += [dict(source_id=f'X{i}',action='replace',asset_id='father') for i in range(5)]
        data['segments_plan']['inputs_sig']=segment_plan.inputs_sig(data,self.d)
        data['segment_boards']['segments']['d2'].update(current=None,versions=[])
        store.update(self.pid,lambda d:d.update(data))
        with self.assertRaisesRegex(ValueError,'四张图不足'):
            segment_plan.generation_images(data,data['segments_plan']['segments'][1],self.d)
        with patch.object(gateway,'reason_checked',return_value=(self.fields(data,'d1'),{})):
            saved=director.save(self.pid,dict(action='rewrite',segment_id='d1'))
        self.assertFalse(saved['director']['segments'][0]['needs_rewrite'])
        self.assertEqual(saved['director']['segments'][1]['prompt'],'')

    def test_legacy_segmentation_migrates_on_save_but_rejects_changed_source(self):
        data=self.fixture();board=data['storyboard']
        (self.d/'analysis/boundaries.json').write_text(json.dumps({'merged_shots':data['breakdown']['shots']}))
        board['source_sig']=storyboard.source_sig(data,self.d)
        data['segment_boards']={'segments':segment_boards.build_sources(data,self.d)}
        originals={file:segment_plan._hash(self.d,file) for file in
            [c['file'] for c in board['cells']]+[s['file'] for s in board['sheets']]}
        photos=[{**m,'role':scene_prompt.signature_role_label(m['key'],m['role']),
                 'sha256':segment_plan._hash(self.d,m['file'])} for m in segment_plan.material_options(data,self.d)]
        data['segments_plan']['inputs_sig']=segment_plan.digest([board,originals,data['breakdown'],data['replacements'],photos])
        self.assertFalse(segment_plan.status(data,self.d)['stale'])
        original=(self.d/'source/source.mp4').read_bytes()
        (self.d/'source/source.mp4').write_bytes(b'changed source')
        self.assertTrue(segment_plan.status(data,self.d)['stale'])
        (self.d/'source/source.mp4').write_bytes(original)
        store.update(self.pid,lambda d:d.update(data))
        replacement=copy.deepcopy(data['replacements']);replacement['characters'][0]['action']='replace'
        saved=pipeline.save_replacements(self.pid,replacement)
        self.assertTrue(saved['segments_plan']['inputs_sig'].startswith('source-v1:'))
        self.assertFalse(segment_plan.status(saved,self.d)['stale'])

    def test_more_than_four_photos_retain_target_coverage_instead_of_truncating(self):
        data=self.fixture();segment=data['segments_plan']['segments'][0]
        data['replacements']['characters']=[dict(source_id=f'X{i}',action='add',asset_id='father') for i in range(5)]
        data['storyboard']['cells'][0]['characters']=[f'X{i}' for i in range(5)]
        segment['gen_images']=['c01','c02']
        images=segment_plan.generation_images(data,segment,self.d)
        self.assertEqual(len(images),4)
        self.assertEqual(images,['c01','X0','X1','X2'])

    def test_noop_cast_save_keeps_existing_prompt_signature_current(self):
        data=self.fixture()
        with patch.object(gateway,'reason_checked',return_value=(self.fields(data,'d1'),{})):
            saved=director.save(self.pid,dict(action='rewrite',segment_id='d1'))
        original=copy.deepcopy(saved['director']['segments'][0])
        updated=pipeline.save_replacements(self.pid,saved['replacements'])
        self.assertEqual(updated['director']['segments'][0],original)
        self.assertEqual(director.segment_inputs_sig(updated,self.d,'d1'),original['inputs_sig'])
        self.assertFalse(director.view(updated,self.d)['segments'][0]['stale'])

    def test_legacy_director_can_start_again_one_ready_segment_at_a_time(self):
        data=self.fixture()
        data['director']={'format_version':2,'segments':[{'id':'d1','brief':'旧版结构化说明'}]}
        data['segment_boards']['segments']['d2'].update(current=None,versions=[])
        store.update(self.pid,lambda d:d.update(data))
        with patch.object(gateway,'reason_checked',return_value=(self.fields(data,'d1'),{})) as reason:
            saved=director.save(self.pid,dict(action='rewrite',segment_id='d1'))
        reason.assert_called_once()
        self.assertEqual(saved['director']['format_version'],3)
        self.assertFalse(saved['director']['segments'][0]['needs_rewrite'])
        self.assertEqual(saved['director']['segments'][1]['prompt'],'')


if __name__ == '__main__':
    unittest.main()
