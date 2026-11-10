"""Director video transport cases, imported by test_director_sb3 (all remote I/O mocked)."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from console import config, gateway, jimmy_bridge as bridge, jobs, media, runtime_env, store, director, segment_plan, segment_boards, storyboard_edits

ROOT = Path(__file__).resolve().parents[1]


class DirectorBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT/'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.temp))
        self.enterContext(patch.object(config, 'load', return_value={**config.DEFAULTS, 'JIMMY_API_KEY':'mock'}))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('no secret reads')))
        self.enterContext(patch.object(config, 'jimmy_configured', return_value=True))
        self.enterContext(patch.object(config, 'jimmy_env_file', return_value=self.temp/'mock.conf'))
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('no live network')))
        self.cli = self.enterContext(patch.object(bridge, '_call', side_effect=self.package))
        self.enterContext(patch.object(bridge, 'replacement_check', side_effect=AssertionError('legacy replacement check')))
        self.enterContext(patch.object(bridge, 'compose_prompt', side_effect=AssertionError('legacy prompt')))
        self.enterContext(patch.object(bridge.scene_plan, 'enabled', side_effect=AssertionError('legacy scene plan')))
        self.pid = store.create('SB3 video synthetic')['id']; self.d = store.project_dir(self.pid)
        for name in ('cell1','cell2','father','product','sheet'):
            Image.new('RGB',(90,160),'blue').save(self.d/'assets'/f'{name}.jpg')
        (self.d/'source/source.mp4').write_bytes(b'mock source video')
        self.segment = dict(id='d01',title='门廊',cells=['c01','c02'],reason='动作闭合',mode='video',mode_reason='动作重要',
                            images=['c01','C1','product'],start_state='站着',end_state='坐下',prompt='  完整提示词 图1 图2 图3 视频1。\nDo not change any words.  ',
                            what='父亲坐下', check=['身份一致'], replace_keep='Replace the father and product.',
                            timeline=[{'cells':['c01'],'text':'The father stands.'},{'cells':['c02'],'text':'The father sits.'}],
                            continuity='Keep his hands consistent.',sound='Quiet room tone.',forbid=['the original father'],
                            source_start=1.25,source_end=6.583,source_seconds=5.333,target_seconds=6,stretch=6/5.333,
                            duration=6,needs_rewrite=False,edited=False,warnings=[])
        store.update(self.pid,lambda d:d.update(
            source={'file':'source/source.mp4','probe':{'duration':10}},script={'tasks':[]},
            assets=[{'id':n,'file':f'assets/{n}.jpg','type':'image','roles':['reference']} for n in ('father','product')],
            breakdown={'characters':[{'id':'C1','name':'父亲'}],'overlays':[{'start':2,'end':3,'kind':'caption','bbox':[.1,.7,.9,.8],'text':'old'}]},
            replacements={'characters':[{'source_id':'C1','action':'replace','asset_id':'father'}],'product':{'action':'replace','asset_id':'product'}},
            storyboard={'cells':[{'id':f'c0{i}','index':i,'start':1.25 if i==1 else 3,'end':3 if i==1 else 6.583,'file':f'assets/cell{i}.jpg','characters':['C1'],'product':'商品'} for i in (1,2)],'sheets':[{'id':'s01','cells':['c01','c02'],'file':'assets/sheet.jpg'}]},
            storyboard_edits={'sheets':{'s01':{'current':'v1','versions':[{'id':'v1','file':'assets/sheet.jpg','cells':{'c01':'assets/cell1.jpg','c02':'assets/cell2.jpg'}}],'plan':{'remove_text':True}}}},
            director={'format_version':3,'summary':'一段','segments':[copy.deepcopy(self.segment)],'questions':[]}))
        self.sync_plan()
        def cut(src,start,length,out,**kw):
            out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(b'mock silent masked video')
        self.cut = self.enterContext(patch.object(media,'cut_clip',side_effect=cut))
        def stretch(src,source_seconds,target_seconds,out):
            out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(b'mock stretched silent video')
            return {'duration':target_seconds,'fps':30,'audio_stream_count':0,'indexed_frames':target_seconds*30,'decoded_frames':target_seconds*30}
        self.stretch = self.enterContext(patch.object(media,'stretch_clip',side_effect=stretch))
        self.enterContext(patch.object(media,'probe',return_value={'duration':5.333,'audio_stream_count':0}))
        self.residue = self.enterContext(patch.object(media,'caption_residue',return_value={'status':'clear','suspected':[]}))

    def package(self,args,cwd,**kw):
        if 'prepare' not in args:return ''
        plan=json.loads(Path(args[args.index('--plan')+1]).read_text());out=Path(args[args.index('--output')+1]);out.mkdir(parents=True)
        task=plan['tasks'][0]
        task={**task,'model':bridge.MINI_VIDEO if task['reference_video_ids'] else bridge.MINI}
        job={'job_id':plan['job_id'],'tasks':[task],'media':plan['media']}
        raw=json.dumps(job,ensure_ascii=False).encode()
        (out/'jimmy-job.preview.json').write_bytes(raw)
        store.atomic_write_json(out/'jimmy-handoff.json',{'job_sha256':hashlib.sha256(raw).hexdigest(),'state':'ready_for_confirmation'})
        return ''

    def change(self,**kw):
        data=store.update(self.pid,lambda d:d['director']['segments'][0].update(kw))
        if 'mode' in kw or ('images' in kw and len(kw['images'])<=4):self.sync_plan()
        return store.load(self.pid)

    def sync_plan(self):
        data=store.load(self.pid);row=data['director']['segments'][0]
        raw={'segments':[dict(id='d01',cells=['c01','c02'],mode=row['mode'],refs=[key for key in row['images'] if key not in row['cells']],gen_images=row['images'])]}
        data['segments_plan']=segment_plan.validate_plan(raw,data,self.d)
        data['segments_plan']['inputs_sig']=segment_plan.inputs_sig(data,self.d)
        data['segment_boards']={'segments':segment_boards.build_sources(data,self.d)}
        state=segment_boards.state_for(data,'d01');sig=storyboard_edits.board_sig(data,'d01')
        state.update(current='v1',storyboard_sig=sig,plan={'remove_text':True},versions=[dict(
            id='v1',file='assets/sheet.jpg',cells={'c01':'assets/cell1.jpg','c02':'assets/cell2.jpg'},storyboard_sig=sig)])
        row['gen_images']=list(row['images'])
        row['plan_sig']=segment_plan.segment_sig(data['segments_plan']['segments'][0])
        row['inputs_sig']=director.segment_inputs_sig(data,self.d,'d01')
        data['director']['inputs_sig']=director.inputs_sig(data,self.d)
        store.update(self.pid,lambda d:d.update(data))

    def test_task_finds_director_without_script(self):
        task=bridge._task(store.load(self.pid),'d01')
        self.assertEqual(task['kind'],'director');self.assertEqual(task['duration'],6)
        self.assertEqual(task['prompt'],self.segment['prompt'])

    def test_video_preview_exact_prompt_order_mute_mask_and_no_legacy(self):
        run=gateway.video_preview(self.pid,'d01')
        plan=json.loads((self.d/run['package']/'plan.json').read_text())
        self.assertEqual(plan['tasks'][0]['prompt'],self.segment['prompt'])
        self.assertEqual(run['kind'],'director');self.assertTrue(run['director_sig'])
        self.assertEqual([r['key'] for r in run['references']],['c01','C1','product'])
        self.assertEqual(self.cut.call_args.args[1:3],(1.25,5.333))
        self.assertTrue(self.cut.call_args.kwargs['mute']);self.assertTrue(self.cut.call_args.kwargs['masks'])
        self.stretch.assert_called_once()
        self.assertEqual(self.stretch.call_args.args[1:3],(5.333,6))
        self.assertEqual(self.residue.call_args.args[0],self.cut.call_args.args[3])
        self.assertEqual(self.stretch.call_args.args[0],self.cut.call_args.args[3])
        self.residue.assert_called_once();self.assertEqual(run['billing_seconds'],12)
        self.assertIn('参考视频 6s',run['billing_note'])
        self.assertEqual(run['stretch'],6/5.333);self.assertEqual(run['target_seconds'],6)
        self.assertEqual(run['source_seconds'],5.333)
        self.assertEqual(run['clip_processing']['decoded_frames'],180)
        self.assertEqual(run['clip_processing']['muted'],True)
        self.assertEqual(plan['tasks'][0]['duration'],6)
        # Frozen preview images survive subsequent reconstruction of the collage.
        first=Path(plan['media'][0]['path']);saved=first.read_bytes()
        Image.new('RGB',(90,160),'red').save(self.d/'assets/cell1.jpg')
        with self.assertRaisesRegex((ValueError,bridge.BridgeError),'第 04|第 05|第 06'):
            gateway.video_preview(self.pid,'d01')
        director.build_storyboard(self.pid,'d01')
        self.assertEqual(first.read_bytes(),saved)

    def test_image_preview_has_no_video_and_rewrite_or_missing_media_rejected(self):
        self.change(mode='image',prompt=self.segment['prompt'].replace(' 视频1',''))
        run=bridge.preview(self.pid,'d01')
        self.assertIsNone(run['clip']);self.assertFalse(run['preview']['tasks'][0]['reference_video_ids'])
        self.cut.assert_not_called();self.stretch.assert_not_called();self.residue.assert_not_called()
        self.change(needs_rewrite=True,structure_needs_rewrite=True)
        with self.assertRaisesRegex((ValueError,bridge.BridgeError),'回第 04 步（对照和修改），选中这一段，在“提示词”里点“重新写这一段”'):bridge.preview(self.pid,'d01')
        self.change(needs_rewrite=False,structure_needs_rewrite=False,prompt='a'*5001)
        with self.assertRaises((ValueError,bridge.BridgeError)):bridge.preview(self.pid,'d01')
        self.change(prompt='legal',images=['c01','C1','product','C1','product'])
        with self.assertRaises((ValueError,bridge.BridgeError)):bridge.preview(self.pid,'d01')
        self.change(images=['c01','C1']);(self.d/'assets/father.jpg').unlink()
        with self.assertRaises((ValueError,bridge.BridgeError)):bridge.preview(self.pid,'d01')

    def test_legacy_plan_without_timeline_cannot_create_a_preview(self):
        store.update(self.pid,lambda d:d['director'].pop('format_version'))
        with self.assertRaisesRegex(bridge.BridgeError,'全部写提示词'):
            bridge.preview(self.pid,'d01')
        self.cut.assert_not_called();self.stretch.assert_not_called();self.cli.assert_not_called()

    def test_confirm_stale_director_uses_frozen_preview_without_legacy_checks(self):
        run=bridge.preview(self.pid,'d01')
        self.change(prompt='Changed since preview')
        # Director changes are advisory; approved previews retain their exact original payload.
        with patch.object(bridge,'_start_generate') as submit:
            bridge.confirm(self.pid,'d01',run['rev'],run['job_sha256'])
            submit.assert_called_once()
        self.assertEqual(bridge._find_run(store.load(self.pid),'d01',run['rev'])['state'],'submitting')

    def test_confirm_rewrite_flag_is_only_new_director_gate(self):
        run=bridge.preview(self.pid,'d01');self.change(needs_rewrite=True,structure_needs_rewrite=True)
        with patch.object(bridge,'_start_generate') as submit:
            with self.assertRaisesRegex(bridge.BridgeError,'回第 04 步（对照和修改），选中这一段，在“提示词”里点“重新写这一段”'):bridge.confirm(self.pid,'d01',run['rev'],run['job_sha256'])
            submit.assert_not_called()

    def test_confirm_removed_segment_does_not_submit_orphaned_paid_job(self):
        run=bridge.preview(self.pid,'d01')
        store.update(self.pid,lambda d:d['director'].update(segments=[]))
        with patch.object(bridge,'_start_generate') as submit:
            with self.assertRaisesRegex(bridge.BridgeError,'这一段已被移除，请回到第 04 步（对照和修改）查看当前分段方案'):
                bridge.confirm(self.pid,'d01',run['rev'],run['job_sha256'])
            submit.assert_not_called()
        self.assertEqual(bridge._find_run(store.load(self.pid),'d01',run['rev'])['state'],'pending_confirmation')

    def test_refresh_resume_recover_and_refresh_all_do_not_schedule_motion_checks(self):
        run=bridge.preview(self.pid,'d01');run_dir=self.d/'jimmy-runs/mock';run_dir.mkdir(parents=True)
        result=run_dir/'result.mp4';result.write_bytes(b'mock result')
        store.update(self.pid,lambda d:d['runs']['d01'][0].update(run_dir='jimmy-runs/mock',state='running'))
        store.atomic_write_json(run_dir/'state.json',{'tasks':{'d01':{'phase':'completed','local_path':str(result),'jimmy_task_id':'remote-mock'}}})
        with patch.object(bridge,'_schedule_motion_check') as check,patch.object(media,'copy_similarity') as compare,patch.object(jobs,'start') as start:
            bridge.refresh(self.pid,'d01',run['rev'])
            self.assertEqual(store.load(self.pid)['runs']['d01'][0]['state'],'completed')
            check.assert_not_called();compare.assert_not_called()
            start.assert_not_called()
            self.assertNotIn('review',store.load(self.pid)['runs']['d01'][0])
            start.reset_mock()
            bridge.resume(self.pid,'d01',run['rev']);start.assert_called_once()
            self.assertEqual(start.call_args.args[1], f"gen:d01:{run['rev']}")
            bridge.recover(self.pid,'d01',run['rev'],'recovered-mock')
            self.assertIn('recover-task-id',self.cli.call_args.args[0])
            with patch.object(gateway,'video_refresh') as refresh:bridge.refresh_all(self.pid);refresh.assert_not_called()

    def test_director_routes_require_page_version_and_preserve_legacy_script(self):
        from console import director
        with patch.object(runtime_env,'apply'):
            import server
        handler=object.__new__(server.Handler)
        status=dict(restart_pending=False,build='sb3',server_build='s',web_build='w')
        body={'action':'prompt','segment_id':'d01','prompt':'New direct English brief.'}
        before=store.load(self.pid)['script']
        with patch.object(server,'build_status',return_value=status),patch.object(handler,'_body_json',return_value=body),patch.object(server,'project_view',side_effect=lambda d:d):
            for headers in ({},{'X-Client-Build':'old'}):
                handler.headers=headers
                with self.assertRaises(server.ApiError):handler._api('PUT',['projects',self.pid,'director'],{})
            handler.headers={'X-Client-Build':'sb3'}
            out=handler._api('PUT',['projects',self.pid,'director'],{})
            self.assertEqual(out['director']['segments'][0]['prompt'],body['prompt'])
            self.assertEqual(out['script'],before)
            with patch.object(director,'generate',return_value=out) as generate:
                body.clear();body['action']='generate'
                handler._api('POST',['projects',self.pid,'director'],{});generate.assert_called_once_with(self.pid)


