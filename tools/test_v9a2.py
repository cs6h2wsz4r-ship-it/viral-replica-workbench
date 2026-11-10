"""V9a2 fission contracts; all mutations stay in temporary project/library roots."""
import copy
import json
from pathlib import Path
import sys
from unittest.mock import patch
import unittest

sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools import test_v9a as base
from console import cast, config, fission, gateway, library, store, storyboard_edits as edits


class V9a2FissionTests(unittest.TestCase):
    setUpClass = classmethod(base.V9aTests.setUpClass.__func__)
    tearDownClass = classmethod(base.V9aTests.tearDownClass.__func__)
    setUp = base.V9aTests.setUp
    eligible = base.V9aTests.eligible

    def save(self, choices=None, removed=None):
        return fission.save_draft(self.pid, {'choices':choices or {}, 'removed':removed or []})

    def test_empty_open_includes_kept_role_without_row_errors(self):
        before=(self.d/'project.json').read_bytes()
        view=fission.view(self.pid)
        self.assertEqual([c['key'] for c in view['columns']], ['C1','C2','product'])
        kept=view['columns'][1]
        self.assertEqual(kept['action'],'keep')
        self.assertTrue(kept['master_name'].startswith('原片里的'))
        self.assertTrue(kept['file']);self.assertIsNone(kept['sha256'])
        self.assertEqual(view['rows'],[]);self.assertEqual(view['row_errors'],[])
        self.assertEqual((self.d/'project.json').read_bytes(),before)

    def test_cartesian_order_stable_keys_and_legacy_rows_readonly(self):
        second=library.upload('person','另一个人','p.png',self.product.read_bytes())['item']
        choices={'C1':[self.person['id'],second['id']], 'C2':[second['id'],self.person['id']]}
        view=self.save(choices)
        pairs=[(r['selections']['C1'],r['selections']['C2']) for r in view['rows']]
        self.assertEqual(pairs,[(a,b) for a in choices['C1'] for b in choices['C2']])
        self.assertEqual([r['id'] for r in view['rows']],[r['id'] for r in fission.view(self.pid)['rows']])
        self.assertNotIn('rows',store.load(self.pid)['fission_draft'])
        old=[{'id':'r1','selections':{'C1':self.person['id']}},{'id':'r2','selections':{'C1':second['id']}},{'id':'r3','selections':{'C1':self.person['id']}}]
        store.update(self.pid,lambda d:d.update(fission_draft={'rows':old}))
        before=(self.d/'project.json').read_bytes();migrated=fission.view(self.pid)
        self.assertEqual(migrated['draft']['choices']['C1'],choices['C1'])
        self.assertEqual(migrated['draft']['removed'],[])
        self.assertEqual(before,(self.d/'project.json').read_bytes())

    def test_removed_persists_across_other_choices_and_clears_with_slot(self):
        other=library.upload('person','其他','p.png',self.product.read_bytes())['item']
        choices={'C1':[self.person['id']], 'C2':[other['id']]}
        row=self.save(choices)['rows'][0];removed=[row['id']]
        self.assertEqual(self.save(choices,removed)['rows'],[])
        choices['C2'].append(self.person['id'])
        self.assertEqual(len(self.save(choices,removed)['rows']),1)
        choices['C2']=[other['id']]
        self.assertEqual(self.save(choices,removed)['rows'],[])
        choices['C1']=[]
        cleared=self.save(choices,removed)
        self.assertEqual(cleared['draft']['removed'],[])
        choices['C1']=[self.person['id']]
        self.assertEqual(len(self.save(choices,cleared['draft']['removed'])['rows']),1)

    def test_service_rejects_same_photo_kind_missing_and_over_twenty(self):
        self.eligible()
        same=library.from_project(self.pid,{'kind':'person','source_id':'C1'})['item']
        for value in [same['id'],self.item['id'],'deleted-item']:
            view=self.save({'C1':[value]})
            self.assertTrue(view['row_errors'])
            with self.assertRaises(ValueError):fission.create(self.pid,{})
        ids=[]
        from PIL import Image
        from io import BytesIO
        for i in range(21):
            raw=BytesIO();Image.new('RGB',(2,2),(i,10,10)).save(raw,format='PNG')
            ids.append(library.upload('person',str(i),'a.png',raw.getvalue())['item']['id'])
        view=self.save({'C1':ids});self.assertEqual(len(view['rows']),21)
        with self.assertRaisesRegex(ValueError,'20'):fission.create(self.pid,{})

    def test_product_questions_shared_by_photo_and_creation_clears_draft(self):
        self.eligible();self.save({'C1':[self.person['id']], 'product':[self.item['id']]})
        checks={'checks':[dict(id='k1',feature='动作',where='片段',question='能这样使用吗？')]}
        with patch.object(gateway,'reason_checked',return_value=(checks,{})) as reason:
            fission.check_story(self.pid,{'library_id':self.item['id']})
            answered=fission.answer_story(self.pid,{'library_id':self.item['id'],'answers':[{'id':'k1','answer':'no'}]})
            self.assertEqual(answered['product_checks'][self.item['id']]['checks'][0]['answer'],'no')
            fission.check_story(self.pid,{'library_id':self.item['id']});self.assertEqual(reason.call_count,1)
        result=fission.create(self.pid,{'request_id':'batch-one'})
        self.assertTrue(result['batch']['items'][0]['project_id'])
        draft=store.load(self.pid)['fission_draft']
        self.assertEqual(draft['choices'],{});self.assertEqual(draft['removed'],[])
        self.assertTrue(draft['story_cache'])
        self.assertEqual(fission.create(self.pid,{'request_id':'batch-one'})['batch']['id'],result['batch']['id'])
        self.save({'C1':[self.person['id']], 'product':[self.item['id']]})
        self.assertNotEqual(fission.create(self.pid,{'request_id':'batch-two'})['batch']['id'],result['batch']['id'])

    def test_keep_person_becomes_replace_refs_modes_changes_and_frame_prompt(self):
        self.eligible()
        def second_segment_has_no_kept_role(data):
            excluded=set(data['segments_plan']['segments'][1]['cells'])
            for cell in data['storyboard']['cells']:
                if cell['id'] in excluded:cell['characters']=[key for key in cell.get('characters',[]) if key!='C2']
        store.update(self.pid,second_segment_has_no_kept_role)
        view=self.save({'C2':[self.person['id']]})
        result=fission.create(self.pid,{'request_id':'keep-person'})
        child=store.load(result['batch']['items'][0]['project_id']);directory=store.project_dir(child['id'])
        entry=next(r for r in child['replacements']['characters'] if r['source_id']=='C2')
        self.assertEqual(entry['action'],'replace');self.assertNotIn('camera_assets',entry)
        self.assertIn('C2',child['segments_plan']['segments'][0]['refs'])
        self.assertNotIn('C2',child['segments_plan']['segments'][1]['refs'])
        self.assertEqual(child['segments_plan']['segments'][0]['mode'],'image')
        self.assertEqual(child['segments_plan']['segments'][0]['mode_suggested'],'image')
        self.assertEqual(child['segments_plan']['segments'][1]['mode'],'image')
        self.assertIn('带原片视频容易',child['segments_plan']['segments'][0]['mode_reason'])
        self.assertIn('C2',fission.changes(child,directory))
        messages=edits.plan_messages(child,'d01','',edits.materials_for(child,'d01',directory),directory)
        text='\n'.join(p.get('text','') for p in messages[1]['content'])
        self.assertIn('从：原片里的',text);self.assertIn('原片截帧',text)
        self.assertIn('只用来说明替换前',text)
        entry['asset_id']=child['replacements']['characters'][0]['asset_id']
        self.assertIn('C2',fission.changes(child,directory))

    def test_keep_product_story_signature_and_unknown_presence_fallback(self):
        self.eligible()
        def keep(d):
            d['replacements']['product']={'action':'keep','camera_assets':{'S1':'old'}}
            for cell in d['storyboard']['cells']:
                cell.pop('product',None);cell.pop('product_later',None)
        store.update(self.pid,keep)
        view=self.save({'product':[self.item['id']]})
        product=next(c for c in view['columns'] if c['key']=='product')
        self.assertEqual(product['master_name'],'原片商品');self.assertIsNone(product['file'])
        with patch.object(gateway,'reason_checked',return_value=({'checks':[]},{})):
            fission.check_story(self.pid,{'library_id':self.item['id']})
        batch=fission.create(self.pid,{})['batch'];child=store.load(batch['items'][0]['project_id']);directory=store.project_dir(child['id'])
        self.assertEqual(child['replacements']['product']['action'],'replace')
        self.assertFalse(cast.story_stale(child,directory))
        for segment in child['segments_plan']['segments']:
            self.assertIn('product',segment['refs']);self.assertEqual(segment['mode'],'image')
            self.assertIn('product',[r['key'] for r in edits.material_options(child,segment['id'],directory)])
        messages=edits.plan_messages(child,'d01','',edits.materials_for(child,'d01',directory),directory)
        text='\n'.join(p.get('text','') for p in messages[1]['content'])
        self.assertIn('从：原片里的商品',text)
        self.assertNotIn('原片里的原片',child['segments_plan']['segments'][0]['mode_reason'])

    def test_legacy_snapshot_without_keep_material_still_detects_later_replacement(self):
        self.eligible();self.save({'C1':[self.person['id']]})
        batch=fission.create(self.pid,{})['batch'];child=store.load(batch['items'][0]['project_id']);directory=store.project_dir(child['id'])
        child['derived_from']['master_snapshot']['materials']={}
        entry=next(e for e in child['replacements']['characters'] if e['source_id']=='C2')
        entry.update(action='replace',asset_id=child['replacements']['characters'][0]['asset_id'])
        changed=fission.changes(child,directory)
        self.assertIn('C2',changed)
        self.assertTrue(changed['C2']['from_name'].startswith('原片里的'))

    def test_missing_master_photo_and_forged_removal_are_rejected(self):
        self.eligible()
        asset=next(a for a in self.data['assets'] if a['id']==self.data['replacements']['characters'][0]['asset_id'])
        (self.d/asset['file']).unlink()
        self.save({'C2':[self.person['id']]})
        with self.assertRaisesRegex(ValueError,'母版照片'):fission.create(self.pid,{})
        with self.assertRaises(ValueError):self.save({'C2':[self.person['id']]},[{}])

    def test_default_kept_product_without_source_evidence_has_no_slot(self):
        def no_product(data):
            data['replacements']['product']={'action':'keep','story_checks':[]}
            data['breakdown']['productMoments']=[]
            for cell in data['storyboard']['cells']:cell.update(product='',product_later=False)
        store.update(self.pid,no_product)
        self.assertNotIn('product',[c['key'] for c in fission.view(self.pid)['columns']])
        store.update(self.pid,lambda d:d['breakdown'].update(productMoments=[{'start':0,'end':1,'product':'原片物品'}]))
        self.assertIn('product',[c['key'] for c in fission.view(self.pid)['columns']])

    def test_kept_product_thumbnail_requires_visibility_at_frame_time(self):
        def product_later(data):
            data['replacements']['product']={'action':'keep'}
            for cell in data['storyboard']['cells']:cell.update(product='',product_later=True)
            data['storyboard']['cells'][2]['product']='可见商品'
        data=store.update(self.pid,product_later)
        product=next(c for c in fission.view(self.pid)['columns'] if c['key']=='product')
        self.assertEqual(product['file'],data['storyboard']['cells'][2]['file'])
        store.update(self.pid,lambda d:d['storyboard']['cells'][2].update(product=''))
        product=next(c for c in fission.view(self.pid)['columns'] if c['key']=='product')
        self.assertIsNone(product['file'])

    def test_idempotent_retry_clears_restored_draft_but_preserves_new_choices(self):
        self.eligible();choices={'C1':[self.person['id']]};self.save(choices)
        batch=fission.create(self.pid,{'request_id':'retry-original'})['batch']
        self.save(choices)
        self.assertEqual(fission.create(self.pid,{'request_id':'retry-original'})['batch']['id'],batch['id'])
        self.assertEqual(fission.view(self.pid)['draft']['choices'],{})
        other=library.upload('person','另一张','p.png',self.product.read_bytes())['item']
        changed={'C1':[other['id']]};self.save(changed)
        fission.create(self.pid,{'request_id':'retry-original'})
        self.assertEqual(fission.view(self.pid)['draft']['choices'],changed)
        # Even if removals make the generated rows equal, a new selection must survive.
        choices_with_removed={'C1':[self.person['id'],other['id']]}
        rows=self.save(choices_with_removed)['rows']
        removed=[r['id'] for r in rows if r['selections']['C1']==other['id']]
        self.save(choices_with_removed,removed)
        fission.create(self.pid,{'request_id':'retry-original'})
        self.assertEqual(fission.view(self.pid)['draft']['choices'],choices_with_removed)
        self.assertEqual(fission.view(self.pid)['draft']['removed'],removed)


if __name__=='__main__':unittest.main()
