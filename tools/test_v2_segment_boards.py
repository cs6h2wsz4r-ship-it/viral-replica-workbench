"""V2 per-segment image layouts, local composition and mocked paid bridge."""
import copy
import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from console import gateway, store, storyboard_edits as edits, storyboard_images as images
from tools.test_v2_segment_plan import SegmentFixture


class SegmentBoardTests(SegmentFixture,unittest.TestCase):
    def plan(self,sid):
        data=store.load(self.pid);_,cells=edits.sheet_cells(data,sid)
        value=dict(summary='换人换表',cells=[dict(cell=c['id'],changes='换父亲',keep='机位') for c in cells],remove_text=True,questions=[])
        with patch.object(gateway,'reason_checked',return_value=(value,{})):
            return edits.generate_plan(self.pid,sid,{})

    def test_all_layouts_original_clean_and_padded_dimensions(self):
        from console import segment_plan,segment_boards
        for count,expected in ((1,(2,1)),(2,(2,1)),(3,(2,2)),(4,(2,2)),(5,(3,2)),(6,(3,2))):
            with self.subTest(count=count):
                data=self.fixture(count)
                value=dict(segments=[dict(id='d01',cells=[c['id'] for c in data['storyboard']['cells']],mode='video',refs=['C1'],gen_images=[data['storyboard']['cells'][-1]['id'],'C1'])])
                with patch.object(gateway,'reason_checked',return_value=(value,{})):
                    data=segment_plan.generate(self.pid)
                self.assertEqual(segment_boards.layout(count),expected)
                sheet=segment_boards.sheets(data)[0];cols,rows=expected
                expected_size=(90*cols+6*(cols-1),160*rows+6*(rows-1))
                for key in ('file','clean_file'):
                    with Image.open(self.d/sheet[key]) as img:self.assertEqual(img.size,expected_size)
                self.plan('d01');rec=edits.preview(self.pid,'d01',{})
                self.assertEqual(rec['preview']['size'],'1024x1536')
                with Image.open(rec['storyboard_context']['references'][0]['path']) as img:
                    self.assertEqual(img.width*3,img.height*2)
                    self.assertEqual(img.size,tuple(rec['storyboard_context']['padding']['size']))
                    unit=math.ceil(max(expected_size[0]/2,expected_size[1]/3))
                    self.assertEqual(img.size,(unit*2,unit*3))
                self.assertEqual(rec['storyboard_context']['layout'],[cols,rows])
                self.assertIn(f'{cols} 列 × {rows} 行',rec['preview']['prompt'])
                self.assertIn('9:16',rec['preview']['prompt'])

    def test_result_slicing_uses_layout_positions(self):
        from console import segment_boards
        for count in (3,4,5,6):
            cols,rows=segment_boards.layout(count);colors=[(30+i*30,60,80) for i in range(count)]
            img=Image.new('RGB',(cols*94+12,rows*164+12),'white');draw=ImageDraw.Draw(img)
            for i,color in enumerate(colors):
                x=8+i%cols*94;y=8+i//cols*164
                draw.rectangle((x,y,x+89,y+159),fill=color)
            cells=[dict(id=f'c{i+1:02d}') for i in range(count)]
            tiles,info=images.slice_result(img,[90,160],cells,layout=(cols,rows))
            self.assertEqual(len(tiles),count)
            for cell,color in zip(cells,colors):self.assertEqual(tiles[cell['id']].getpixel((45,80)),color)
            self.assertGreater(info['cells'][cells[-1]['id']]['bounds'][1],0)

    def test_refs_defaults_plan_layout_and_per_segment_staleness(self):
        from console import segment_plan,segment_boards
        self.generated();data=self.plan('d01');data=self.plan('d02')
        self.assertEqual(edits.materials_for(data,'d01',self.d),{'C1':True})
        messages=edits.plan_messages(data,'d02','',edits.materials_for(data,'d02',self.d),self.d)
        self.assertIn('本段共 5 格，3 列 × 2 行',messages[0]['content'])
        self.assertIn('只露出手、手臂、腿',messages[0]['content'])
        saved=segment_plan.save(self.pid,'d01',{'mode':'image','refs':['C1','C2']})
        self.assertIn(segment_boards.STALE_WARNING,edits.warnings(saved,'d01',self.d))
        self.assertEqual(edits.warnings(saved,'d02',self.d),[])
        self.assertNotIn('C2',edits.materials_for(saved,'d01',self.d))
        self.assertNotIn('product',edits.materials_for(saved,'d01',self.d))
        self.assertEqual(edits.view(saved,self.d)['d01']['active_materials'],edits.materials_for(saved,'d01',self.d))

    def test_full_cell_redraw_versions_reslice_and_old_data_untouched(self):
        from console import segment_boards
        data=self.generated();old=copy.deepcopy(data['storyboard'])
        self.plan('d01');self.plan('d02')
        rec=edits.preview(self.pid,'d01',{});ctx=rec['storyboard_context']
        output=self.d/'result.png'
        with Image.open(ctx['references'][0]['path']) as image:image.save(output)
        images.complete(self.pid,rec,output)
        data=store.load(self.pid);first=copy.deepcopy(segment_boards.current(data,'d01'))
        self.assertEqual(first['id'],'v1');self.assertEqual(segment_boards.current(data,'d02'),None)
        with patch.object(gateway,'reason_checked',return_value=({'changes':'按本次要求调整画面','keep':'','why':'已整理这一镜的改法'},{})):
            rec=edits.preview(self.pid,'d01',{'cell_id':'c02','request':'父亲换表情'})
        Image.new('RGB',(100,180),(10,200,10)).save(output)
        images.complete(self.pid,rec,output)
        data=store.load(self.pid);current=segment_boards.current(data,'d01')
        self.assertEqual(current['id'],'v2')
        self.assertEqual((self.d/first['cells']['c01']).read_bytes(),(self.d/current['cells']['c01']).read_bytes())
        with Image.open(self.d/current['file']) as img:self.assertEqual(img.size,(186,326))
        self.assertEqual(data['storyboard'],old)
        edits.use_version(self.pid,'d01','v1')
        self.assertEqual(segment_boards.current(store.load(self.pid),'d01')['id'],'v1')
        self.assertEqual(len(segment_boards.state_for(data,'d01')['versions']),2)

    def test_reslice_each_layout_and_source_frames_are_never_extracted(self):
        from console import segment_boards,media
        with patch.object(media,'extract_frame',side_effect=AssertionError('must reuse Step02 frames')):
            self.generated()
        for sid in ('d01','d02'):
            self.plan(sid);rec=edits.preview(self.pid,sid,{})
            output=self.d/f'{sid}-result.png'
            with Image.open(rec['storyboard_context']['references'][0]['path']) as image:image.save(output)
            store.update(self.pid,lambda d:d['images'][rec['id']].update(result=str(output.relative_to(self.d))))
            images.complete(self.pid,rec,output)
            before=copy.deepcopy(segment_boards.current(store.load(self.pid),sid))
            saved=images.reslice(self.pid,sid,'v1');after=segment_boards.current(saved,sid)
            self.assertIn('resliced_at',after);self.assertEqual(after['layout'],before['layout'])
            self.assertEqual(after['cells'],before['cells']);self.assertEqual(after['id'],'v1')
            with Image.open(self.d/after['file']) as image:
                cols,rows=segment_boards.layout(len(after['cells']))
                self.assertEqual(image.size,(90*cols+6*(cols-1),160*rows+6*(rows-1)))

    def test_preview_stale_after_refs_edit_until_new_version_and_other_part_stays_current(self):
        from console import segment_plan,segment_boards
        self.generated()
        for sid in ('d01','d02'):
            self.plan(sid);rec=edits.preview(self.pid,sid,{})
            output=self.d/f'{sid}.png'
            with Image.open(rec['storyboard_context']['references'][0]['path']) as image:image.save(output)
            images.complete(self.pid,rec,output)
        rec=edits.preview(self.pid,'d01',{})
        before=copy.deepcopy(segment_boards.state_for(store.load(self.pid),'d02'))
        data=segment_plan.save(self.pid,'d01',{'mode':'image','refs':['C1','C2']})
        with self.assertRaisesRegex(ValueError,'改过'):edits.verify_preview(self.pid,rec)
        self.assertEqual(segment_boards.state_for(data,'d02'),before)
        data=self.plan('d01')
        self.assertIn(segment_boards.STALE_WARNING,edits.warnings(data,'d01',self.d))
        rec=edits.preview(self.pid,'d01',{})
        output=self.d/'new.png'
        with Image.open(rec['storyboard_context']['references'][0]['path']) as image:image.save(output)
        images.complete(self.pid,rec,output)
        data=store.load(self.pid)
        self.assertEqual(edits.warnings(data,'d01',self.d),[])
        self.assertEqual(edits.warnings(data,'d02',self.d),[])

    def test_original_frame_bytes_change_invalidates_existing_source_board(self):
        self.generated(); self.plan('d01')
        data=store.load(self.pid)
        Image.new('RGB',(90,160),'green').save(self.d/data['storyboard']['cells'][0]['file'])
        with patch.object(gateway,'image_preview') as preview:
            with self.assertRaisesRegex(ValueError,'原片分镜变了'):
                edits.preview(self.pid,'d01',{})
            preview.assert_not_called()


if __name__=='__main__':unittest.main()
