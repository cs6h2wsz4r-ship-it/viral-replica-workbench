"""Stable Step04 IDs and Step05 reuse; synthetic projects and fake reasoning only."""
import copy
import unittest
from unittest.mock import patch

from PIL import Image
from console import director, gateway, segment_boards, segment_plan, store, storyboard_edits as edits, storyboard_images
from tools.test_v2_segment_plan import SegmentFixture


class StableSegmentTests(SegmentFixture, unittest.TestCase):
    def install(self, raw=None):
        data=self.fixture()
        data['segments_plan']=segment_plan.validate_plan(raw or self.value(),data,self.d)
        data['segments_plan'].update(inputs_sig=segment_plan.inputs_sig(data,self.d),generated_at='2026-11-01T00:00:00+00:00')
        data['segment_boards']={'segments':segment_boards.build_sources(data,self.d)}
        for segment in data['segments_plan']['segments']:
            sid=segment['id'];state=segment_boards.state_for(data,sid)
            materials=edits.materials_for(data,sid,self.d);materials['C1']=False
            ssig=segment_plan.segment_sig(segment);bsig=edits.board_sig(data,sid)
            plan=dict(summary='替换画面主体',cells=[dict(cell=cid,changes='按照片替换',keep='机位') for cid in segment['cells']],
                      remove_text=True,questions=[],generated_at='2026-11-01T00:00:00+00:00')
            plan['sig']=edits.plan_sig(plan,materials)
            state.update(plan=plan,request='保留背景光线',materials=materials,materials_segment_sig=ssig,materials_photos=edits.photo_fingerprints(data,sid,self.d),
                         segment_sig=ssig,storyboard_sig=bsig,settings_sig=edits.settings_sig(data,sid,self.d,materials),current='v1',
                         versions=[dict(id='v1',image_id='image-'+sid,created_at='2026-11-01T01:00:00+00:00',
                                        file=state['source']['file'],clean_file=state['source']['clean_file'],
                                        cells={c['id']:c['file'] for c in data['storyboard']['cells'] if c['id'] in segment['cells']},
                                        plan_sig=plan['sig'],storyboard_sig=bsig,segment_sig=ssig,slicer=2,layout=state['source']['layout'])])
        return store.update(self.pid,lambda d:d.update(data))

    def rerun(self,value=None):
        with patch.object(gateway,'reason_checked',return_value=(value or self.value(),{})) as reason:
            data=segment_plan.generate(self.pid)
        reason.assert_called_once()
        return data

    def alias(self,data,old,new):
        state=copy.deepcopy(segment_boards.state_for(data,old))
        segment=copy.deepcopy(segment_plan.get_segment(data,old));segment['id']=new
        temporary={**data,'segments_plan':{'segments':[segment]}}
        oldsig=segment_plan.segment_sig(segment_plan.get_segment(data,old));newsig=segment_plan.segment_sig(segment)
        oldboard=edits.board_sig(data,old);newboard=edits.board_sig(temporary,new)
        for item in [state,state['source'],*state['versions']]:
            for key in ('segment_sig','materials_segment_sig'):
                if item.get(key)==oldsig:item[key]=newsig
            if item.get('storyboard_sig')==oldboard:item['storyboard_sig']=newboard
        state['source']['id']=new
        data['segment_boards']['segments'][new]=state
        return segment,state

    def test_model_ids_are_ignored_including_missing_unsafe_and_duplicates(self):
        self.fixture()
        for ids in [('custom_a','custom_b'),('../unsafe','../unsafe'),(None,42)]:
            with self.subTest(ids=ids):
                value=self.value()
                for row,sid in zip(value['segments'],ids):
                    row['id']=sid
                    if sid is None:del row['id']
                data=self.rerun(value)
                self.assertEqual([s['id'] for s in data['segments_plan']['segments']],['d01','d02'])
                self.assertEqual(data['segments_plan']['segments'][0]['cells'],value['segments'][0]['cells'])
        value=self.value();value['segments'][1]['cells'].pop();value['skip_cells']=['c08']
        self.assertEqual(self.rerun(value)['segments_plan']['skip_cells'],['c08'])
        value['segments'][0]['refs']=['missing']
        with self.assertRaisesRegex(ValueError,'参考照片'):self.rerun(value)

    def test_reuse_after_unused_product_edit_preserves_selection_plan_request_switches(self):
        raw=self.value();raw['segments'][0]['id']='previous_a';raw['segments'][1]['id']='previous_b'
        data=self.install(raw);old=copy.deepcopy(segment_boards.state_for(data,'previous_a'))
        store.update(self.pid,lambda d:d['replacements']['product'].update(description='更新说明'))
        self.assertFalse(segment_plan.status(store.load(self.pid),self.d)['stale'])
        expected=edits.warnings(store.load(self.pid),'previous_a',self.d)
        self.assertEqual(expected,[])
        data=self.rerun();state=segment_boards.state_for(data,'d01')
        self.assertEqual(state.get('current'),old['current'])
        for key in ('plan','request','materials','settings_sig'):
            self.assertEqual(state[key],old[key])
        for key in ('file','clean_file','cells','image_id','created_at'):
            self.assertEqual(state['versions'][0][key],old['versions'][0][key])
        self.assertEqual(state['source']['id'],'d01')
        self.assertEqual(edits.materials_for(data,'d01',self.d),old['materials'])
        self.assertEqual(edits.warnings(data,'d01',self.d),expected)
        self.assertTrue(state.get('reused_after_replan'))
        self.assertIn(edits.SETTINGS_WARNING,edits.warnings(data,'d02',self.d))
        director.require_ready(data,self.d,'d01')
        edits.preview(self.pid,'d01',{})

    def test_two_historical_sets_pick_latest_generation_and_keep_selected_older_version(self):
        raw=self.value();raw['segments']=[
            dict(id='d01',cells=['c01','c02','c03'],mode='video',refs=['C1'],gen_images=['c01']),
            dict(id='d02',cells=['c04','c05'],mode='image',refs=['C1','product'],gen_images=['c04','product']),
            dict(id='d03',cells=['c06','c07','c08'],mode='image',refs=['C1','product'],gen_images=['c06','product'])]
        data=self.install(raw);aliases=['d01_fall','d02_watch_demo','d03_rescue'];latest=[]
        for sid,new in zip(('d01','d02','d03'),aliases):
            segment,state=self.alias(data,sid,new);latest.append(segment)
            state['request']='后一套 '+new
            state['versions'].append({**copy.deepcopy(state['versions'][0]),'id':'v2','created_at':'2026-11-02T00:00:00+00:00'})
            # A newer plan edit is not a newer generated board.
            data['segment_boards']['segments'][sid]['plan']['generated_at']='2026-11-03T00:00:00+00:00'
        data['segments_plan']['segments']=latest
        data['runs']={sid:[dict(rev='r1',state='completed',result=sid+'.mp4')] for sid in ['d01',*aliases]}
        data['director']={'format_version':2,'segments':[dict(id=sid,brief='原有提示词') for sid in aliases]}
        store.update(self.pid,lambda d:d.update(data));runs=copy.deepcopy(data['runs']);prompts=copy.deepcopy(data['director'])
        result=self.rerun(raw)
        self.assertEqual(result['runs'],runs);self.assertEqual(result['director'],prompts)
        for sid,new in zip(('d01','d02','d03'),aliases):
            state=segment_boards.state_for(result,sid)
            self.assertEqual(state['request'],'后一套 '+new)
            self.assertEqual(state['current'],'v1');self.assertEqual(len(state['versions']),2)
            self.assertEqual(edits.warnings(result,sid,self.d),[])

    def test_latest_generation_compares_timezone_aware_instants(self):
        data=self.install();_,alias=self.alias(data,'d01','other')
        data['segment_boards']['segments']['d01']['versions'][0]['created_at']='2026-11-02T09:30:00+08:00'
        alias['versions'][0]['created_at']='2026-11-02T02:00:00Z';alias['request']='actually newer'
        store.update(self.pid,lambda d:d.update(data))
        self.assertEqual(segment_boards.state_for(self.rerun(),'d01')['request'],'actually newer')

    def test_changed_content_does_not_reuse_even_at_same_key(self):
        for changed in ('cells','refs','source_start','source_end'):
            with self.subTest(changed=changed):
                data=self.install();value=self.value()
                if changed=='cells':value['segments'][0]['cells'].pop();value['segments'][1]['cells'].insert(0,'c03')
                elif changed=='refs':value['segments'][0]['refs']=['C2']
                elif changed=='source_start':data['storyboard']['cells'][0]['start']=0.1
                else:data['storyboard']['cells'][2]['end']=5.3
                store.update(self.pid,lambda d:d.update(data));result=self.rerun(value)
                state=segment_boards.state_for(result,'d01')
                self.assertFalse(state.get('versions'));self.assertIsNone(segment_boards.current(result,'d01'))
                self.assertFalse(state.get('reused_after_replan'))

    def test_stale_source_plan_and_material_warnings_survive_rebinding(self):
        for changed in ('source','plan','settings','segment'):
            with self.subTest(changed=changed):
                raw=self.value();raw['segments'][0]['id']='history'
                data=self.install(raw);state=segment_boards.state_for(data,'history')
                if changed=='source':data['storyboard']['built_at']='changed'
                elif changed=='plan':state['plan']['sig']='edited-plan'
                elif changed=='settings':data['replacements']['product']['story_checks'][0]['answer']='no'
                else:state['versions'][0]['segment_sig']='different-content'
                store.update(self.pid,lambda d:d.update(data));expected=edits.warnings(data,'history',self.d)
                self.assertTrue(expected)
                result=self.rerun()
                self.assertEqual(set(edits.warnings(result,'d01',self.d)),set(expected))

    def test_changed_frame_bytes_stay_stale_after_source_is_rebuilt(self):
        for old_id in ('d01','old_alias'):
            with self.subTest(old_id=old_id):
                raw=self.value();raw['segments'][0]['id']=old_id
                data=self.install(raw)
                Image.new('RGB',(90,160),'green').save(self.d/data['storyboard']['cells'][0]['file'])
                self.assertIn(edits.SOURCE_WARNING,edits.warnings(data,old_id,self.d))
                result=self.rerun()
                self.assertIn(edits.SOURCE_WARNING,edits.warnings(result,'d01',self.d))
                with self.assertRaises(ValueError):director.require_ready(result,self.d,'d01')
                self.assertTrue(edits.view(result,self.d)['d01']['source_stale'])
                with self.assertRaises(ValueError):edits.preview(self.pid,'d01',{})

    def test_saved_edits_during_reasoning_are_preserved_and_second_rerun_is_stable(self):
        self.install()
        def reply(*args,**kwargs):
            edits.save(self.pid,'d01',{'request':'推理过程中保存的要求'})
            return self.value(),{}
        with patch.object(gateway,'reason_checked',side_effect=reply):
            data=segment_plan.generate(self.pid)
        self.assertEqual(segment_boards.state_for(data,'d01')['request'],'推理过程中保存的要求')
        data=self.rerun()
        self.assertEqual(segment_boards.state_for(data,'d01')['request'],'推理过程中保存的要求')
        self.assertEqual(edits.warnings(data,'d01',self.d),[])
        before=copy.deepcopy(segment_boards.state_for(data,'d01'))
        data=segment_plan.save(self.pid,'d01',{'mode':'image'})
        self.assertEqual(segment_boards.state_for(data,'d01'),before)
        self.assertEqual(edits.warnings(data,'d01',self.d),[])

    def test_matching_uses_snapshot_before_new_keys_overwrite_old_keys(self):
        raw=self.value();raw['segments'][0]['id']='d02';raw['segments'][1]['id']='d01'
        before=self.install(raw);result=self.rerun()
        for old,new in (('d02','d01'),('d01','d02')):
            self.assertEqual(segment_boards.current(result,new)['cells'],segment_boards.current(before,old)['cells'])
            self.assertEqual(edits.warnings(result,new,self.d),[])

    def test_single_segment_adjustment_keeps_every_existing_id(self):
        raw=self.value();raw['segments'][0]['id']='old_alias';raw['segments'][1]['id']='other_alias'
        data=self.install(raw);other=copy.deepcopy(segment_boards.state_for(data,'other_alias'))
        before=copy.deepcopy(segment_boards.state_for(data,'old_alias'))
        saved=segment_plan.save(self.pid,'old_alias',{'mode':'image','id':'ignored'})
        self.assertEqual([s['id'] for s in saved['segments_plan']['segments']],['old_alias','other_alias'])
        self.assertEqual(segment_boards.state_for(saved,'other_alias'),other)
        self.assertEqual(segment_boards.state_for(saved,'old_alias'),before)
        self.assertEqual(edits.warnings(saved,'old_alias',self.d),[])

    def test_prompt_signatures_expire_on_regeneration_and_runs_are_untouched(self):
        data=self.install();sig=director.segment_inputs_sig(data,self.d,'d01')
        runs={'old_alias':[dict(rev='r1',state='completed',result='old.mp4')],'d01':[dict(rev='r2',state='completed')]}
        store.update(self.pid,lambda d:d.update(runs=runs))
        result=self.rerun()
        self.assertNotEqual(director.segment_inputs_sig(result,self.d,'d01'),sig)
        self.assertEqual(result['runs'],runs)
        regenerated=director.segment_inputs_sig(result,self.d,'d02')
        saved=segment_plan.save(self.pid,'d01',{'mode':'image'})
        self.assertEqual(director.segment_inputs_sig(saved,self.d,'d02'),regenerated)

    def test_legacy_prompt_signature_is_unchanged_until_explicit_regeneration(self):
        data=self.install(); before=director.segment_inputs_sig(data,self.d,'d01')
        changed=copy.deepcopy(data);changed['segments_plan']['segments'][0]['gen_images']=['C1','c01']
        self.assertEqual(segment_plan.segment_sig(data['segments_plan']['segments'][0]),segment_plan.segment_sig(changed['segments_plan']['segments'][0]))
        self.assertNotEqual(before,director.segment_inputs_sig(changed,self.d,'d01'))
        self.assertEqual(director.segment_inputs_sig(data,self.d,'d02'),director.segment_inputs_sig(changed,self.d,'d02'))

    def test_reads_and_source_build_do_not_rebind_or_call_models(self):
        data=self.install();before=copy.deepcopy(data)
        with patch.object(gateway,'reason_checked',side_effect=AssertionError('no inference on reads')):
            segment_plan.status(data,self.d);edits.view(data,self.d)
            segment_boards.build_sources(data,self.d)
        self.assertEqual(data,before);self.assertEqual(store.load(self.pid),before)

    def test_reused_version_can_reslice_and_generate_a_cell_under_new_id(self):
        raw=self.value();raw['segments'][0]['id']='old_alias'
        self.install(raw)
        rec=edits.preview(self.pid,'old_alias',{})
        output=self.d/'generated.png'
        with Image.open(rec['storyboard_context']['references'][0]['path']) as image:image.save(output)
        store.update(self.pid,lambda d:d['images'][rec['id']].update(result='generated.png',state='completed'))
        storyboard_images.complete(self.pid,rec,output)
        data=self.rerun();original_record=copy.deepcopy(data['images'][rec['id']])
        data=storyboard_images.reslice(self.pid,'d01','v2')
        self.assertEqual(data['images'][rec['id']],original_record)
        self.assertEqual(edits.warnings(data,'d01',self.d),[])
        self.assertEqual(segment_boards.current(data,'d01')['slicer'],2)
        edits.use_version(self.pid,'d01','v1');edits.use_version(self.pid,'d01','v2')
        with patch.object(gateway,'reason_checked',return_value=({'changes':'按本次要求调整画面','keep':'','why':'已整理这一镜的改法'},{})):
            rec=edits.preview(self.pid,'d01',{'cell_id':'c01','request':'保持机位，修改表情'})
        Image.new('RGB',(90,160),'blue').save(output)
        storyboard_images.complete(self.pid,rec,output)
        data=store.load(self.pid)
        self.assertEqual(segment_boards.current(data,'d01')['id'],'v3')
        self.assertFalse(segment_boards.state_for(data,'d01').get('reused_after_replan'))

    def test_running_board_blocks_regeneration_before_request_and_at_commit(self):
        self.install()
        for state in ('running','query_pending','submitting'):
            with self.subTest(state=state):
                store.update(self.pid,lambda d:d.update(images={'active':dict(state=state,storyboard_context={'segment_id':'d01','sheet_id':'d01'})}))
                before=store.load(self.pid)
                with patch.object(gateway,'reason_checked') as reason,self.assertRaisesRegex(ValueError,'第 04 步（对照和修改）.*新分镜.*生成'):
                    segment_plan.generate(self.pid)
                reason.assert_not_called();self.assertEqual(store.load(self.pid),before)
        store.update(self.pid,lambda d:d.update(images={}))
        before=store.load(self.pid)
        def reply(*args,**kwargs):
            store.update(self.pid,lambda d:d.update(images={'active':dict(state='running',storyboard_context={'segment_id':'d01','sheet_id':'d01'})}))
            return self.value(),{}
        with patch.object(gateway,'reason_checked',side_effect=reply),self.assertRaisesRegex(ValueError,'第 04 步（对照和修改）.*新分镜.*生成'):
            segment_plan.generate(self.pid)
        after=store.load(self.pid)
        for key in ('segments_plan','segment_boards'):
            self.assertEqual(after[key],before[key])


if __name__=='__main__':unittest.main()
