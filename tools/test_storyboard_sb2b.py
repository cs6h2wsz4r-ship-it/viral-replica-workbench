"""SB2b: synthetic images and one disposable read-only-source project copy; no models."""
import copy
import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import config, gateway, store, storyboard, storyboard_edits, storyboard_images
from tools import test_storyboard_sb2 as sb2

COLORS = [(180,20,20),(20,170,20),(20,20,180),(180,170,20),(180,20,180),
          (190,90,20),(20,170,170),(200,60,100),(90,60,30)]


def grid(margin=0, shifted=False, gutter=4, count=9):
    """Square panels with a central circle expose stretching to portrait tiles."""
    cuts = [0,110,230,300] if shifted else [0,100,200,300]
    im = Image.new('RGB', (300+margin*2,300+margin*2), 'white')
    draw = ImageDraw.Draw(im)
    for i in range(count):
        col,row=i%3,i//3
        x0,y0=cuts[col]+margin,cuts[row]+margin
        x1,y1=cuts[col+1]+margin-(gutter if col<2 else 0),cuts[row+1]+margin-(gutter if row<2 else 0)
        draw.rectangle((x0,y0,x1-1,y1-1), fill=COLORS[i])
        x,y=(x0+x1)//2,(y0+y1)//2
        draw.ellipse((x-12,y-12,x+12,y+12), fill='black')
    return im


