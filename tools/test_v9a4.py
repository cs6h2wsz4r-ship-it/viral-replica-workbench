"""V9a4 offline contracts. All writes use isolated temporary roots; models are stubbed."""
import copy
import io
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from PIL import Image
from tools import test_v9a as base
from console import config, fission, gateway, library, pipeline, store, storyboard_images
from console import storyboard_edits as edits


class V9a4Tests(unittest.TestCase):
    setUpClass = classmethod(base.V9aTests.setUpClass.__func__)
    tearDownClass = classmethod(base.V9aTests.tearDownClass.__func__)
    setUp = base.V9aTests.setUp
    eligible = base.V9aTests.eligible
    create = base.V9aTests.create
    save = base.V9aTests.save
    row = base.V9aTests.row

    def test_ghost_slots_hidden_but_preserved_after_step03_save(self):
        def ghost(data):
            data['breakdown']['characters'] = data['breakdown']['characters'][:1]
            data['replacements']['characters'].append(dict(source_id='C9',action='add',name='新增人物'))
        store.update(self.pid,ghost)
        before=store.load(self.pid); raw=(self.d/'project.json').read_bytes()
        cols=fission.view(self.pid)['columns']
        self.assertEqual([c['key'] for c in cols],['C1','C9','product'])
        self.assertEqual(raw,(self.d/'project.json').read_bytes())
        submitted=copy.deepcopy(before['replacements'])
        submitted['characters']=[c for c in submitted['characters'] if c['source_id']!='C2']
        pipeline.save_replacements(self.pid,submitted)
        self.assertEqual(store.load(self.pid)['replacements']['characters'][1],before['replacements']['characters'][1])

    def test_ghost_record_is_never_normalized_by_step03_save(self):
        ghost=dict(source_id='C99',action='replace',asset_id='historical-photo',description='旧记录')
        store.update(self.pid,lambda d:d['replacements']['characters'].append(copy.deepcopy(ghost)))
        supplied=copy.deepcopy(store.load(self.pid)['replacements'])
        supplied['characters']=[c for c in supplied['characters'] if c['source_id']!='C99']
        pipeline.save_replacements(self.pid,supplied)
        actual=next(c for c in store.load(self.pid)['replacements']['characters'] if c['source_id']=='C99')
        self.assertEqual(json.dumps(actual,ensure_ascii=False).encode(),json.dumps(ghost,ensure_ascii=False).encode())

    def test_ghost_missing_asset_does_not_block_clone_or_change_persisted_record(self):
        ghost=dict(source_id='C99',action='replace',asset_id='historical-photo',description='旧记录')
        store.update(self.pid,lambda d:d['replacements']['characters'].append(copy.deepcopy(ghost)))
        batch,child=self.create()
        self.assertTrue(batch['items'][0]['project_id'])
        self.assertEqual(next(c for c in child['replacements']['characters'] if c['source_id']=='C99'),ghost)

    def test_slot_titles_use_source_role_and_master_status(self):
        def change(data):
            data['breakdown']['characters'][0].update(name='未具名人物',description='原片中的介绍者')
            entry=data['replacements']['characters'][0]
            next(a for a in data['assets'] if a['id']==entry['asset_id'])['label']='照片名字'
        store.update(self.pid,change)
        col=fission.view(self.pid)['columns'][0]
        self.assertEqual(col.get('title'),'人物 · C1 未具名人物')
        self.assertEqual(col.get('source_description'),'原片中的介绍者')
        self.assertEqual(col.get('master_status'),'母版已换成：照片名字')

    def test_stored_ghost_choices_are_filtered_in_memory(self):
        def change(data):
            data['breakdown']['characters']=data['breakdown']['characters'][:1]
            data['fission_draft']={'choices':{'C2':[self.person['id']]},'removed':[]}
        store.update(self.pid,change);before=(self.d/'project.json').read_bytes()
        view=fission.view(self.pid)
        self.assertEqual(view['draft']['choices'],{})
        self.assertEqual((self.d/'project.json').read_bytes(),before)
        with self.assertRaises(ValueError):fission.save_draft(self.pid,{'choices':{'C2':[self.person['id']]}})

    def test_legacy_created_batch_snapshot_ignores_ghost_material(self):
        batch,child=self.create();pid=child['id']
        def ghost(data):
            data['derived_from']['master_snapshot']['materials']['C99']=dict(key='C99',kind='person',name='幽灵',master_name='幽灵')
        store.update(pid,ghost)
        path=config.BATCHES_DIR/(batch['id']+'.json');saved=json.loads(path.read_text())
        for item in saved['items']:item.pop('slots',None)
        store.atomic_write_json(path,saved)
        self.assertNotIn('C99',[slot['key'] for slot in fission.batch_view(batch['id'])['items'][0]['slots']])

    def test_explicit_third_party_retry_only_overrides_opted_in_preview(self):
        from console import jimmy_bridge
        _,child,sid=self.prepared_child();pid=child['id'];selected=child['segments_plan']['segments'][0]['cells'][:2]
        answer=dict(why='调整选中画面',cells=[dict(cell=cid,changes='调整背景颜色',keep='主体与构图') for cid in selected])
        def local_prepare(args,directory):
            job=Path(args[args.index('--job')+1]);folder=Path(args[args.index('--run')+1]);folder.mkdir(parents=True,exist_ok=True)
            store.atomic_write_json(folder/'image-job.preview.json',json.loads(job.read_text()))
            return 'offline prepared'
        with patch.object(jimmy_bridge,'_call',side_effect=local_prepare),patch.object(gateway,'reason_checked',return_value=(answer,{})),patch.object(gateway,'image_preview',wraps=gateway.image_preview) as preview:
            rec=edits.preview(pid,sid,{'cells':selected,'request':'调整背景','channel':'third_party'})
        self.assertEqual(preview.call_args.kwargs.get('override'),'third_party:gpt-image-2')
        self.assertEqual(rec['state'],'pending_confirmation');self.paid.assert_not_called()

    def test_default_names_numbering_short_names_and_explicit_names(self):
        def upload(kind,name,filename,n):
            raw=io.BytesIO();Image.new('RGB',(3,4),(n,12,13)).save(raw,format='PNG')
            return library.upload(kind,name,filename,raw.getvalue())['item']
        upload('person','人物 7','a.png',1)
        self.assertEqual(upload('person','','ChatGPT Image 2026年8月.png',2)['name'],'素材 8')
        self.assertEqual(upload('person','','IMG_1234.jpg',3)['name'],'素材 9')
        self.assertEqual(upload('product','','截屏2026.png',4)['name'],'素材 10')
        self.assertEqual(upload('product','','这是超过十二个字的商品默认名称.png',5)['name'],'素材 11')
        self.assertEqual(upload('person','','短名字.png',6)['name'],'素材 12')
        explicit=upload('person','这是用户明确填写的很长名称','export.png',7)
        self.assertEqual(explicit['name'],'这是用户明确填写的很长名称')
        library.rename(explicit['id'],'ChatGPT Image 用户命名')
        self.assertEqual(library.get(explicit['id'])['name'],'ChatGPT Image 用户命名')

    def prepared_child(self):
        batch,child=self.create();sid=child['segments_plan']['segments'][0]['id']
        answer=dict(summary='调整选中素材',remove_text=False,questions=[],cells=[dict(cell=c,changes='使用新照片',keep='构图') for c in child['segments_plan']['segments'][0]['cells']])
        with patch.object(gateway,'reason_checked',return_value=(answer,{})):
            fission.prepare_segment(child['id'],sid)
        child=store.load(child['id']);rec=next(iter(child['images'].values()))
        directory=store.project_dir(child['id']);output=directory/'synthetic-result.png'
        Image.new('RGB',(300,400),'navy').save(output)
        storyboard_images.complete(child['id'],rec,output)
        store.update(child['id'],lambda d:d['images'][rec['id']].update(state='completed'))
        return batch,store.load(child['id']),sid

    def test_created_snapshot_names_and_orphan_linkage(self):
        batch,child=self.create()
        self.assertEqual(child['name'],self.data['name']+' · 裂变 1')
        item=batch['items'][0]
        self.assertEqual(item.get('project_name'),child['name'])
        self.assertTrue(item.get('slots'));saved=copy.deepcopy(item['slots'])
        library.rename(self.person['id'],'后来改名');library.delete(self.person['id'])
        store.update(self.pid,lambda d:d.update(name='母版后来改名'))
        self.assertEqual(fission.batch_view(batch['id'])['items'][0]['slots'],saved)
        store.update(child['id'],lambda d:[state.update(status='pending_confirmation') for state in d['fission_preparation']['segments'].values()])
        self.assertEqual(fission.view(self.pid).get('recent_batch',{}).get('id'),batch['id'])
        project=next(p for p in store.list_projects() if p['id']==child['id'])
        self.assertEqual(project.get('derived_from',{}).get('project_id'),self.pid)
        self.assertTrue(project.get('master_exists'))
        shutil.rmtree(self.d)
        project=next(p for p in store.list_projects() if p['id']==child['id'])
        self.assertFalse(project.get('master_exists'))

    def test_new_batches_continue_master_numbering_and_keep_batch_row_numbers(self):
        self.eligible();self.save([self.row()])
        first=fission.create(self.pid,{'request_id':'number-batch-one'})['batch']
        self.save([self.row()])
        second=fission.create(self.pid,{'request_id':'number-batch-two'})['batch']
        one,two=first['items'][0],second['items'][0]
        self.assertEqual((one['row'],two['row']),(1,1))
        self.assertEqual((one.get('fission_number'),two.get('fission_number')),(1,2))
        child=store.load(two['project_id'])
        self.assertEqual(child['name'],self.data['name']+' · 裂变 2')
        self.assertEqual(child['derived_from']['row'],1)
        self.assertEqual(child['derived_from'].get('fission_number'),2)
        # Deleting a child cannot recycle a number reserved by its batch.
        shutil.rmtree(store.project_dir(two['project_id']))
        self.save([self.row()])
        third=fission.create(self.pid,{'request_id':'number-batch-three'})['batch']['items'][0]
        self.assertEqual(third.get('fission_number'),3)
        # A surviving child also preserves its number if batch metadata is gone.
        for path in config.BATCHES_DIR.glob('*.json'):path.unlink()
        self.save([self.row()])
        fourth=fission.create(self.pid,{'request_id':'number-batch-four'})['batch']['items'][0]
        self.assertEqual(fourth.get('fission_number'),4)

    def test_stale_tab_cannot_overwrite_draft_or_create_other_tab_selection(self):
        self.eligible();first=fission.view(self.pid)
        self.assertEqual(first['draft'].get('revision'),0)
        saved=fission.save_draft(self.pid,{'choices':{'C1':[self.person['id']]},'revision':0})
        self.assertEqual(saved['draft']['revision'],1)
        with self.assertRaisesRegex(ValueError,'另一个窗口'):
            fission.save_draft(self.pid,{'choices':{},'revision':0})
        with self.assertRaisesRegex(ValueError,'另一个窗口'):
            fission.create(self.pid,{'choices':{'C1':[self.person['id']]},'revision':0,'request_id':'stale-tab'})
        result=fission.create(self.pid,{'choices':{'C1':[self.person['id']]},'revision':1,'request_id':'current-tab'})
        self.assertEqual(result.get('draft_revision'),2)
        repeated=fission.create(self.pid,{'choices':{'C1':[self.person['id']]},'revision':1,'request_id':'current-tab'})
        self.assertEqual(repeated.get('draft_revision'),2)
        self.assertEqual(repeated['batch']['id'],result['batch']['id'])

    def test_create_returns_its_own_revision_when_other_tab_saves_during_scheduling(self):
        self.eligible();self.save([self.row()])
        def other_tab(*args,**kwargs):
            revision=store.load(self.pid)['fission_draft']['revision']
            fission.save_draft(self.pid,{'choices':{'C2':[self.person['id']]},'revision':revision})
        self.scheduled.side_effect=other_tab
        result=fission.create(self.pid,{'choices':{'C1':[self.person['id']]},'revision':1,'request_id':'creation-race'})
        self.assertEqual(result.get('draft_revision'),2)
        self.assertEqual(store.load(self.pid)['fission_draft']['revision'],3)
        with self.assertRaisesRegex(ValueError,'另一个窗口'):
            fission.save_draft(self.pid,{'choices':{},'revision':result['draft_revision']})
        self.assertEqual(store.load(self.pid)['fission_draft']['choices'],{'C2':[self.person['id']]})

    def test_lost_create_response_retry_does_not_borrow_other_tab_revision(self):
        self.eligible();self.save([self.row()])
        body={'choices':{'C1':[self.person['id']]},'revision':1,'request_id':'lost-response-race'}
        original=fission.create(self.pid,body)
        fission.save_draft(self.pid,{'choices':{'C2':[self.person['id']]},'revision':original['draft_revision']})
        repeated=fission.create(self.pid,body)
        self.assertEqual(repeated['draft_revision'],original['draft_revision'])
        self.assertEqual(store.load(self.pid)['fission_draft']['choices'],{'C2':[self.person['id']]})

    def test_generated_fission_not_stale_after_empty_camera_defaults(self):
        _,child,sid=self.prepared_child();pid=child['id'];directory=store.project_dir(pid)
        self.assertFalse(edits.casting_stale(child,sid,directory))
        rep=copy.deepcopy(child['replacements'])
        rep['characters'][0]['camera_assets']={'S1':None,'S2':None}
        pipeline.save_replacements(pid,rep);saved=store.load(pid)
        self.assertFalse(edits.casting_stale(saved,sid,directory),'Empty scene-photo defaults are not a casting edit')
        rep=copy.deepcopy(saved['replacements']);rep['characters'][0]['description']='实际改过说明'
        pipeline.save_replacements(pid,rep)
        self.assertTrue(edits.casting_stale(store.load(pid),sid,directory))

    def test_only_real_photo_edits_stale_and_master_later_edits_do_not(self):
        _,child,sid=self.prepared_child();pid=child['id'];directory=store.project_dir(pid)
        before=edits.settings_sig(child,sid,directory,edits.materials_for(child,sid,directory))
        def master_edit(data):
            data['replacements']['characters'][0]['description']='母版后改'
            data['segment_boards']['segments'][sid]['plan']['summary']='母版后改新分镜'
        store.update(self.pid,master_edit)
        now=store.load(pid)
        self.assertEqual(before,edits.settings_sig(now,sid,directory,edits.materials_for(now,sid,directory)))
        self.assertFalse(edits.casting_stale(now,sid,directory))
        asset=next(a for a in now['assets'] if a['id']==now['replacements']['characters'][0]['asset_id'])
        Image.new('RGB',(22,35),'purple').save(directory/asset['file'])
        self.assertTrue(edits.casting_stale(store.load(pid),sid,directory))

    def test_single_request_bytes_match_frozen_v9a3_module(self):
        baseline=next((parent/'before-snapshot/console/storyboard_edits.py' for parent in Path(__file__).resolve().parents
                       if (parent/'before-snapshot/console/storyboard_edits.py').is_file()),None)
        if baseline is None:self.skipTest('Run under the V9a4 guarded snapshot to compare frozen request bytes')
        spec=importlib.util.spec_from_file_location('console._v9a4_before_edits',baseline)
        old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        _,child,sid=self.prepared_child();pid=child['id'];directory=store.project_dir(pid);cid=child['segments_plan']['segments'][0]['cells'][0]
        body={'cell_id':cid,'request':'调整这一镜的背景'}
        self.assertEqual(json.dumps(edits.cell_revision_messages(child,sid,body,directory),ensure_ascii=False,separators=(',',':')).encode(),
                         json.dumps(old.cell_revision_messages(child,sid,body,directory),ensure_ascii=False,separators=(',',':')).encode())
        refs=[dict(key='current',name='当前版本的这一格',path='frame.png')]+edits.material_options(child,sid,directory)
        self.assertEqual(edits.image_prompt(child,sid,refs,cid,dict(changes='调整背景',keep='主体')),old.image_prompt(child,sid,refs,cid,dict(changes='调整背景',keep='主体')))
        answer=dict(changes='调整背景',keep='主体',why='调整这一镜')
        captured=[]
        original=store.load(pid)
        for payload in (body,{'cells':[cid],'request':body['request']}):
            store.atomic_write_json(directory/'project.json',original)
            with patch.object(gateway,'reason_checked',return_value=(answer,{})) as reason:
                rec=edits.preview(pid,sid,payload)
            captured.append((json.dumps(reason.call_args.args[1],ensure_ascii=False,separators=(',',':')).encode(),rec['preview']['prompt'].encode(),
                             [hashlib.sha256(Path(ref['path']).read_bytes()).hexdigest() for ref in rec['storyboard_context']['references']]))
        self.assertEqual(captured[0],captured[1])

    def test_multiple_cells_one_reason_one_preview_only_selected_bytes_change(self):
        _,child,sid=self.prepared_child();pid=child['id'];directory=store.project_dir(pid)
        state=edits.state_for(child,sid);current=next(v for v in state['versions'] if v['id']==state['current'])
        selected=list(current['cells'])[:2]
        before={cid:(directory/file).read_bytes() for cid,file in current['cells'].items()}
        answer=dict(why='调整选中画面',cells=[dict(cell=cid,changes='调整背景颜色',keep='主体与构图') for cid in selected])
        with patch.object(gateway,'reason_checked',return_value=(answer,{})) as reason, patch.object(gateway,'image_preview',wraps=gateway.image_preview) as preview:
            rec=edits.preview(pid,sid,{'cells':selected,'request':'调整选中的背景'})
        self.assertEqual(reason.call_count,1);self.assertEqual(preview.call_count,1)
        self.assertEqual(rec['storyboard_context'].get('selected_cells'),selected)
        self.assertEqual(rec['state'],'pending_confirmation')
        edits.verify_preview(pid,rec)
        output=directory/'multi-result.png';Image.new('RGB',(300,400),'red').save(output)
        storyboard_images.complete(pid,rec,output)
        latest=edits.state_for(store.load(pid),sid)['versions'][-1]
        for cid,file in latest['cells'].items():
            if cid in selected:self.assertNotEqual((directory/file).read_bytes(),before[cid])
            else:
                self.assertEqual((directory/file).read_bytes(),before[cid])
                with Image.open(directory/file) as a,Image.open(io.BytesIO(before[cid])) as b:self.assertEqual(a.tobytes(),b.tobytes())
        self.paid.assert_not_called()

    def test_multicell_cancel_then_reprepare_supersedes_unpaid_preview_and_photo_edits_invalidate(self):
        _,child,sid=self.prepared_child();pid=child['id'];selected=child['segments_plan']['segments'][0]['cells'][:2]
        answer=dict(why='调整画面',cells=[dict(cell=cid,changes='调整背景',keep='主体') for cid in selected])
        with patch.object(gateway,'reason_checked',return_value=(answer,{})):
            rec=edits.preview(pid,sid,{'cells':selected,'request':'调整背景'})
        with patch.object(gateway,'reason_checked',return_value=(answer,{})):
            newer=edits.preview(pid,sid,{'cells':selected,'request':'再改背景'})
        with self.assertRaises(ValueError):edits.verify_preview(pid,rec)
        edits.verify_preview(pid,newer)
        rep=copy.deepcopy(store.load(pid)['replacements']);rep['characters'][0]['description']='另一个标签改了说明'
        pipeline.save_replacements(pid,rep)
        with self.assertRaises(ValueError):edits.verify_preview(pid,newer)

    def test_created_view_distinguishes_paid_generating_failed_and_completed(self):
        batch,child=self.create();pid=child['id']
        def ready(data):
            for sid,state in data['fission_preparation']['segments'].items():
                state.update(status='pending_confirmation',image_id=sid)
                data['images'][sid]={'id':sid,'state':'completed'}
        store.update(pid,ready)
        sid=child['segments_plan']['segments'][0]['id']
        store.update(pid,lambda d:d['images'][sid].update(state='running'))
        self.assertEqual(fission.batch_view(batch['id'])['items'][0]['status'],'generating')
        self.assertIsNone(fission.view(self.pid)['recent_batch'])
        store.update(pid,lambda d:d['images'][sid].update(state='failed',error='offline failure'))
        item=fission.batch_view(batch['id'])['items'][0]
        self.assertEqual(item['status'],'image_failed');self.assertIn('offline failure',item['error']);self.assertFalse(item['can_retry'])
        store.update(pid,lambda d:d['images'][sid].update(state='completed'))
        self.assertEqual(fission.batch_view(batch['id'])['items'][0]['status'],'completed')

    def test_single_preview_after_multicell_invalidates_old_confirmation(self):
        _,child,sid=self.prepared_child();pid=child['id'];selected=child['segments_plan']['segments'][0]['cells'][:2]
        answer=dict(why='调整画面',cells=[dict(cell=cid,changes='调整背景',keep='主体') for cid in selected])
        with patch.object(gateway,'reason_checked',return_value=(answer,{})):
            rec=edits.preview(pid,sid,{'cells':selected,'request':'调整背景'})
        with patch.object(gateway,'reason_checked',return_value=(dict(changes='调整光线',keep='主体',why='调整光线'),{})):
            newer=edits.preview(pid,sid,{'cell_id':selected[0],'request':'改成调整光线'})
        with self.assertRaises(ValueError):edits.verify_preview(pid,rec)
        edits.verify_preview(pid,newer)

    def test_concurrent_single_revision_cannot_be_overwritten_by_multicell_reasoning(self):
        _,child,sid=self.prepared_child();pid=child['id'];selected=child['segments_plan']['segments'][0]['cells'][:2]
        answer=dict(why='旧的多选改法',cells=[dict(cell=cid,changes='旧的多选背景',keep='主体') for cid in selected])
        def concurrent_single(*args,**kwargs):
            with patch.object(gateway,'reason_checked',return_value=(dict(changes='另一个标签的新改法',keep='主体',why='更新单镜'),{})):
                edits.revise_cell(pid,sid,{'cell_id':selected[1],'request':'另一个标签更新单镜'})
            return answer,{}
        with patch.object(gateway,'reason_checked',side_effect=concurrent_single),patch.object(gateway,'image_preview',wraps=gateway.image_preview) as image:
            with self.assertRaisesRegex(ValueError,'刚刚改过'):
                edits.preview(pid,sid,{'cells':selected,'request':'旧的多选背景'})
        image.assert_not_called()
        saved=edits.state_for(store.load(pid),sid)['cell_revisions'][selected[1]]
        self.assertEqual(saved['changes'],'另一个标签的新改法')

    def test_multicell_rejects_cross_segment_skip_and_busy(self):
        _,child,sid=self.prepared_child();pid=child['id'];selected=child['segments_plan']['segments'][0]['cells'][:2]
        other=child['segments_plan']['segments'][1]['cells'][0]
        with patch.object(gateway,'reason_checked',side_effect=AssertionError('invalid selection reached model')):
            with self.assertRaises(ValueError):edits.preview(pid,sid,{'cells':[selected[0],other],'request':'改背景'})
            store.update(pid,lambda d:d['segments_plan'].update(skip_cells=[selected[0]]))
            with self.assertRaises(ValueError):edits.preview(pid,sid,{'cells':selected,'request':'改背景'})
            def busy(data):
                data['segments_plan']['skip_cells']=[]
                data['images']['busy']={'id':'busy','state':'running','storyboard_context':{'sheet_id':sid}}
            store.update(pid,busy)
            with self.assertRaises(ValueError):edits.preview(pid,sid,{'cells':selected,'request':'改背景'})


if __name__=='__main__':unittest.main()
