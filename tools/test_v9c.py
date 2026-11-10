"""V9c settings and frozen image contracts; synthetic files and offline transports."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image
from console import config, gateway, jimmy_bridge, store, storyboard_images
from console import storyboard_edits as edits
from console.gateway import routes
from console.gateway.adapters import official
from tools.test_v2_segment_plan import SegmentFixture

OPTIONS = [('official:gpt-image-2', 'auto'), ('official:gpt-image-2', 'high'),
           ('official:gpt-image-2.6-sunburst', 'high'), ('third_party:gpt-image-2', 'auto')]


class V9cSettingsTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory(dir=config.ROOT/'runtime/temp')))
        self.file = self.folder/'settings.conf'
        self.enterContext(patch.object(config, 'ENV_FILE', self.file))
        self.enterContext(patch.dict(os.environ, {}, clear=True))

    def test_four_options_save_load_and_public_view(self):
        for route, quality in OPTIONS:
            with self.subTest(route=route, quality=quality):
                updates = {'ROUTE_'+s.upper(): route for s in ('storyboard_image', 'camera_image')}
                updates.update({s.upper()+'_QUALITY': quality for s in ('storyboard_image', 'camera_image')})
                config.save(updates)
                view = config.public_view()
                for key, value in updates.items(): self.assertEqual(view.get(key), value)

    def test_legacy_default_auto_and_read_does_not_write(self):
        self.file.write_text('OPENAI_API_KEY=offline\n')
        before = self.file.read_bytes()
        values = config.load()
        for step in ('storyboard_image', 'camera_image'):
            self.assertEqual(values.get(step.upper()+'_QUALITY'), 'auto')
            self.assertEqual(routes.resolve(step, values=values), {'channel':'official', 'model':'gpt-image-2'})
        self.assertEqual(self.file.read_bytes(), before)

    def test_legacy_custom_model_survives_unrelated_save(self):
        self.file.write_text('OPENAI_API_KEY=offline\nOPENAI_IMAGE_MODEL=custom-image-id\n'
                             'ROUTE_STORYBOARD_IMAGE=official:gpt-image-2\nROUTE_CAMERA_IMAGE=official\n')
        for action in (lambda: None, lambda: config.save({'PORT':'8765'})):
            action()
            for step in ('storyboard_image', 'camera_image'):
                self.assertEqual(routes.resolve(step)['model'], 'custom-image-id')
        self.assertEqual(config.load()['OPENAI_IMAGE_MODEL'], 'custom-image-id')

    def test_explicit_choice_does_not_revert_to_legacy_custom_model(self):
        self.file.write_text('OPENAI_API_KEY=offline\nOPENAI_IMAGE_MODEL=custom-image-id\n')
        config.save({'ROUTE_STORYBOARD_IMAGE':'official:gpt-image-2', 'STORYBOARD_IMAGE_QUALITY':'high'})
        self.assertEqual(routes.resolve('storyboard_image')['model'], 'gpt-image-2')
        self.assertEqual(routes.resolve('camera_image')['model'], 'custom-image-id')

    def test_invalid_quality_or_channel_rejected_without_writing(self):
        config.save({})
        before = self.file.read_bytes()
        for key, value in [('STORYBOARD_IMAGE_QUALITY','low'), ('CAMERA_IMAGE_QUALITY',''),
                           ('CAMERA_IMAGE_QUALITY','HIGH'), ('ROUTE_STORYBOARD_IMAGE','invalid:model')]:
            with self.subTest(key=key,value=value), self.assertRaises(ValueError): config.save({key:value})
            self.assertEqual(self.file.read_bytes(), before)

    def test_step_settings_can_differ(self):
        config.save({'OPENAI_API_KEY':'offline','ROUTE_STORYBOARD_IMAGE':OPTIONS[2][0],
                     'STORYBOARD_IMAGE_QUALITY':'high','ROUTE_CAMERA_IMAGE':OPTIONS[0][0], 'CAMERA_IMAGE_QUALITY':'auto'})
        self.assertEqual(routes.resolve('storyboard_image')['model'],'gpt-image-2.6-sunburst')
        self.assertEqual(routes.resolve('camera_image')['model'],'gpt-image-2')


class V9cPreviewTests(SegmentFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.values = {**config.DEFAULTS, 'OPENAI_API_KEY':'offline'}
        config.load.side_effect = lambda: dict(self.values)
        self.enterContext(patch.object(config,'_parse',side_effect=AssertionError('no secret reads')))
        self.enterContext(patch('urllib.request.urlopen',side_effect=AssertionError('no network')))
        self.enterContext(patch.object(jimmy_bridge,'_call',side_effect=self.prepare_third_party))
        self.generated()

    def prepare_third_party(self, args, cwd):
        job = Path(args[args.index('--job')+1])
        folder = Path(args[args.index('--run')+1]); folder.mkdir(parents=True,exist_ok=True)
        store.atomic_write_json(folder/'image-job.preview.json',json.loads(job.read_text()))
        return 'offline'

    def choose(self, route, quality):
        for step in ('storyboard_image','camera_image'):
            self.values['ROUTE_'+step.upper()] = route
            self.values[step.upper()+'_QUALITY'] = quality

    def plan(self, sid='d01'):
        segment = next(s for s in store.load(self.pid)['segments_plan']['segments'] if s['id']==sid)
        answer = dict(summary='调整',remove_text=False,questions=[],cells=[dict(cell=c,changes='调整背景',keep='主体') for c in segment['cells']])
        with patch.object(gateway,'reason_checked',return_value=(answer,{})): edits.generate_plan(self.pid,sid,{})

    def complete_board(self):
        self.plan(); rec=edits.preview(self.pid,'d01',{})
        output=self.d/'synthetic.png';Image.new('RGB',(360,640),'navy').save(output)
        storyboard_images.complete(self.pid,rec,output)
        store.update(self.pid,lambda d:d['images'][rec['id']].update(state='completed'))

    def assert_frozen(self, rec, route, quality):
        channel, model = route.split(':',1)
        self.assertEqual(rec['channel'],channel)
        self.assertEqual(rec['preview']['model'],model)
        self.assertEqual(rec['preview']['quality'],quality if channel=='official' else 'auto')
        path=self.d/rec['run_dir']/'image-job.preview.json'
        self.assertEqual(rec['preview'],json.loads(path.read_text()))
        self.assertEqual(rec['preview_sha256'],store.sha256_file(path))
        self.assertNotIn('complexity',rec['preview'])
        self.assertNotIn('免费',rec['summary'])

    def test_whole_storyboard_all_options(self):
        self.plan()
        for route,quality in OPTIONS:
            with self.subTest(route=route,quality=quality):
                self.choose(route,quality);self.assert_frozen(edits.preview(self.pid,'d01',{}),route,quality)

    def test_single_cell_all_options(self):
        self.complete_board()
        answer=dict(changes='调整光线',keep='主体',why='按要求调整')
        for route,quality in OPTIONS:
            with self.subTest(route=route,quality=quality),patch.object(gateway,'reason_checked',return_value=(answer,{})):
                self.choose(route,quality)
                self.assert_frozen(edits.preview(self.pid,'d01',{'cell_id':'c01','request':'调整光线'}),route,quality)

    def test_multiple_cells_all_options(self):
        self.complete_board()
        answer=dict(why='按要求调整',cells=[dict(cell=c,changes='调整光线',keep='主体') for c in ('c01','c02')])
        for route,quality in OPTIONS:
            with self.subTest(route=route,quality=quality),patch.object(gateway,'reason_checked',return_value=(answer,{})):
                self.choose(route,quality)
                self.assert_frozen(edits.preview(self.pid,'d01',{'cells':['c01','c02'],'request':'调整光线'}),route,quality)

    def test_camera_image_all_options(self):
        for route,quality in OPTIONS:
            with self.subTest(route=route,quality=quality):
                self.choose(route,quality)
                rec=gateway.image_preview('camera_image',self.pid,'主镜头图',['father'],'9:16',None,'2k',None)
                self.assert_frozen(rec,route,quality)

    def test_project_route_priority_with_settings_quality(self):
        self.choose(OPTIONS[0][0],'high');self.plan()
        store.update(self.pid,lambda d:d.update(routes={'storyboard_image':'official:custom-project-image'}))
        self.assert_frozen(edits.preview(self.pid,'d01',{}),'official:custom-project-image','high')

    def test_actual_previews_use_independent_step_quality(self):
        self.plan()
        self.values.update(ROUTE_STORYBOARD_IMAGE=OPTIONS[2][0],STORYBOARD_IMAGE_QUALITY='high',CAMERA_IMAGE_QUALITY='auto')
        self.assert_frozen(edits.preview(self.pid,'d01',{}),OPTIONS[2][0],'high')
        camera=gateway.image_preview('camera_image',self.pid,'主镜头图',['father'],'9:16',None,'2k',None)
        self.assert_frozen(camera,OPTIONS[0][0],'auto')

    def test_project_explicit_quality_priority_and_invalid_quality(self):
        self.plan()
        store.update(self.pid,lambda d:d.update(routes={'storyboard_image':{'channel':'official','model':'gpt-image-2','quality':'high'}}))
        self.assert_frozen(edits.preview(self.pid,'d01',{}),OPTIONS[1][0],'high')
        store.update(self.pid,lambda d:d['routes']['storyboard_image'].update(quality='invalid'))
        with self.assertRaises(ValueError):edits.preview(self.pid,'d01',{})

    def test_confirm_uses_frozen_values_after_settings_change(self):
        self.choose(*OPTIONS[2]);self.plan();rec=edits.preview(self.pid,'d01',{})
        self.choose(*OPTIONS[0]);self.values['CAMERA_IMAGE_QUALITY']='high'
        submitted=[]
        def generate(preview,output,**kw):
            kw['before_send'](); submitted.append(copy.deepcopy(preview));output.write_bytes(b'fake');return {}
        with patch.object(jimmy_bridge.openai_image,'generate',side_effect=generate),patch.object(jimmy_bridge,'_image_done'), \
             patch.object(jimmy_bridge.jobs,'start',side_effect=lambda pid,key,fn,label:fn()):
            gateway.image_confirm(self.pid,rec['id'])
        self.assertEqual(submitted,[rec['preview']])
        self.assertEqual(submitted[0]['quality'],'high')
        self.assertEqual(submitted[0]['model'],'gpt-image-2.6-sunburst')

    def test_tampered_quality_rejected_before_confirm(self):
        self.plan();rec=edits.preview(self.pid,'d01',{})
        path=self.d/rec['run_dir']/'image-job.preview.json'
        value=json.loads(path.read_text());value['quality']='high';store.atomic_write_json(path,value)
        with patch.object(jimmy_bridge.jobs,'start') as start,self.assertRaisesRegex(Exception,'哈希'):
            gateway.image_confirm(self.pid,rec['id'])
        start.assert_not_called()

    def test_default_official_multipart_matches_round21_including_order(self):
        self.plan();rec=edits.preview(self.pid,'d01',{})
        spec=importlib.util.spec_from_file_location('console._v9c_old_official',config.ROOT/'tools/fixtures/round21/openai_image_before.py')
        old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        with patch('uuid.uuid4',return_value=SimpleNamespace(hex='fixed-boundary')):
            current=official._multipart(rec['preview']); baseline=old._multipart(rec['preview'])
        self.assertEqual(current,baseline)
        body=current[0]
        fields=[body.index(('name="'+key+'"').encode()) for key in ['model','prompt','image[]','size','quality','n']]
        self.assertEqual(fields,sorted(fields))
        self.assertIn(b'name="quality"\r\n\r\nauto\r\n',body)

    def test_third_party_bytes_ignore_quality_settings(self):
        self.choose(OPTIONS[3][0],'auto')
        with patch.object(store,'new_id',return_value='img-fixed'):
            first=gateway.image_preview('camera_image',self.pid,'固定',['father'],'9:16',None,'2k',None)
            path=self.d/'image-runs/img-fixed.job.json';before=path.read_bytes()
            self.values['CAMERA_IMAGE_QUALITY']='high'
            second=gateway.image_preview('camera_image',self.pid,'固定',['father'],'9:16',None,'2k',None)
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(first['preview'],second['preview'])

    def test_default_preview_bytes_match_original_before_gateway(self):
        spec=importlib.util.spec_from_file_location('console._v9c_old_bridge',config.ROOT/'tools/fixtures/round21/jimmy_bridge_before.py')
        old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        with patch.object(store,'new_id',return_value='img-byte-fixed'):
            before=old.image_preview(self.pid,'同一提示词',['father'],'9:16','gpt-image-2','2k',None,channel='official')
            path=self.d/before['run_dir']/'image-job.preview.json'
            frozen_bytes=path.read_bytes()
            after=gateway.image_preview('storyboard_image',self.pid,'同一提示词',['father'],'9:16',None,'2k',None)
        self.assertEqual(path.read_bytes(),frozen_bytes)
        with patch('uuid.uuid4',return_value=SimpleNamespace(hex='fixed-boundary')):
            self.assertEqual(official._multipart(after['preview']),official._multipart(before['preview']))


class V9cBatchTests(unittest.TestCase):
    def setUp(self):
        from tools import test_v9b
        from console import fission_dashboard
        self.dashboard = fission_dashboard
        self.case = test_v9b.V9bTests('test_image_confirmation_bounded_live_tasks_and_duplicate_token')
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.values = config.load.return_value

    def test_batch_freezes_all_options_and_token_contains_model_quality(self):
        for number,(route,quality) in enumerate(OPTIONS,1):
            self.values.update(ROUTE_STORYBOARD_IMAGE=route,STORYBOARD_IMAGE_QUALITY=quality)
            pid=self.case.child(number)
            with patch.object(jimmy_bridge,'_call',side_effect=lambda args,cwd:V9cPreviewTests.prepare_third_party(None,args,cwd)):
                rec=self.case.board_preview(pid)
            pending=self.dashboard.view(self.case.master)['pending']['images']
            item=next(i for i in pending['items'] if i['project_id']==pid)
            self.assertEqual((item.get('model'),item.get('quality')),(route.split(':')[1],quality))
            token=pending['token']
            store.update(pid,lambda d:d['images'][rec['id']]['preview'].update(quality='changed'))
            self.assertNotEqual(self.dashboard.view(self.case.master)['pending']['images']['token'],token)

    def test_settings_change_keeps_batch_token_and_frozen_confirm(self):
        self.values.update(ROUTE_STORYBOARD_IMAGE=OPTIONS[2][0],STORYBOARD_IMAGE_QUALITY='high')
        pid=self.case.child();rec=self.case.board_preview(pid)
        token=self.dashboard.view(self.case.master)['pending']['images']['token']
        self.values.update(ROUTE_STORYBOARD_IMAGE=OPTIONS[0][0],STORYBOARD_IMAGE_QUALITY='auto')
        config.ENV_FILE.write_text('different settings metadata')
        self.assertEqual(self.dashboard.view(self.case.master)['pending']['images']['token'],token)
        self.dashboard.confirm(self.case.master,'images',{'token':token})
        self.case.paid_image.assert_called_once_with(pid,rec['id'])
        self.assertEqual(store.load(pid)['images'][rec['id']]['preview']['quality'],'high')

    def test_dashboard_board_annotation_comes_from_frozen_image(self):
        self.values.update(ROUTE_STORYBOARD_IMAGE=OPTIONS[2][0],STORYBOARD_IMAGE_QUALITY='high')
        pid=self.case.child();self.case.boards(pid)
        self.values.update(ROUTE_STORYBOARD_IMAGE=OPTIONS[0][0],STORYBOARD_IMAGE_QUALITY='auto')
        boards=self.dashboard.view(self.case.master)['batches'][0]['items'][0]['boards']
        self.assertTrue(boards)
        self.assertTrue(all(b.get('model')=='gpt-image-2.6-sunburst' and b.get('quality')=='high' for b in boards))

    def test_fission_prepare_entry_all_options_stops_at_confirmation(self):
        from console import fission
        for number,(route,quality) in enumerate(OPTIONS,1):
            pid=self.case.child(number)
            self.values.update(ROUTE_STORYBOARD_IMAGE=route,STORYBOARD_IMAGE_QUALITY=quality)
            segment=store.load(pid)['segments_plan']['segments'][0]
            answer=dict(summary='调整',remove_text=False,questions=[],cells=[dict(cell=c,changes='调整背景',keep='主体') for c in segment['cells']])
            with patch.object(gateway,'reason_checked',return_value=(answer,{})), \
                 patch.object(jimmy_bridge,'_call',side_effect=lambda args,cwd:V9cPreviewTests.prepare_third_party(None,args,cwd)):
                fission.prepare_segment(pid,segment['id'])
            data=store.load(pid);state=data['fission_preparation']['segments'][segment['id']]
            self.assertEqual(state['status'],'pending_confirmation',state)
            rec=data['images'][state['image_id']]
            self.assertEqual((rec['preview']['model'],rec['preview']['quality']),(route.split(':')[1],quality))
        self.case.paid_image.assert_not_called()

    def test_dashboard_partial_redraw_labels_follow_each_cells_origin(self):
        pid=self.case.child();self.case.boards(pid)
        def versions(data):
            state=data['segment_boards']['segments']['d02'];base=state['versions'][0]
            data['images']['single']={'channel':'official','preview':{'model':'gpt-image-2.6-sunburst','quality':'high'}}
            data['images']['multi']={'channel':'official','preview':{'model':'gpt-image-2','quality':'high'}}
            state['versions'] += [{**base,'id':'v2','image_id':'single','from':'cell:c04','base_version':base['id']},
                                  {**base,'id':'v3','image_id':'multi','from':'cells:c05,c06','selected_cells':['c05','c06'],'base_version':'v2'}]
            state['current']='v3'
        store.update(pid,versions)
        boards=self.dashboard.view(self.case.master)['batches'][0]['items'][0]['boards']
        info={b['cell_id']:(b['model'],b['quality']) for b in boards}
        self.assertEqual(info['c04'],('gpt-image-2.6-sunburst','high'))
        self.assertEqual(info['c05'],('gpt-image-2','high'))
        self.assertEqual(info['c06'],('gpt-image-2','high'))
        self.assertEqual(info['c07'],('gpt-image-2','auto'))
        self.assertEqual(info['c08'],('gpt-image-2','auto'))
        store.update(pid,lambda d:d['segment_boards']['segments']['d02']['versions'][-1].update(base_version='v3'))
        info={b['cell_id']:b for b in self.dashboard.view(self.case.master)['batches'][0]['items'][0]['boards']}
        self.assertIsNone(info['c08']['model'])


class V9cRedrawAndCameraTests(unittest.TestCase):
    def test_camera_http_default_uses_step_route_and_explicit_override_survives(self):
        import importlib
        from console import runtime_env
        with patch.object(runtime_env,'apply'):
            server=importlib.import_module('server')
        handler=server.Handler.__new__(server.Handler)
        for prefix in ('camera:C1:S1','scene-camera:S1'):
            body=dict(for_task=prefix,route_selected=False,channel='official',model='old-status-model')
            with patch.object(handler,'_body_json',return_value=body),patch.object(gateway,'image_preview',return_value={}) as preview:
                handler._project_api('POST','synthetic',['images','preview'],{})
                self.assertIsNone(preview.call_args.kwargs['override'])
                body['route_selected']=True
                handler._project_api('POST','synthetic',['images','preview'],{})
                self.assertEqual(preview.call_args.kwargs['override'],{'channel':'official','model':'old-status-model'})

    def test_plan_redraw_freezes_each_option(self):
        from tools import test_v6
        for route,quality in OPTIONS:
            with self.subTest(route=route,quality=quality):
                case=test_v6.V6Tests('test_own_redraw_resigns_prompt_then_manual_redraw_invalidates')
                try:
                    case.setUp()
                    config.load.return_value.update(OPENAI_API_KEY='offline',ROUTE_STORYBOARD_IMAGE=route,STORYBOARD_IMAGE_QUALITY=quality)
                    plan=case.plan();case.fix().apply(case.pid,'d1','r1',{'plan_id':plan['id']})
                    answer=dict(changes='让目标外观明确入镜',keep='构图',why='修复外观')
                    with patch.object(gateway,'reason_checked',return_value=(answer,{})), \
                         patch.object(jimmy_bridge,'_call',side_effect=lambda args,cwd:V9cPreviewTests.prepare_third_party(None,args,cwd)):
                        rec=edits.preview(case.pid,'d1',{'cell_id':'c01','request':plan['redraw'][0]['request'],
                                                       'fix_plan':{'rev':'r1','id':plan['id']}})
                    self.assertEqual((rec['preview']['model'],rec['preview']['quality']),(route.split(':')[1],quality))
                    self.assertEqual(rec['storyboard_context']['fix_plan']['id'],plan['id'])
                    self.assertEqual(rec['state'],'pending_confirmation')
                finally:case.doCleanups()

    def test_scene_camera_with_real_reference_validation(self):
        from tools import test_scene_camera
        case=test_scene_camera.SceneCameraTests('test_image_preview_anchor_metadata_and_validation')
        try:
            case.setUp()
            case.data['assets'].append(dict(id='scene-frame',file='assets/scene-frame.jpg',type='image',
                                           roles=['scene'],camera_scene='S2',frame_at=8.0))
            case.persist()
            for route,quality in OPTIONS:
                config.load.return_value.update(OPENAI_API_KEY='offline',ROUTE_CAMERA_IMAGE=route,CAMERA_IMAGE_QUALITY=quality)
                with patch.object(jimmy_bridge,'_call',side_effect=lambda args,cwd:V9cPreviewTests.prepare_third_party(None,args,cwd)):
                    rec=gateway.image_preview('camera_image',case.pid,'主镜头图',
                        [case.portrait,case.product,case.camera,'scene-frame'],'9:16',None,'2k','camera:C1:S2')
                self.assertEqual((rec['preview']['model'],rec['preview']['quality']),(route.split(':')[1],quality))
                self.assertEqual(rec['camera_scene'],'S2')
        finally:case.doCleanups()


if __name__ == '__main__': unittest.main()
