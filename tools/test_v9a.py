"""V9a offline contracts. Real master is copied read-only; every mutation is temporary."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image
from console import config, cast, final_preview, gateway, store, segment_boards, storyboard_edits as edits

try:
    from console import library, fission
except ImportError:
    library = fission = None


class V9aTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp')
        cls.seed = Path(cls.base.name) / 'readonly-master'
        shutil.copytree(ROOT / 'projects/p1106203907-b320', cls.seed)
        for file in cls.seed.rglob('*'):
            if file.is_file(): file.chmod(0o444)

    @classmethod
    def tearDownClass(cls):
        cls.base.cleanup()

    def setUp(self):
        self.assertIsNotNone(fission, 'V9a modules must exist')
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.tmp / 'projects'))
        self.enterContext(patch.object(config, 'LIBRARY_DIR', self.tmp / 'library'))
        self.enterContext(patch.object(config, 'BATCHES_DIR', self.tmp / 'batches'))
        self.enterContext(patch.object(config, 'load', return_value={**config.DEFAULTS, 'OPENAI_API_KEY': 'mock-only'}))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('no secrets')))
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('no network')))
        self.paid = self.enterContext(patch.object(gateway, 'image_confirm', side_effect=AssertionError('no paid calls')))
        self.pid = 'master'; self.d = config.PROJECTS_DIR / self.pid
        shutil.copytree(self.seed, self.d)
        for file in self.d.rglob('*'):
            if file.is_file(): file.chmod(0o644)
        self.data = json.loads((self.d / 'project.json').read_text())
        self.data['id'] = self.pid
        store.atomic_write_json(self.d / 'project.json', self.data)
        self.photo = self.tmp / 'new-person.png'; Image.new('RGB', (22, 35), 'green').save(self.photo)
        self.product = self.tmp / 'new-product.png'; Image.new('RGB', (22, 35), 'blue').save(self.product)
        self.person = library.upload('person', '新人物', self.photo.name, self.photo.read_bytes())['item']
        self.item = library.upload('product', '新商品', self.product.name, self.product.read_bytes())['item']
        self.scheduled = self.enterContext(patch.object(fission, 'schedule_prepare'))

    def eligible(self):
        data = store.load(self.pid)
        data['director'] = {'format_version': 3, 'segments': []}
        data['runs'] = {}
        for segment in data['segments_plan']['segments']:
            sid = segment['id']; rel = f'clips/{sid}.mp4'; (self.d / rel).parent.mkdir(exist_ok=True)
            (self.d / rel).write_bytes(b'completed synthetic clip')
            data['director']['segments'].append({**segment, 'selected_run': 'r1'})
            data['runs'][sid] = [{'rev': 'r1', 'state': 'completed', 'result': rel}]
            state = data['segment_boards']['segments'][sid]
            if not segment_boards.current(data, sid):
                cells = {cid: next(c['file'] for c in data['storyboard']['cells'] if c['id'] == cid) for cid in segment['cells']}
                state.update(current='v1', versions=[{'id':'v1', 'file':state['source']['file'], 'clean_file':state['source']['clean_file'], 'cells':cells, 'slicer':2}])
        store.atomic_write_json(self.d / 'project.json', data)
        return data

    def row(self, rid='row1', product=False):
        return {'id':rid, 'selections':{'C1':self.person['id'], 'product':self.item['id'] if product else None}}

    def save(self, rows):
        choices={}
        for row in rows:
            for key,lid in row['selections'].items():
                if lid and lid not in choices.setdefault(key,[]):choices[key].append(lid)
        return fission.save_draft(self.pid, {'choices':choices, 'removed':[]})

    def create(self, row=None):
        self.eligible(); self.save([row or self.row()])
        result=fission.create(self.pid, {})
        return result['batch'], store.load(result['batch']['items'][0]['project_id'])

    def test_library_crud_dedupe_and_project_import(self):
        same=library.upload('person','其他名字','p.png',self.photo.read_bytes())
        self.assertTrue(same['duplicate']); self.assertEqual(same['item']['id'], self.person['id'])
        library.rename(self.person['id'], '新名字')
        self.assertEqual(library.get(self.person['id'])['name'], '新名字')
        saved=library.from_project(self.pid, {'kind':'person','source_id':'C1'})
        self.assertEqual(saved['item']['description'], self.data['replacements']['characters'][0]['description'])
        self.assertEqual(saved['item']['from']['project_id'],self.pid)
        self.assertTrue(library.from_project(self.pid,{'kind':'person','source_id':'C1'})['duplicate'])
        library.delete(self.person['id'])
        with self.assertRaises(ValueError):library.get(self.person['id'])
        self.assertNotIn('groups',library.list_all())

    def test_library_rejects_non_image_and_wrong_kind(self):
        with self.assertRaises(ValueError):library.upload('person','bad','bad.png',b'not an image')
        with self.assertRaises(ValueError):library.upload('group','bad','p.png',self.photo.read_bytes())

    def test_eligibility_is_final_selections_and_reads_never_write(self):
        before=(self.d/'project.json').read_bytes()
        expected=final_preview.selections(self.data,self.d)[2]
        view=fission.view(self.pid)
        self.assertEqual(view['warnings'], expected)
        self.assertEqual(view['ready'],not expected)
        self.assertEqual((self.d/'project.json').read_bytes(),before)
        self.eligible(); self.assertTrue(fission.view(self.pid)['ready'])
        self.assertEqual([c['key'] for c in view['columns']],['C1','C2','product'])
        self.assertTrue(view['kept'])

    def test_choices_omit_all_master_dedupe_and_block_unanswered(self):
        self.eligible()
        view=self.save([{'id':'r','selections':{}}])
        self.assertEqual(view['rows'],[]);self.assertEqual(view['row_errors'],[])
        with self.assertRaises(ValueError):fission.create(self.pid,{})
        self.assertEqual(len(self.save([self.row(),self.row('row2')])['rows']),1)
        self.assertTrue(self.save([self.row(product=True)])['row_errors'])
        with self.assertRaises(ValueError):fission.create(self.pid,{})
        self.save([self.row()]);self.assertEqual(fission.view(self.pid)['rows'][0]['selections']['C1'],self.person['id'])

    def test_story_checks_generated_once_cached_answered_and_signed(self):
        self.eligible(); row=self.row(product=True); self.save([row])
        checks={'checks':[dict(id='k1',feature='功能',where='剧情段落',question='是否支持此功能？')]}
        original=copy.deepcopy(store.load(self.pid)['replacements'])
        with patch.object(gateway,'reason_checked',return_value=(checks,{})) as reason:
            fission.check_story(self.pid,{'library_id':self.item['id']})
            fission.answer_story(self.pid,{'library_id':self.item['id'],'answers':[{'id':'k1','answer':'unsure','note':'待确认'}]})
            self.save([self.row('row2',product=True)])
            result=fission.check_story(self.pid,{'library_id':self.item['id']})
            self.assertEqual(reason.call_count,1)
            self.assertEqual(result['rows'][0]['story_check']['checks'][0]['answer'],'unsure')
        self.assertEqual(store.load(self.pid)['replacements'],original)
        batch=fission.create(self.pid,{})['batch']
        child=store.load(batch['items'][0]['project_id']); directory=store.project_dir(child['id'])
        self.assertFalse(cast.story_stale(child,directory))
        self.assertEqual(child['replacements']['product']['story_checks'][0]['note'],'待确认')

    def test_empty_checks_complete_but_forged_checks_do_not(self):
        row=self.row(product=True);row['story_check']={'status':'ready','checks':[],'sha256':self.item['sha256']}
        self.assertTrue(self.save([row])['row_errors'])
        with patch.object(gateway,'reason_checked',return_value=({'checks':[]},{})):
            view=fission.check_story(self.pid,{'library_id':self.item['id']})
        self.assertFalse(view['row_errors'])

    def test_child_copy_allowlist_assets_and_snapshot_independence(self):
        batch,child=self.create(); d=store.project_dir(child['id'])
        for key in ('source','analysis','breakdown','breakdown_versions','breakdown_provider','storyboard','segments_plan'):
            self.assertEqual(child[key],store.load(self.pid)[key])
        for key in ('director','usage','log','final_preview','fission_draft'):
            self.assertNotIn(key,child)
        self.assertEqual(child['runs'],{});self.assertEqual(child['images'],{})
        for state in child['segment_boards']['segments'].values():self.assertFalse(state.get('versions'))
        for rel in ('source/source.mp4','analysis/台词表.转写原句.srt','analysis/台词表.srt'):
            self.assertEqual((d/rel).read_bytes(),(self.d/rel).read_bytes())
        for a in child['assets']:self.assertTrue((d/a['file']).is_file())
        self.assertEqual(cast._photo(child,child['replacements']['characters'][0],d).read_bytes(),self.photo.read_bytes())
        frozen=child['derived_from']['master_snapshot']['boards']['d01']
        old=segment_boards.current(store.load(self.pid),'d01')
        self.assertEqual((d/frozen['clean_file']).read_bytes(),(self.d/old['clean_file']).read_bytes())
        (self.d/old['clean_file']).write_bytes(b'changed master')
        self.assertNotEqual((d/frozen['clean_file']).read_bytes(),(self.d/old['clean_file']).read_bytes())
        library.delete(self.person['id']); self.assertTrue(cast._photo(child,child['replacements']['characters'][0],d).is_file())
        self.assertTrue((config.BATCHES_DIR/(batch['id']+'.json')).is_file())

    def test_per_row_atomic_failure_preserves_other_row(self):
        self.eligible();other=library.upload('person','other','p.png',self.product.read_bytes())['item']
        rows=[self.row(),{'id':'row2','selections':{'C1':other['id']}}];self.save(rows)
        original=fission.clone_row
        def fail(master,row,index,*args,**kwargs):
            if index==1: raise OSError('synthetic failure')
            return original(master,row,index,*args,**kwargs)
        with patch.object(fission,'clone_row',side_effect=fail):batch=fission.create(self.pid,{})['batch']
        self.assertIsNone(batch['items'][0]['project_id']); self.assertIn('synthetic failure',batch['items'][0]['error'])
        self.assertTrue(batch['items'][1]['project_id'])
        self.assertEqual(len(list(config.PROJECTS_DIR.glob('*/project.json'))),2)
        self.assertFalse(list(config.PROJECTS_DIR.glob('.fission-*')))

    def test_fission_plan_and_pending_preview_use_snapshot(self):
        batch,child=self.create();pid=child['id'];d=store.project_dir(pid);sid='d01'
        messages=edits.plan_messages(child,sid,'',edits.materials_for(child,sid,d),d)
        text=json.dumps(messages,ensure_ascii=False)
        self.assertEqual(messages[0]['content'].split('\n本段')[0], fission.FISSION_PLAN_SYSTEM)
        for term in ('换掉的项','沿用的项','母版改法','frame_at','剧情检查'):self.assertIn(term,text)
        answer={'summary':'仅替换人物','remove_text':False,'questions':[], 'cells':[{'cell':cid,'changes':'换人物','keep':'构图'} for cid in child['segments_plan']['segments'][0]['cells']]}
        with patch.object(gateway,'reason_checked',return_value=(answer,{})) as reason:
            fission.prepare_segment(pid,sid)
        child=store.load(pid); images=list(child['images'].values()); self.assertEqual(len(images),1)
        rec=images[0];self.assertEqual(rec['state'],'pending_confirmation')
        self.assertEqual(rec['storyboard_context']['references'][0]['key'],'source')
        self.assertIn('母版',rec['storyboard_context']['references'][0]['name'])
        self.assertIn('already-replaced',rec['preview']['prompt']);self.assertNotIn('原片拼图',rec['preview']['prompt'])
        reason.assert_called_once();self.assertEqual(reason.call_args.args[0],'storyboard_plan')
        self.paid.assert_not_called()
        self.assertEqual(fission.batch_view(batch['id'])['items'][0]['segments'][0]['status'],'pending_confirmation')

    def test_preparation_failure_is_retryable(self):
        _,child=self.create();sid='d01';pid=child['id']
        with patch.object(edits,'generate_plan',side_effect=ValueError('stub plan failure')): fission.prepare_segment(pid,sid)
        self.assertEqual(store.load(pid)['fission_preparation']['segments'][sid]['status'],'failed')
        fission.retry(pid,{'segment_id':sid});self.assertGreater(self.scheduled.call_count,1)

    def test_non_fission_request_bytes_match_before_snapshot(self):
        # Captured from runtime/temp/v9a-work/before-snapshot, before any V9a edits.
        expected={
            'd01':('64038609f1af710ded4e6e1342669b1f34d16a7d6ea7be5ca9a28076d20f093a','bbd8f32f1e16ddb9080c04da4cb5e3e07c0e0d9e7434f7ea0c3fb5c6a03fbd8e'),
            'd02':('a63a45fd5bbb139583ca17356f921657f17cc52f227c2294d68c40eff1299e11','67fec98de0f04201ef95e69a97de5c41e042cbe24a38bebc25e667f71da06c17'),
            'd03':('f7fd4e8d7acfedacae644ee5f0fd50dbea48b8b41c8ceeb0116de7f79d79e091','d39584ae64690bcce3d4ee88af8aaac5e370e19b5545bb94ca8a211fa56f9223')}
        with patch.object(edits.llm,'image_part',side_effect=lambda path:[{'type':'fixture_image','path':str(Path(path).relative_to(self.d))}]):
            for sid,(plan_hash,image_hash) in expected.items():
                materials=edits.materials_for(self.data,sid,self.d)
                messages=edits.plan_messages(self.data,sid,'',materials,self.d)
                refs=[{'key':'source','name':'不带格号的原片拼图（已补白）','path':'frozen.png'}]+[{**m,'path':m['file']} for m in edits.material_options(self.data,sid,self.d) if materials.get(m['key'])]
                prompt=edits.image_prompt(self.data,sid,refs)
                self.assertEqual(hashlib.sha256(json.dumps(messages,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),plan_hash)
                self.assertEqual(hashlib.sha256(prompt.encode()).hexdigest(),image_hash)

    def test_import_dedup_refreshes_recognized_description_and_look(self):
        entry=self.data['replacements']['characters'][0];photo=cast._photo(self.data,entry,self.d)
        old=library.upload('person','same-photo',photo.name,photo.read_bytes())['item']
        store.update(self.pid,lambda d:d['replacements']['characters'][0].update(description='已识别描述',look={'hair':'短发'}))
        result=library.from_project(self.pid,{'kind':'person','source_id':'C1'})
        self.assertTrue(result['duplicate']);self.assertEqual(result['item']['id'],old['id'])
        self.assertEqual(result['item']['description'],'已识别描述');self.assertEqual(result['item']['look'],{'hair':'短发'})

    def test_library_concurrent_upload_is_unique_and_same_master_photo_not_change(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as pool:
            outputs=list(pool.map(lambda n:library.upload('person','x','x.png',self.photo.read_bytes()),range(8)))
        self.assertEqual(len({o['item']['id'] for o in outputs}),1)
        same=library.from_project(self.pid,{'kind':'person','source_id':'C1'})['item']
        result=self.save([{'id':'r','selections':{'C1':same['id']}}])
        self.assertEqual(result['rows'][0]['selections']['C1'],same['id']);self.assertIn('母版正在用',result['row_errors'][0]['error'])

    def test_story_cache_invalidated_by_breakdown_and_ignores_forged_answers(self):
        self.save([self.row(product=True)])
        with patch.object(gateway,'reason_checked',return_value=({'checks':[]},{})) as reason:
            fission.check_story(self.pid,{'library_id':self.item['id']})
            store.update(self.pid,lambda d:d['breakdown'].update(dialogue=[{'text':'changed plot'}]))
            self.assertTrue(fission.view(self.pid)['row_errors'])
            fission.check_story(self.pid,{'library_id':self.item['id']});self.assertEqual(reason.call_count,2)

    def test_actual_copy_failure_cleans_staging(self):
        self.eligible();self.save([self.row()]);master=store.load(self.pid)
        old=fission._copy;count=0
        def fail(*args,**kwargs):
            nonlocal count
            count+=1
            if count==4:raise OSError('disk failure')
            return old(*args,**kwargs)
        with patch.object(fission,'_copy',side_effect=fail):
            with self.assertRaisesRegex(OSError,'disk failure'):fission.clone_row(master,self.row(),1)
        self.assertFalse(list(config.PROJECTS_DIR.glob('.fission-*')))
        self.assertEqual(len(list(config.PROJECTS_DIR.glob('*/project.json'))),1)

    def test_interrupted_progress_is_read_only_retryable(self):
        batch,child=self.create();path=store.project_dir(child['id'])/'project.json';before=path.read_bytes()
        view=fission.batch_view(batch['id']);item=view['items'][0]
        self.assertEqual(item['status'],'failed');self.assertTrue(item['can_retry']);self.assertIn('中断',item['error'])
        self.assertEqual(path.read_bytes(),before)
        fission.retry(child['id'],{})

    def test_repeated_submission_is_idempotent_and_records_selections(self):
        self.eligible();body={'request_id':'same-request'};self.save([self.row()])
        one=fission.create(self.pid,body)['batch'];two=fission.create(self.pid,body)['batch']
        self.assertEqual(one['id'],two['id']);self.assertEqual(one['items'][0]['selections'],{**self.row()['selections'],'C2':None})
        self.assertEqual(len(list(config.PROJECTS_DIR.glob('*/project.json'))),2)

    def test_concurrency_limit_two_and_stops_at_unpaid_preview(self):
        from concurrent.futures import ThreadPoolExecutor
        _,child=self.create();pid=child['id'];segments=[s['id'] for s in child['segments_plan']['segments']]
        lock=threading.Lock();active=peak=0
        def reason(*args,**kwargs):
            nonlocal active,peak
            with lock:active+=1;peak=max(peak,active)
            time.sleep(.04)
            with lock:active-=1
        with patch.object(edits,'generate_plan',side_effect=reason),patch.object(edits,'preview',return_value={'id':'unpaid','state':'pending_confirmation'}):
            with ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(lambda sid:fission.prepare_segment(pid,sid),segments))
        self.assertEqual(peak,2);self.paid.assert_not_called()
        with self.assertRaisesRegex(ValueError,'已准备好'):fission.retry(pid,{'segment_id':segments[0]})

    def test_dynamic_changes_and_snapshot_hash_invalidation(self):
        _,child=self.create();pid=child['id'];d=store.project_dir(pid);sid='d01'
        self.assertNotIn('product',fission.changes(child,d))
        asset=dict(id='updated-product',type='image',file='assets/new-product.png',label='后来换的商品')
        shutil.copyfile(self.product,d/asset['file'])
        def change(data):
            data['assets'].append(asset);data['replacements']['product']['asset_id']=asset['id']
        child=store.update(pid,change)
        self.assertIn('product',fission.changes(child,d))
        material=edits.materials_for(child,sid,d);before=edits.settings_sig(child,sid,d,material)
        snap=fission.snapshot(child,sid);(d/snap['cells']['c01']).write_bytes(b'tampered snapshot')
        self.assertNotEqual(before,edits.settings_sig(child,sid,d,material))

    def test_product_absent_segment_does_not_send_product_reference(self):
        _,child=self.create();sid='d01';d=store.project_dir(child['id'])
        cells=set(child['segments_plan']['segments'][0]['cells'])
        for cell in child['storyboard']['cells']:
            if cell['id'] in cells:cell.update(product='',product_later=False)
        keys=[m['key'] for m in edits.material_options(child,sid,d)]
        self.assertNotIn('product',keys)

    def test_added_master_role_is_carried_into_fission_segment_references(self):
        self.eligible()
        def add(data):
            original=copy.deepcopy(data['replacements']['characters'][0]);original.update(source_id='N1',name='新增角色',action='add')
            data['replacements']['characters'].append(original)
            data['segment_boards']['segments']['d01'].setdefault('materials',{})['N1']=True
        store.update(self.pid,add)
        row={'id':'added-row','selections':{'N1':self.person['id']}};self.save([row])
        result=fission.create(self.pid,{})['batch'];child=store.load(result['items'][0]['project_id']);directory=store.project_dir(child['id'])
        photos=edits.material_options(child,'d01',directory)
        self.assertIn('N1',[m['key'] for m in photos])
        self.assertNotIn('N1',[m['key'] for m in edits.material_options(child,'d02',directory)])
        text=json.dumps(edits.plan_messages(child,'d01','',edits.materials_for(child,'d01',directory),directory),ensure_ascii=False)
        self.assertIn('新增角色',text);self.assertIn('N1',text)

    def test_snapshot_old_photo_change_during_reasoning_rejects_commit(self):
        _,child=self.create();pid=child['id'];sid='d01';directory=store.project_dir(pid)
        answer={'summary':'替换人物','remove_text':False,'questions':[], 'cells':[{'cell':cid,'changes':'换人物','keep':'构图'} for cid in child['segments_plan']['segments'][0]['cells']]}
        def mutate(*args,**kwargs):
            relative=child['derived_from']['master_snapshot']['materials']['C1']['file']
            (directory/relative).write_bytes(b'mutated old photo')
            return answer,{}
        with patch.object(gateway,'reason_checked',side_effect=mutate):
            with self.assertRaisesRegex(ValueError,'改过'):edits.generate_plan(pid,sid,{})
        self.assertNotIn('plan',edits.state_for(store.load(pid),sid))

    def test_explicit_answers_cannot_complete_unknown_checks(self):
        self.save([self.row(product=True)])
        checks={'checks':[dict(id='k1',feature='功能',where='剧情段落',question='是否支持此功能？')]}
        with patch.object(gateway,'reason_checked',return_value=(checks,{})):
            fission.check_story(self.pid,{'library_id':self.item['id']})
        bad=self.row(product=True);bad['story_check']={'checks':[{'id':'k1','answer':'yes'}]}
        result=self.save([bad]);self.assertTrue(result['row_errors'])
        self.assertEqual(result['rows'][0]['story_check']['checks'][0]['answer'],'')


if __name__ == '__main__':unittest.main()
