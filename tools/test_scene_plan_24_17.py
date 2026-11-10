"""Scene plan derivation uses synthetic in-memory projects only."""
import copy
import tempfile
import unittest
from pathlib import Path

from console import config

try:
    from console import scene_plan
except ImportError:
    scene_plan = None


def example():
    return {
        'source': {'probe': {'duration': 12.3}},
        'breakdown': {
            'characters': [{'id':'C1','name':'父亲'}, {'id':'C2','name':'儿子'},
                           {'id':'C3','name':'旁白','appearance':'画外角色，不出镜'}],
            'scene': [
                {'id':'S1','name':'门廊','timeRanges':[[0,4.8]]},
                {'id':'S2','name':'产品展示','timeRanges':[[4.8,5.8]]},
                {'id':'S3','name':'佩戴','timeRanges':[[5.8,9]]},
                {'id':'S4','name':'厨房','timeRanges':[[9,12.3]]}],
            'shots': [
                {'id':'K1','start':0,'end':4.8,'characters':['C1'],'product':'无产品'},
                {'id':'K2','start':4.8,'end':5.8,'characters':['C1'],'product':'手环'},
                {'id':'K3','start':5.8,'end':9,'characters':['C1'],'product':'手环'},
                {'id':'K4','start':9,'end':10.58,'characters':['C1'],'product':'手环','description':'正面'},
                {'id':'K5','start':10.58,'end':12.3,'characters':['C1','C2','C3'],'product':'手环'}],
            'productMoments': [{'start':4.8,'end':12.3,'description':'商品清楚'}],
            'overlays': []},
        'replacements': {
            'characters': [{'source_id':'C1','action':'replace','asset_id':'father','description':'老人'},
                           {'source_id':'C2','action':'replace','asset_id':'son'},
                           {'source_id':'C3','action':'keep'}],
            'product': {'action':'replace','asset_id':'watch'}, 'scene_plan': {}},
        'assets': [{'id':aid,'type':'image','file':f'assets/{aid}.png','sha256':aid}
                   for aid in ['father','son','watch','other']], 'images': {}}


class ScenePlanTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(scene_plan, '缺少按场景计算有效设定的模块')
        self.data = example()

    def test_present_subjects_and_default_checkboxes(self):
        casts = scene_plan.scene_cast(self.data)
        self.assertEqual([s['key'] for s in casts['S1']['subjects']], ['C1'])
        self.assertEqual([s['key'] for s in casts['S2']['subjects']], ['C1','product'])
        self.assertEqual([s['key'] for s in casts['S4']['subjects']], ['C1','C2','product'])
        self.assertTrue(all(s['on'] for s in casts['S4']['subjects']))
        self.data['replacements']['characters'][1]['action'] = 'keep'
        self.assertFalse(scene_plan.effective_subjects(self.data,'S4')[1]['on'])

    def test_override_off_add_restore_and_following(self):
        plan = self.data['replacements']['scene_plan']
        plan['S1'] = {'subjects':{'C1':{'on':False}, 'C2':{'on':True,'asset_id':'other'}}, 'added':['C2']}
        rows = scene_plan.effective_subjects(self.data,'S1')
        self.assertEqual([r['key'] for r in rows], ['C1','C2'])
        self.assertFalse(rows[0]['on']); self.assertTrue(rows[1]['added'])
        self.assertTrue(rows[1]['override']); self.assertEqual(rows[1]['asset_id'],'other')
        self.data['replacements']['characters'][1]['asset_id'] = 'father'
        self.assertEqual(scene_plan.effective_subjects(self.data,'S1')[1]['asset_id'],'other')
        del plan['S1']['subjects']['C2']['asset_id']
        self.assertEqual(scene_plan.effective_subjects(self.data,'S1')[1]['asset_id'],'father')
        self.assertFalse(scene_plan.effective_subjects(self.data,'S1')[1]['override'])

    def test_auto_frame_prefers_all_selected_subjects(self):
        self.data['script'] = {'tasks':[{'scene_id':'S4','source_start':9,'source_end':12.3,'mode':'video'}]}
        frame = scene_plan.frame_choice(self.data,'S4')
        self.assertGreater(frame['at'],10.58)
        self.assertLess(frame['at'],12.3)
        self.assertEqual(frame['characters'],['C1','C2'])
        self.assertIn('父亲',frame['present_text']); self.assertIn('儿子',frame['present_text'])
        self.assertIn('10.58',frame['reason'])
        self.assertEqual(scene_plan.frame_choice(self.data,'S4',9.5)['at'],9.5)
        for at in [8,13,float('nan'),float('inf'),'bad']:
            with self.subTest(at=at), self.assertRaisesRegex(ValueError,'时间|秒'):
                scene_plan.frame_choice(self.data,'S4',at)

    def test_analysis_thumbnail_does_not_move_frame_outside_scene_or_coverage(self):
        self.data['script'] = {'tasks':[{'scene_id':'S4','source_start':9,'source_end':12.3,'mode':'video'}]}
        self.data['analysis'] = {'shots':[{'start':0,'end':12.3,'frame_at':6.15,'frame':'analysis/frames/shot-001.jpg'}]}
        picked = scene_plan.frame_choice(self.data,'S4')
        self.assertGreater(picked['at'],10.58)
        self.assertEqual(picked['characters'],['C1','C2'])

    def test_frame_choice_file_matches_the_reported_instant(self):
        self.data['analysis'] = {'shots':[{'start':9,'end':12.3,'frame_at':9.5,
                                         'frame':'analysis/frames/shot-004.jpg'}]}
        choices = scene_plan.frame_choices(self.data,'S4')
        self.assertEqual(choices[0]['file'],'analysis/frames/shot-004.jpg')
        self.assertEqual(choices[0]['at'],9.5)
        self.assertGreater(choices[-1]['at'],10.58)
        self.assertIsNone(choices[-1]['file'], '不能用儿子进画前的缩略图标成父子同框')

    def test_frame_choice_infers_legacy_thumbnail_midpoint(self):
        self.data['analysis'] = {'shots':[{'start':9,'end':12.3,
                                         'frame':'analysis/frames/shot-004.jpg'}]}
        choices = scene_plan.frame_choices(self.data,'S4')
        self.assertIsNone(choices[0]['file'])
        self.assertEqual(choices[-1]['at'],10.65)
        self.assertEqual(choices[-1]['file'],'analysis/frames/shot-004.jpg')

    def test_saved_frame_thumbnail_never_claims_another_time(self):
        self.data['assets'].append({'id':'frame','type':'image','file':'assets/frame.jpg',
                                   'camera_scene':'S4','frame_at':9.5,'scene_frame':True})
        self.data['replacements']['scene_plan']['S4'] = {'frame_at':11.4,'frame_asset_id':'frame'}
        frame = scene_plan.scene_cast(self.data)['S4']['frame']
        self.assertEqual(frame['at'],11.4)
        self.assertIsNone(frame['file'])
        self.assertIsNone(frame['asset_id'])
        self.assertIn('重新选帧',frame['warning'])

    def test_saved_frame_without_time_uses_its_actual_instant(self):
        self.data['assets'].append({'id':'frame','type':'image','file':'assets/frame.jpg',
                                   'camera_scene':'S4','frame_at':11.4,'scene_frame':True})
        self.data['replacements']['scene_plan']['S4'] = {'frame_asset_id':'frame'}
        frame = scene_plan.scene_cast(self.data)['S4']['frame']
        self.assertEqual(frame['at'],11.4)
        self.assertEqual(frame['file'],'assets/frame.jpg')

    def test_enabled_detects_new_asset_prefix_but_not_old_metadata(self):
        self.data['replacements'].pop('scene_plan')
        self.data['assets'].append({'id':'old','for_task':'camera:C1:S1','camera_scene':'S1'})
        self.assertFalse(scene_plan.enabled(self.data))
        self.data['assets'].append({'id':'new','for_task':'scene-camera:S1'})
        self.assertTrue(scene_plan.enabled(self.data))

    def test_product_denials_and_no_evidence_are_not_inferred(self):
        self.data['breakdown']['productMoments'] = []
        for text in ['无','无产品','尚未出现','未出现商品','none','no product','没有手环']:
            self.data['breakdown']['shots'][0]['product'] = text
            with self.subTest(text=text):
                self.assertNotIn('product',[r['key'] for r in scene_plan.effective_subjects(self.data,'S1')])
        self.data['breakdown']['shots'][0]['product'] = '手表'
        self.assertIn('product',[r['key'] for r in scene_plan.effective_subjects(self.data,'S1')])

    def test_bindings_change_only_for_effective_scene_settings(self):
        self.data['replacements']['scene_plan']['S4'] = {'subjects':{'C2':{'asset_id':'other'}}}
        before = {sid:scene_plan.binding(self.data,sid) for sid in ['S1','S4']}
        self.data['replacements']['characters'][1]['asset_id'] = 'father'
        self.assertEqual(before,{sid:scene_plan.binding(self.data,sid) for sid in ['S1','S4']})
        self.data['replacements']['scene_plan']['S4']['subjects']['C2']['on'] = False
        self.assertEqual(before['S1'],scene_plan.binding(self.data,'S1'))
        self.assertNotEqual(before['S4'],scene_plan.binding(self.data,'S4'))
        self.data['replacements']['scene_plan']['S1'] = {'camera_asset':'anything'}
        self.assertEqual(before['S1'],scene_plan.binding(self.data,'S1'))

    def test_unchecked_description_does_not_invalidate_scene(self):
        self.data['replacements']['scene_plan']['S4'] = {'subjects':{'C2':{'on':False}}}
        before = scene_plan.binding(self.data,'S4')
        self.data['replacements']['characters'][1]['description'] = '新说明'
        self.assertEqual(before,scene_plan.binding(self.data,'S4'))

    def test_frame_choices_use_breakdown_thumbnails_and_recorded_reason(self):
        self.data['breakdown']['shots'][-1]['frame'] = 'analysis/frames/shot-005.jpg'
        row = scene_plan.frame_choices(self.data,'S4')[-1]
        self.assertEqual(row['file'],'analysis/frames/shot-005.jpg')
        self.data['assets'].append({'id':'frame','type':'image','file':'assets/frame.jpg',
                                   'camera_scene':'S4','frame_at':11.4,'scene_frame':True,
                                   'scene_frame_reason':'儿子 10.6 秒后才进画面，已选他在画面里的一帧'})
        self.data['replacements']['scene_plan']['S4'] = {'frame_at':11.4,'frame_asset_id':'frame'}
        frame = scene_plan.scene_cast(self.data)['S4']['frame']
        self.assertIn('儿子 10.6',frame['reason'])

    def test_read_view_falls_back_when_saved_frame_is_outside_changed_scene(self):
        self.data['replacements']['scene_plan']['S4'] = {'frame_at':8.5}
        frame = scene_plan.scene_cast(self.data)['S4']['frame']
        self.assertTrue(10.3 <= frame['at'] <= 11.7)
        self.assertIn('原来选的参考帧',frame['warning'])

    def test_validate_frame_and_camera_belong_to_scene(self):
        self.data['assets'].append({'id':'frame','type':'image','file':'assets/f.jpg',
                                   'scene_frame':True,'camera_scene':'S4','frame_at':11.4})
        self.data['assets'].append({'id':'camera','type':'image','file':'assets/c.jpg',
                                   'scene_camera_scene':'S4','scene_camera_binding':'stale'})
        scene_plan.validate_plan(self.data,{'S4':{'frame_at':11.4,'frame_asset_id':'frame','camera_asset':'camera'}})
        for row in [{'frame_at':11.5,'frame_asset_id':'frame'}, {'frame_asset_id':'father'}, {'camera_asset':'camera'}]:
            sid = 'S4' if 'frame_at' in row or row.get('frame_asset_id') == 'father' else 'S1'
            with self.subTest(row=row), self.assertRaisesRegex(ValueError,'参考帧|主镜头图'):
                scene_plan.validate_plan(self.data,{sid:row})

    def test_new_camera_selection_and_staleness(self):
        asset = {'id':'camera','type':'image','file':'assets/camera.png','camera_scene':'S4',
                 'scene_camera_binding':scene_plan.binding(self.data,'S4')}
        self.data['assets'].append(asset)
        self.data['images']['camera'] = {'for_task':'scene-camera:S4','state':'completed','asset_id':'camera'}
        self.assertEqual(scene_plan.camera_image_for(self.data,'S4'),'camera')
        self.data['replacements']['scene_plan']['S4'] = {'subjects':{'C2':{'on':False}}}
        self.assertIsNone(scene_plan.camera_image_for(self.data,'S4'))

    def test_legacy_readonly_migration_requires_present_character(self):
        self.data['replacements'].pop('scene_plan')
        self.assertFalse(scene_plan.enabled(self.data))
        for aid,cid,sid in [('old1','C1','S1'),('wrong','C2','S1'),('old4a','C1','S4'),('old4b','C2','S4')]:
            self.data['assets'].append({'id':aid,'type':'image','file':f'assets/{aid}.png'})
            self.data['images'][aid] = {'state':'completed','asset_id':aid,'for_task':f'camera:{cid}:{sid}'}
        before = copy.deepcopy(self.data)
        self.assertEqual(scene_plan.camera_image_for(self.data,'S1'),'old1')
        self.assertIsNone(scene_plan.camera_image_for(self.data,'S4'))
        self.assertNotIn('wrong',scene_plan.scene_cast(self.data)['S1']['camera_candidates'])
        self.assertIn('旧的单人主镜头图',scene_plan.scene_cast(self.data)['S4']['legacy_notice'])
        self.assertEqual(before,self.data)

    def test_legacy_migration_honors_existing_in_use_version(self):
        self.data['replacements'].pop('scene_plan')
        for aid in ['older-selected','newer-unused']:
            self.data['assets'].append({'id':aid,'type':'image','file':f'assets/{aid}.png'})
            self.data['images'][aid] = {'state':'completed','asset_id':aid,'for_task':'camera:C1:S1'}
        self.data['replacements']['characters'][0]['camera_assets'] = {'S1':'older-selected'}
        self.assertEqual(scene_plan.camera_image_for(self.data,'S1'),'older-selected')
        self.assertEqual(set(scene_plan.scene_cast(self.data)['S1']['camera_candidates']),
                         {'older-selected','newer-unused'})

    def test_validation_rejects_bad_structure_assets_and_times(self):
        scene_plan.validate_plan(self.data, {'S1':{'added':['C2'],'subjects':{'C2':{'on':True,'asset_id':'other'}}}})
        for plan in [[], {'unknown':{}}, {'S1':{'subjects':[]}},
                     {'S1':{'subjects':{'C3':{'on':True}}}},
                     {'S1':{'subjects':{'C1':{'asset_id':'missing'}}}},
                     {'S1':{'frame_at':6}}, {'S1':{'subjects':{'C1':{'on':'yes'}}}}]:
            with self.subTest(plan=plan), self.assertRaisesRegex(ValueError,'场|照片|人物|秒|设定'):
                scene_plan.validate_plan(self.data,plan)

    def test_original_thumbnails_only_use_safe_existing_paths(self):
        with tempfile.TemporaryDirectory(dir=config.ROOT / 'runtime/temp') as folder:
            directory = Path(folder)
            (directory/'analysis/frames').mkdir(parents=True)
            (directory/'analysis/frames/shot-001.jpg').write_bytes(b'fake')
            self.data['analysis'] = {'shots':[{'start':0,'end':4.8,'frame':'analysis/frames/shot-001.jpg'}]}
            row = scene_plan.scene_cast(self.data,directory)['S1']['subjects'][0]
            self.assertEqual(row['original_file'],'analysis/frames/shot-001.jpg')
            self.data['analysis']['shots'][0]['frame'] = '../outside.jpg'
            self.assertNotEqual(scene_plan.scene_cast(self.data,directory)['S1']['subjects'][0]['original_file'],'../outside.jpg')

    def test_original_thumbnail_never_uses_generated_replacement(self):
        self.data['assets'].append({'id':'generated','type':'image','file':'assets/generated.png',
                                   'frame_at':1,'roles':['target_state'],'generated':True,
                                   'scene_camera_scene':'S1'})
        self.assertIsNone(scene_plan.original_file(self.data,'C1'))
        self.data['assets'].append({'id':'source-frame','type':'image','file':'assets/source-frame.jpg',
                                   'frame_at':1,'roles':['scene'],'scene_frame':True,'camera_scene':'S1'})
        self.assertEqual(scene_plan.original_file(self.data,'C1'),'assets/source-frame.jpg')

    def test_original_thumbnail_must_contain_late_entering_character(self):
        self.data['analysis'] = {'shots':[{'start':9,'end':12.3,'frame_at':9.5,
                                         'frame':'analysis/frames/shot-004.jpg'}]}
        self.assertIsNone(scene_plan.original_file(self.data,'C2'))
        self.data['assets'].append({'id':'father-son','type':'image','file':'assets/father-son.jpg',
                                   'frame_at':11.4,'roles':['scene'],'scene_frame':True,'camera_scene':'S4'})
        self.assertEqual(scene_plan.original_file(self.data,'C2'),'assets/father-son.jpg')

    def test_indexed_frame_choice_thumbnail_fallback(self):
        with tempfile.TemporaryDirectory(dir=config.ROOT / 'runtime/temp') as folder:
            directory = Path(folder)
            (directory/'analysis/frames').mkdir(parents=True)
            (directory/'analysis/frames/shot-005.jpg').write_bytes(b'fake')
            self.data['analysis'] = {'shots':[{'start':0,'end':4.8}]}
            choices = scene_plan.frame_choices(self.data,'S4',directory)
            self.assertEqual(choices[-1]['file'],'analysis/frames/shot-005.jpg')


if __name__ == '__main__':
    unittest.main()
