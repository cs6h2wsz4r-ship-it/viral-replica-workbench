"""SB1 local-only storyboard rules and synthetic media, never real projects."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import config, gateway, media, pipeline, runtime_env, store


def detail(start, end, chars=None, product='', action='走进房间'):
    return dict(start=start, end=end, characters=chars or [], product=product, action=action)


class StoryboardSB1Tests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {'PATH':str(ROOT/'runtime/ffmpeg')+os.pathsep+os.environ.get('PATH','')}))
        from console import storyboard
        self.sb = storyboard
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.tmp))
        self.enterContext(patch.object(config, 'load', return_value=dict(config.DEFAULTS)))
        self.pid = store.create('SB1 synthetic')['id']
        self.d = store.project_dir(self.pid)

    def cells(self, merged, shots=None):
        return self.sb.plan_cells(merged, {'shots': shots or [], 'characters':[{'id':'C2','name':'儿子'}]})

    def test_rules_first_change_and_at_most_two(self):
        merged = [{'index':1,'start':0,'end':3}]
        self.assertEqual(len(self.cells(merged)), 1)
        cells = self.cells(merged, [detail(0,1,['C1']), detail(1,2,['C1','C2']), detail(2,3,['C2'])])
        self.assertEqual([(c['start'],c['end'],c['part']) for c in cells], [(0,1,1),(1,3,2)])
        self.assertIn('C2 儿子进画面', cells[0]['split_reason'])
        product = self.cells(merged,[detail(0,1,['C1']), detail(1,3,['C1'],'手表')])
        self.assertEqual(product[0]['end'],1)
        self.assertIn('商品出现',product[1]['split_reason'])
        gone = self.cells(merged,[detail(0,2,['C1'],'手表'),detail(2,3,['C1'])])
        self.assertIn('商品消失',gone[0]['split_reason'])

    def test_duration_endpoint_margin_and_midpoint_assignment(self):
        self.assertEqual([c['end'] for c in self.cells([dict(start=0,end=4)])],[2,4])
        for split in (.3,2.7):
            self.assertEqual(len(self.cells([dict(start=0,end=3)], [detail(0,split,['C1']),detail(split,3,['C2'])])),1)
        # First change too close to the end: a long shot still follows the duration rule.
        self.assertEqual(self.cells([dict(start=0,end=4)], [detail(0,.2,['C1']),detail(.2,2,['C2']),detail(2,4,['C1'])])[0]['end'],2)
        cells=self.cells([dict(start=0,end=2),dict(start=2,end=4)], [detail(0,1,['C1']), detail(1,3,['C2']),detail(3,4,['C2'])])
        self.assertEqual(len(cells),2)  # midpoint=2 belongs to the second shot
        self.assertEqual(len(self.cells([dict(start=0,end=3)])),1)

    def test_frame_fullest_people_and_metadata(self):
        pack={'shots':[detail(0,1,['C1']),detail(1,2,['C1','C2'],'表','一起看表'),detail(2,3,['C1','C2'],'表','走开')],
              'dialogue':[dict(start=.5,end=1.2,text='爸')], 'overlays':[dict(start=1,end=2,text='救援') ]}
        cell=self.sb.cell_data(0,3,pack)
        self.assertEqual(cell['frame_at'],1.5)
        self.assertEqual(cell['characters'],['C1','C2'])
        self.assertEqual(cell['dialogue'],'爸')
        self.assertEqual(cell['overlay'],'救援')
        repeated=self.sb.cell_data(0,2,{'dialogue':[dict(start=0,end=1,text='快'),dict(start=1,end=2,text='快')]})
        self.assertEqual(repeated['dialogue'],'快；快')
        self.assertEqual(len(self.sb.cell_data(0,3,{'shots':[detail(0,3,action='很长'*80)]})['action']),40)
        self.assertEqual(self.sb.cell_data(0,3,{'shots':[detail(0,1,['C1']),detail(1,3,['C2'])]})['frame_at'],1.5)

    def video(self, count=10):
        target=self.d/'source/source.mp4'
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',
            'testsrc2=size=90x160:rate=6:duration=12','-c:v','libx264','-pix_fmt','yuv420p',str(target)],check=True)
        store.atomic_write_json(self.d/'analysis/boundaries.json',{'merged_shots':[dict(index=i+1,start=i*1.2,end=(i+1)*1.2) for i in range(count)]})
        store.update(self.pid,lambda d:d.update(source={'file':'source/source.mp4'},breakdown={'shots':[detail(0,1.2,['C1']), detail(1.2,2.4,['C2'])]}))

    def test_sheets_pixels_merge_restore_and_stale_api(self):
        from PIL import Image, ImageChops
        self.video()
        data=self.sb.build(self.pid); sb=data['storyboard']; original=copy.deepcopy(sb['cells'])
        self.assertEqual(len(sb['cells']),10); self.assertEqual(len(sb['sheets']),2)
        raw=Image.open(self.d/sb['cells'][0]['file']).convert('RGB')
        sheet=Image.open(self.d/sb['sheets'][0]['file']).convert('RGB')
        self.assertEqual(sheet.size,(raw.width*3+12,raw.height*3+12))
        self.assertIsNotNone(ImageChops.difference(sheet.crop((0,0,240,65)),raw.crop((0,0,240,65))).getbbox())
        self.assertGreater(min(sheet.getpixel((raw.width+3,200))),240)
        last=Image.open(self.d/sb['sheets'][1]['file']); self.assertGreater(min(last.getpixel((last.width-20,last.height-20))),240)
        merged=self.sb.merge(self.pid,'c01')['storyboard']; c=merged['cells'][0]
        self.assertEqual((c['start'],c['end']),(0,2.4)); self.assertTrue(c['manual']); self.assertEqual(c['characters'],['C1','C2'])
        self.assertEqual(c['frame_at'],original[0]['frame_at'])
        self.assertEqual([c['index'] for c in merged['cells']],list(range(1,10)))
        self.assertEqual(merged['merges'],[['c01','c02']])
        self.assertEqual(self.sb.build(self.pid)['storyboard']['cells'],original)
        store.update(self.pid,lambda d:d['breakdown'].update(dialogue=[dict(start=0,end=1,text='变了')]))
        self.assertTrue(self.sb.status(store.load(self.pid),self.d)['stale'])
        with patch.object(runtime_env,'apply'):
            import server
        handler=object.__new__(server.Handler)
        result=handler._project_api('GET',self.pid,['storyboard'],{})
        self.assertTrue(result['stale']); self.assertTrue(result['needs_rebuild'])
        with self.assertRaisesRegex(ValueError,'重新生成'):
            self.sb.merge(self.pid,'c01')

    def test_longer_frame_and_repeated_merge(self):
        self.video()
        self.sb.build(self.pid)
        store.update(self.pid,lambda d:d['storyboard']['cells'][1].update(end=3,frame_at=2.8))
        c=self.sb.merge(self.pid,'c01')['storyboard']['cells'][0]
        self.assertEqual(c['frame_at'],2.8)
        self.assertEqual(len(self.sb.merge(self.pid,'c01')['storyboard']['cells']),8)

    def test_clear_source_discards_deleted_storyboard(self):
        store.update(self.pid,lambda d:d.update(source={'file':'source/source.mp4'},storyboard={'cells':[{}],'sheets':[{}]}))
        data=pipeline.clear_source(self.pid)
        self.assertIsNone(data.get('storyboard'))
        with patch.object(self.sb,'build',return_value={'storyboard':{'cells':[{}],'sheets':[{}]}}) as build:
            pipeline._build_analysis_storyboard(self.pid)
        build.assert_called_once_with(self.pid)

    def test_write_endpoint_requires_page_version_and_actions(self):
        with patch.object(runtime_env,'apply'):
            import server
        handler=object.__new__(server.Handler); handler.headers={}
        with self.assertRaises(server.ApiError) as err:
            handler._api('POST',['projects',self.pid,'storyboard'],{})
        self.assertEqual(err.exception.status,409)
        with patch.object(server,'build_status',return_value={'restart_pending':False,'build':'b','server_build':'s','web_build':'w'}):
            handler.headers={'X-Client-Build':'b'}
            with patch.object(handler,'_body_json',return_value={'action':'restore'}),patch.object(self.sb,'build',return_value={'ok':True}) as build:
                self.assertEqual(handler._api('POST',['projects',self.pid,'storyboard'],{}),{'ok':True})
                build.assert_called_once_with(self.pid)

    def test_run_analysis_builds_after_pack_and_keeps_breakdown_on_failure(self):
        self.video()
        overview={'passages':[dict(id='B1',start=0,end=12)],'characters':[dict(id='C1',name='父亲')]}
        meta=dict(model='mock',seconds=0,usage={})
        real_build=self.sb.build
        for fail in (True,False):
            def after_pack(pid):
                self.assertEqual(store.load(pid)['analysis']['steps']['pack']['status'],'done')
                if fail: raise ValueError('截帧失败')
                return real_build(pid)
            with patch.object(config,'provider_settings',return_value={'model':'mock'}), \
                    patch.object(pipeline,'_grids',return_value=[]), \
                    patch.object(media,'hypit_boundaries',return_value=[]), \
                    patch.object(gateway,'reason',side_effect=[(overview,meta),({'shots':[detail(0,12,['C1'])]},meta)]), \
                    patch.object(self.sb,'build',side_effect=after_pack):
                pipeline._run_analysis(self.pid,'deepseek',skip_asr=True)
            data=store.load(self.pid)
            self.assertEqual(data['analysis']['status'],'done')
            self.assertEqual(data['breakdown']['characters'][0]['id'],'C1')
            self.assertEqual(data['analysis']['steps']['storyboard']['status'],'failed' if fail else 'done')
        self.assertEqual(len(data['storyboard']['cells']),2)

    def test_pack_hook_failure_isolated_and_existing_board_not_overwritten(self):
        self.assertIn('storyboard',pipeline.STEP_META)
        src=(ROOT/'console/pipeline.py').read_text()
        self.assertLess(src.index('_set_step(pid, "pack", status="done"'), src.index('_build_analysis_storyboard(pid)'))
        store.update(self.pid,lambda d:d.update(breakdown={'shots':[]},analysis={'status':'done','steps':{'pack':{'status':'done'}}}))
        with patch.object(self.sb,'build',side_effect=ValueError('截帧失败')):
            pipeline._build_analysis_storyboard(self.pid)
        data=store.load(self.pid)
        self.assertEqual(data['analysis']['status'],'done'); self.assertIsNotNone(data['breakdown'])
        self.assertEqual(data['analysis']['steps']['storyboard']['status'],'failed')
        with patch.object(self.sb,'build',return_value={'storyboard':{'cells':[{}],'sheets':[{}]}}):
            pipeline._build_analysis_storyboard(self.pid)
        self.assertEqual(store.load(self.pid)['analysis']['steps']['storyboard']['output'],{'cells':1,'sheets':1})
        store.update(self.pid,lambda d:d.update(storyboard={'source_sig':'old','manual':True}))
        with patch.object(self.sb,'build') as build:
            pipeline._build_analysis_storyboard(self.pid)
            build.assert_not_called()


if __name__=='__main__': unittest.main()