class StoryboardSB2bTests(unittest.TestCase):
    setUp = sb2.StoryboardSB2Tests.setUp
    fixture = sb2.StoryboardSB2Tests.fixture
    valid = sb2.StoryboardSB2Tests.valid
    plan = sb2.StoryboardSB2Tests.plan

    def record(self, image, cid=None, base=None):
        data=store.load(self.pid)
        cells=data['storyboard']['cells'][:8]
        ctx=dict(sheet_id='s01',cell_id=cid,cells=cells,cell_size=[90,160],
                 padding=dict(size=[400,600],left=100,top=0,width=200,height=600),
                 plan_sig='plan',storyboard_sig='board',base_current=base,
                 base_cells=copy.deepcopy(next((v['cells'] for v in data.get('storyboard_edits',{}).get('sheets',{}).get('s01',{}).get('versions',[]) if v['id']==base),{})))
        iid=store.new_id('img');folder=self.d/'image-runs'/iid;folder.mkdir()
        image.save(folder/'result.png')
        record=dict(id=iid,run_dir=str(folder.relative_to(self.d)),result=str((folder/'result.png').relative_to(self.d)),storyboard_context=ctx)
        def update(d):
            d['images'][iid]=record
            d.setdefault('storyboard_edits',{}).setdefault('sheets',{}).setdefault('s01',dict(versions=[],current=None))
        store.update(self.pid,update)
        storyboard_images.complete(self.pid,record,folder/'result.png')
        return store.load(self.pid)['storyboard_edits']['sheets']['s01']['versions'][-1]

    def test_full_result_margins_shifted_thin_missing_and_aspect(self):
        for label,kwargs in [('margins',dict(margin=18)),('full',{}),('shifted',dict(shifted=True)),('thin',dict(gutter=1)),('missing',dict(gutter=0)),('margin_missing',dict(margin=18,gutter=0))]:
            with self.subTest(case=label):
                image=grid(**kwargs)
                tiles=storyboard_images.split_sheet(image,(90,160),8)
                self.assertEqual(len(tiles),8)
                for i,tile in enumerate(tiles):
                    self.assertEqual(tile.size,(90,160))
                    self.assertLess(sum(abs(a-b) for a,b in zip(tile.getpixel((45,20)),COLORS[i])),10)
                    # A circle stays round; stretching a square into portrait fails this.
                    dark=tile.convert('L').point(lambda x:255 if x<5 else 0).getbbox()
                    self.assertIsNotNone(dark)
                    self.assertLessEqual(abs((dark[2]-dark[0])-(dark[3]-dark[1])),2)

    def test_incomplete_sheet_leaves_empty_rows_and_columns_unused(self):
        for count in (1,2,3,4,5,6,7,8):
            with self.subTest(count=count):
                tiles=storyboard_images.split_sheet(grid(count=count,margin=12),(90,160),count)
                self.assertEqual(len(tiles),count)
                for i,tile in enumerate(tiles):
                    self.assertLess(sum(abs(a-b) for a,b in zip(tile.getpixel((45,20)),COLORS[i])),10)

    def test_white_threshold_bands_and_one_axis_fallback(self):
        image=Image.new('RGB',(300,300),(20,60,100));draw=ImageDraw.Draw(image)
        for x in (99,100,199,200):
            draw.line((x,0,x,269),fill=(235,235,235))  # Exactly 90%, inclusive threshold.
        draw.line((55,0,55,299),fill=(235,234,255))  # One channel below threshold.
        cells=[dict(id=f'c{i+1:02d}') for i in range(9)]
        _,info=storyboard_images.slice_result(image,(90,160),cells)
        self.assertEqual(info['separators']['vertical'],[[99,101],[199,201]])
        self.assertEqual(info['separators']['horizontal'],[])
        self.assertEqual(info['method'],dict(x='separators',y='equal'))
        self.assertEqual(info['cells']['c08']['bounds'],[101,200,199,300])

    def test_reslice_failure_restores_files_and_missing_result_does_not_write(self):
        self.fixture();v=self.record(grid())
        paths=[v['file'],v['clean_file'],*v['cells'].values(),'project.json']
        before={p:(self.d/p).read_bytes() for p in paths}
        with patch.object(store,'update',side_effect=OSError('synthetic disk error')):
            with self.assertRaisesRegex(OSError,'synthetic disk error'):
                storyboard_images.reslice(self.pid,'s01','v1')
        self.assertEqual(before,{p:(self.d/p).read_bytes() for p in paths})
        record=store.load(self.pid)['images'][v['image_id']]
        (self.d/record['result']).unlink()
        with self.assertRaisesRegex(ValueError,'模型原图找不到'):
            storyboard_images.reslice(self.pid,'s01','v1')
        self.assertEqual(before,{p:(self.d/p).read_bytes() for p in paths})
        self.assertFalse(list((self.d/'analysis/storyboard/edits').glob('.reslice-*')))

    def test_sparse_count_without_white_edges_still_uses_full_three_by_three(self):
        # Missing separators/margins must fall back to the WHOLE result 3x3,
        # regardless of how many cells are requested from it.
        image=grid(gutter=0)
        _,geometry=storyboard_images.slice_result(image,(90,160),[dict(id=f'c{i:02d}') for i in range(1,5)])
        self.assertEqual(geometry['cells']['c01']['bounds'],[0,0,100,100])
        tiles=storyboard_images.split_sheet(image,(90,160),4)
        for i,tile in enumerate(tiles):
            self.assertLess(sum(abs(a-b) for a,b in zip(tile.getpixel((45,20)),COLORS[i])),10)

    def test_near_white_empty_tail_without_detected_margins_uses_full_grid(self):
        image=grid(count=3,gutter=0);ImageDraw.Draw(image).rectangle((0,100,299,299),fill=(234,234,234))
        _,geometry=storyboard_images.slice_result(image,(90,160),[dict(id=f'c{i:02d}') for i in range(1,4)])
        self.assertEqual(geometry['cells']['c01']['bounds'],[0,0,100,100])
        self.assertEqual(geometry['margins'],dict(left=0,right=0,top=0,bottom=0))

    def test_legacy_base_requires_reslice_before_new_single_preview(self):
        self.plan();rec=self.edit.preview(self.pid,'s01',{})
        output=self.tmp/'result.png';grid().save(output)
        storyboard_images.complete(self.pid,rec,output)
        store.update(self.pid,lambda d:d['storyboard_edits']['sheets']['s01']['versions'][0].pop('slicer'))
        before=store.load(self.pid)
        with patch.object(gateway,'image_preview') as preview:
            with self.assertRaisesRegex(ValueError,'先重新切上一版'):
                self.edit.preview(self.pid,'s01',{'cell_id':'c05','request':'改手表'})
            preview.assert_not_called()
        self.assertEqual(store.load(self.pid),before)

    def test_reslice_invalidates_a_pending_single_preview(self):
        self.plan();rec=self.edit.preview(self.pid,'s01',{})
        output=self.d/rec['run_dir']/'result.png';output.parent.mkdir(exist_ok=True);grid().save(output)
        storyboard_images.complete(self.pid,rec,output)
        with patch.object(gateway,'reason_checked',return_value=({'changes':'按本次要求调整画面','keep':'','why':'已整理这一镜的改法'},{})):
            one=self.edit.preview(self.pid,'s01',{'cell_id':'c05','request':'改手表'})
        self.edit.verify_preview(self.pid,one)
        storyboard_images.reslice(self.pid,'s01','v1')
        with self.assertRaisesRegex(ValueError,'重新整理改法并生成预览'):
            self.edit.verify_preview(self.pid,one)

    def test_inflight_legacy_single_completion_keeps_repair_notice(self):
        self.fixture();self.record(grid())
        store.update(self.pid,lambda d:d['storyboard_edits']['sheets']['s01']['versions'][0].pop('slicer'))
        version=self.record(Image.new('RGB',(180,180),'purple'),'c05','v1')
        self.assertNotEqual(version.get('slicer'),2,'inherited bad cuts still need the free repair')
        with self.assertRaisesRegex(ValueError,'先重新切上一版'):
            storyboard_images.reslice(self.pid,'s01',version['id'])

    def test_complete_records_geometry_and_skips_unused_ninth_cell(self):
        self.fixture();v=self.record(grid(count=8,shifted=True))
        self.assertEqual(v.get('slicer'),2)
        self.assertEqual(len(v['cells']),8);self.assertNotIn('c09',v['cells'])
        self.assertEqual(v['slicing']['separators']['vertical'],[[106,110],[226,230]])
        box=v['slicing']['cells']['c08']['crop']
        self.assertAlmostEqual((box[2]-box[0])/(box[3]-box[1]),90/160)
        self.assertGreaterEqual(box[0],110);self.assertGreaterEqual(box[1],230)
        self.assertLessEqual(box[2],226);self.assertLessEqual(box[3],300)

    def test_single_cell_reslice_uses_base_version_and_preserves_other_bytes(self):
        self.fixture();v1=self.record(grid());v2=self.record(Image.new('RGB',(180,180),'purple'),'c05','v1')
        # The base was v1 even if a later full version exists/current changes.
        self.record(grid(margin=10))
        def legacy(d):
            for v in d['storyboard_edits']['sheets']['s01']['versions'][:2]:v.pop('slicer',None)
        store.update(self.pid,legacy)
        self.assertTrue(callable(getattr(storyboard_images,'reslice',None)),'local reslice is available')
        with patch.object(gateway,'reason_checked') as reason,patch.object(gateway,'image_preview') as image_call:
            with self.assertRaisesRegex(ValueError,'先重新切上一版'):storyboard_images.reslice(self.pid,'s01','v2')
            storyboard_images.reslice(self.pid,'s01','v1')
            before={k:(self.d/f).read_bytes() for k,f in v1['cells'].items()}
            result=storyboard_images.reslice(self.pid,'s01','v2')
            state=result['storyboard_edits']['sheets']['s01'];new=state['versions'][1]
            self.assertEqual(len(state['versions']),3);self.assertEqual(state['current'],'v3')
            self.assertEqual(new['id'],'v2');self.assertEqual(new['slicer'],2)
            self.assertEqual(set(new['slicing']['cells']),{'c05'})
            for cid,file in new['cells'].items():
                if cid!='c05':self.assertEqual((self.d/file).read_bytes(),before[cid])
            reason.assert_not_called();image_call.assert_not_called()

    def test_single_cell_without_assumed_padding_and_center_crop(self):
        self.fixture()
        self.record(grid())
        for margin in (0,15):
            image=Image.new('RGB',(180+margin*2,180+margin*2),'white')
            tile=Image.new('RGB',(180,180),(20,140,180));draw=ImageDraw.Draw(tile)
            draw.ellipse((65,65,115,115),fill='black');image.paste(tile,(margin,margin))
            version=self.record(image,'c05','v1')
            with Image.open(self.d/version['cells']['c05']) as result:
                box=result.convert('L').point(lambda x:255 if x<10 else 0).getbbox()
                self.assertLessEqual(abs((box[2]-box[0])-(box[3]-box[1])),2)
            self.assertEqual(version['slicing']['margins'],dict(left=margin,right=margin,top=margin,bottom=margin))

    def test_product_at_frame_later_and_version_three(self):
        pack={'shots':[dict(start=0,end=1,characters=['C1','C2']),dict(start=1,end=3,characters=['C1'])],
              'productMoments':[dict(start=1.5,end=2.8,product='手表')]}
        cell=storyboard.cell_data(0,3,pack)
        self.assertEqual(cell['frame_at'],.5);self.assertEqual(cell['product'],'')
        self.assertTrue(cell.get('product_later'))
        self.assertEqual(storyboard.VERSION,3)
        self.assertTrue(storyboard.status({'storyboard':{'version':2}},self.d)['upgrade'])
        for start,end,at,present,later in [(0,1,.5,False,False),(1,2,1.5,True,False),(2.8,3,2.9,False,False)]:
            c=storyboard.cell_data(start,end,{'productMoments':pack['productMoments']})
            self.assertEqual(bool(c['product']),present);self.assertEqual(c.get('product_later',False),later)

    def test_plan_optional_supplement_primary_settings_and_later_product(self):
        data=self.fixture()
        def later_product(d):
            d['storyboard']['cells'][0].update(frame_at=.2,product='旧字段误报',product_later=False)
            d['breakdown']['productMoments']=[dict(start=.6,end=.9,product='手表')]
        store.update(self.pid,later_product)
        for request in ('','第 5 格戴左手'):
            with patch.object(gateway,'reason_checked',return_value=(self.valid(data),{})) as reason:
                result=self.edit.generate_plan(self.pid,'s01',{'request':request})
            messages=reason.call_args.args[1]
            text='\n'.join(p['text'] for p in messages[1]['content'] if p['type']=='text')
            self.assertIn('第 03 步的设定',text);self.assertIn('白发父亲',text)
            self.assertIn('补充要求：'+(request or '无'),text)
            rows=json.loads(messages[1]['content'][0]['text'].split('每格信息：')[1])
            self.assertEqual(rows[0]['商品'],'');self.assertEqual(rows[0]['后半段出现的商品'],'手表')
            self.assertIn('不要画进去',messages[0]['content'])
            self.assertEqual(result['storyboard_edits']['sheets']['s01']['request'],request)

    def test_empty_product_and_character_descriptions_in_b7_b9(self):
        data=self.fixture();data['storyboard_edits']={'sheets':{'s01':{'plan':self.valid(data)}}}
        refs=[dict(key='source'),dict(key='C1',description=''),dict(key='product',description='')]
        for cid in (None,'c01'):
            prompt=self.edit.image_prompt(data,'s01',refs,cid,'改一下')
            self.assertIn('Image 3 is the new product. Use it only for shape, color, material and details;',prompt)
            self.assertNotIn(': .',prompt);self.assertNotIn('. .',prompt)
            self.assertIn('THE FATHER',prompt)

    def test_reslice_api_page_version_guard(self):
        from console import runtime_env
        with patch.object(runtime_env,'apply'):
            import server
        self.fixture();v=self.record(grid())
        handler=object.__new__(server.Handler);handler.headers={}
        body={'action':'reslice','sheet_id':'s01','version_id':v['id']}
        status=dict(restart_pending=False,build='sb2b',server_build='s',web_build='w')
        with patch.object(server,'build_status',return_value=status),patch.object(handler,'_body_json',return_value=body),patch.object(server,'project_view',side_effect=lambda d:d):
            for headers in ({},{'X-Client-Build':'old'}):
                handler.headers=headers
                with self.assertRaises(server.ApiError):handler._api('POST',['projects',self.pid,'storyboard-edits'],{})
            handler.headers={'X-Client-Build':'sb2b'}
            result=handler._api('POST',['projects',self.pid,'storyboard-edits'],{})
            self.assertEqual(result['storyboard_edits']['sheets']['s01']['versions'][0]['slicer'],2)

    def test_user_project_copy_reslice(self):
        pid='p1031220547-a2c0';source=ROOT/'projects'/pid;target=self.tmp/pid
        shutil.copytree(source,target)
        self.addCleanup(shutil.rmtree,target,True)
        self.assertTrue(callable(getattr(storyboard_images,'reslice',None)),'local reslice is available')
        with patch.object(gateway,'reason_checked') as reason,patch.object(gateway,'image_preview') as gen:
            result=storyboard_images.reslice(pid,'s01','v1')
            reason.assert_not_called();gen.assert_not_called()
        state=result['storyboard_edits']['sheets']['s01'];v=state['versions'][0]
        self.assertEqual(len(state['versions']),1);self.assertEqual(v['slicer'],2)
        geometry=v['slicing'];vertical=geometry['separators']['vertical'];horizontal=geometry['separators']['horizontal']
        for band,(low,high) in zip(vertical,[(330,360),(670,700)]):self.assertTrue(low<=sum(band)/2<=high)
        for band,(low,high) in zip(horizontal,[(560,590),(1130,1160)]):self.assertTrue(low<=sum(band)/2<=high)
        self.assertEqual(len(vertical),2);self.assertEqual(len(horizontal),2)
        x0,y0,x1,y1=geometry['cells']['c08']['crop']
        self.assertGreaterEqual(x0,vertical[0][1]);self.assertLessEqual(x1,vertical[1][0])
        self.assertGreaterEqual(y0,horizontal[1][1]);self.assertLessEqual(y1,1536)
        preview=ROOT/'runtime/temp/sb2b_preview';preview.mkdir(exist_ok=True)
        shutil.copyfile(target/v['file'],preview/'edit-s01-v1.jpg')
        (preview/'slicing.json').write_text(json.dumps(geometry,ensure_ascii=False,indent=2))


if __name__=='__main__':
    from tools.run_tests_offline import audit
    sys.addaudithook(audit)
    unittest.main(verbosity=2)
