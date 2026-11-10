"""SB2 synthetic media, local FFmpeg and mocked reasoning/image transport only."""
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import config, gateway, media, store, storyboard


class StoryboardSB2Tests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {'PATH':str(ROOT/'runtime/ffmpeg')+os.pathsep+os.environ.get('PATH','')}))
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT/'runtime/temp')))
        self.enterContext(patch.object(config,'PROJECTS_DIR',self.tmp))
        self.enterContext(patch.object(config,'load',return_value={**config.DEFAULTS,'OPENAI_API_KEY':'mock-only'}))
        self.pid=store.create('SB2 synthetic')['id']; self.d=store.project_dir(self.pid)

    def fixture(self):
        from console import storyboard_edits
        self.edit=storyboard_edits
        folder=self.d/'analysis/storyboard'; folder.mkdir(parents=True)
        colors=['red','green','blue','yellow','purple','orange','cyan','pink','brown']
        cells=[]
        for i,color in enumerate(colors,1):
            file=f'analysis/storyboard/cell-{i:02d}.jpg'; Image.new('RGB',(90,160),color).save(self.d/file)
            cells.append(dict(id=f'c{i:02d}',index=i,start=i-1,end=i,file=file,characters=['C2','C1'] if i==1 else ['C1'],product='SOS 手环' if i<3 else '',action='动作',action_full='完整动作。后续',overlay='字幕'))
        sheets=storyboard.render_sheets(cells,folder)
        for name in ('father','son','new','watch'):
            Image.new('RGB',(50,70),'gray').save(self.d/f'assets/{name}.jpg')
        data=store.update(self.pid,lambda d:d.update(source={'file':'source/source.mp4'},breakdown={'characters':[{'id':'C1','name':'父亲'},{'id':'C2','name':'儿子'}]},assets=[dict(id=n,type='image',file=f'assets/{n}.jpg') for n in ('father','son','new','watch')],storyboard=dict(version=2,built_at='first',cells=cells,sheets=sheets),replacements={'characters':[dict(source_id='C1',action='replace',asset_id='father',description='白发父亲'),dict(source_id='C2',action='replace',asset_id='son',description='短发儿子'),dict(source_id='N1',action='add',name='医生',asset_id='new',description='白大褂')],'product':dict(action='replace',asset_id='watch',description='黑色圆形手表，戴在手腕上',story_checks=[dict(question='支持呼救吗',answer='yes',note='')])}))
        return data

    def valid(self,data):
        return dict(summary='换父子和手表',cells=[dict(cell=c['id'],changes='换成照片里的父亲' if i==0 else '',keep='机位、构图、姿势、场地和光线') for i,c in enumerate(data['storyboard']['cells'])],remove_text=True,questions=[])

    def plan(self):
        data=self.fixture(); plan=self.valid(data)
        with patch.object(gateway,'reason_checked',return_value=(plan,{})) as reason:
            self.edit.generate_plan(self.pid,'s01',{'request':'换父亲并去掉字幕'})
        self.assertEqual(reason.call_count,1)
        return store.load(self.pid)

    def test_product_normalization_overlap_and_actions(self):
        pack={'productMoments':[dict(start=1,end=2,product='SOS 手环'),dict(start=3,end=4,product=' sos手环 '),dict(start=5,end=6,product='SOS 手环'),dict(start=6,end=7,product='手机')], 'shots':[dict(start=0,end=2,characters=['C1'],product='乱写的商品',action='抬手。很长的后文'),dict(start=2,end=3,action='起身；后文'),dict(start=3,end=4,action='走路'),dict(start=4,end=5,action='不应出现') ]}
        self.assertEqual(storyboard.product_names(pack),{'name':'SOS 手环','variants':['SOS 手环',' sos手环 ','手机']})
        self.assertEqual(storyboard.cell_data(0,1,pack)['product'],'')
        c=storyboard.cell_data(0,5,pack)
        self.assertEqual(c['product'],'SOS 手环'); self.assertEqual(c['action'],'抬手 → 起身 → 走路')
        self.assertIn('很长的后文',c['action_full']); self.assertIn('不应出现',c['action_full'])
        self.assertEqual(len(storyboard.cell_data(0,1,{'shots':[dict(start=0,end=1,action='长'*90)]})['action']),40)
        self.assertEqual(storyboard.product_names({'productMoments':[{'product':' B '},{'product':'A'}]})['name'],' B ')

    def test_overlay_avoidance_fullest_preference_and_fully_blocked(self):
        pack={'shots':[dict(start=0,end=1,characters=['C1']),dict(start=1,end=2,characters=['C1','C2'])], 'overlays':[dict(start=1.5,end=1.9)]}
        at=storyboard.cell_data(0,2,pack)['frame_at']; self.assertTrue(1<=at<1.25)
        self.assertEqual(storyboard.cell_data(0,.2,{'overlays':[dict(start=0,end=.2)]})['frame_at'],.1)
        self.assertLess(storyboard.cell_data(0,2,{'overlays':[dict(start=1,end=1.9)]})['frame_at'],.75)

    def test_version_upgrade_status(self):
        data={'storyboard':{'version':1}}
        self.assertTrue(storyboard.status(data,self.d)['upgrade'])

    def test_material_defaults_and_template(self):
        data=self.fixture(); options=self.edit.material_options(data,'s01',self.d)
        self.assertEqual([m['key'] for m in options],['C2','C1','N1','product'])
        self.assertTrue(all(m['enabled'] for m in options))
        text=self.edit.request_template(data,'s01',{'C1':True,'C2':False,'N1':True,'product':True},self.d)
        self.assertIn('白发父亲',text); self.assertNotIn('短发儿子',text); self.assertIn('白大褂',text); self.assertIn('按真实用法',text)
        data['storyboard']['cells']=[{**c,'characters':[],'product':''} for c in data['storyboard']['cells']]
        self.assertEqual({m['key']:m['enabled'] for m in self.edit.material_options(data,'s01',self.d)},{'C1':False,'C2':False,'N1':True,'product':False})

    def test_plan_messages_validation_signature_and_log(self):
        data=self.fixture(); valid=self.valid(data)
        for result in (valid,{**valid,'cells':valid['cells'][:-1]},{**valid,'cells':valid['cells']+[dict(cell='c99',changes='',keep='')]},'not JSON',{**valid,'cells':valid['cells']+[valid['cells'][0]]}):
            with patch.object(gateway,'reason_checked',return_value=(result,{})) as reason:
                if result==valid:
                    self.edit.generate_plan(self.pid,'s01',{'request':'换父亲'})
                    messages=reason.call_args.args[1]; alltext=json.dumps(messages,ensure_ascii=False)
                    self.assertIn('完整动作',alltext);self.assertIn('支持呼救吗',alltext);self.assertIn('图2：C2 儿子的新照片',alltext)
                    self.assertEqual(reason.call_args.args[0],'storyboard_plan')
                else:
                    with self.assertRaisesRegex(ValueError,'模型给的方案不完整，请重试'):
                        self.edit.generate_plan(self.pid,'s01',{'request':'换父亲'})
        with patch.object(gateway,'reason_checked',return_value=(valid,{})) as reason:
            self.edit.generate_plan(self.pid,'s01',{'request':' '})
            reason.assert_called_once()
            self.assertIn('补充要求：无',json.dumps(reason.call_args.args[1],ensure_ascii=False))
        state=store.load(self.pid)['storyboard_edits']['sheets']['s01']; old=state['plan']['sig']
        state['plan']['cells'][0]['changes']='新的改法'
        self.edit.save(self.pid,'s01',{'plan':state['plan']})
        saved=store.load(self.pid); self.assertNotEqual(saved['storyboard_edits']['sheets']['s01']['plan']['sig'],old)
        self.assertTrue(saved['storyboard_edits']['sheets']['s01']['plan']['edited']); self.assertTrue(saved['log'])

    def test_preview_order_padding_prompts_and_no_submit(self):
        self.plan()
        from console import jimmy_bridge
        with patch.object(jimmy_bridge,'image_confirm') as paid,patch.object(gateway,'reason_checked') as reason:
            rec=self.edit.preview(self.pid,'s01',{})
            paid.assert_not_called();reason.assert_not_called()
        self.assertEqual(rec['for_task'],'storyboard:s01'); self.assertEqual(rec['channel'],'official');self.assertEqual(rec['preview']['size'],'1024x1536')
        refs=rec['storyboard_context']['references'];self.assertEqual([r['key'] for r in refs],['source','C2','C1','N1','product'])
        with Image.open(refs[0]['path']) as img:
            self.assertEqual(img.width*3,img.height*2);self.assertEqual(img.getpixel((0,img.height//2)),(255,255,255))
        prompt=rec['preview']['prompt'];self.assertIn('different person',prompt); self.assertIn('THE FATHER',prompt)
        for i in range(1,10):self.assertIn(f'Panel {i} (',prompt)
        self.assertIn('no change',prompt);self.assertIn('Remove all on-screen',prompt)
        data=store.load(self.pid);state=data['storyboard_edits']['sheets']['s01'];state['plan']['remove_text']=False;self.edit.save(self.pid,'s01',{'plan':state['plan']})
        self.assertIn('Keep the on-screen text',self.edit.preview(self.pid,'s01',{})['preview']['prompt'])

    def test_cut_recompose_cell_only_versions_and_use(self):
        from console import jimmy_bridge
        self.plan();rec=self.edit.preview(self.pid,'s01',{})
        ctx=rec['storyboard_context']; pad=ctx['padding']; w,h=ctx['sheet_size']; colors=[(200,0,0),(0,180,0),(0,0,190),(190,190,0),(180,0,180),(190,100,0),(0,190,190),(210,80,100),(80,60,40)]
        clean=Image.new('RGB',(w,h),'white')
        from PIL import ImageDraw
        draw=ImageDraw.Draw(clean)
        # Simulated model moves internal gutters by 1px; centers remain in their panels.
        for i,color in enumerate(colors):
            x=(i%3)*96; y=(i//3)*166
            draw.rectangle((x+(1 if i%3 else 0),y+(1 if i//3 else 0),x+89,y+159),fill=color)
        out=self.d/'image-runs/fake.png';out.parent.mkdir(exist_ok=True)
        canvas=Image.new('RGB',tuple(pad['size']),'white');canvas.paste(clean,(pad['left'],pad['top']));canvas.save(out)
        jimmy_bridge._image_done(self.pid,rec['id'],out)
        state=store.load(self.pid)['storyboard_edits']['sheets']['s01']; version=state['versions'][0]
        self.assertEqual(state['current'],'v1');self.assertEqual(version['from'],'full')
        for i,file in enumerate(version['cells'].values()):
            with Image.open(self.d/file) as tile:self.assertLess(sum(abs(a-b) for a,b in zip(tile.getpixel((45,80)),colors[i])),15)
        with Image.open(self.d/version['clean_file']) as a,Image.open(self.d/version['file']) as b:
            self.assertEqual(a.size,(w,h)); self.assertIsNotNone(ImageChops.difference(a.convert('RGB'),b.convert('RGB')).getbbox())
        before={k:(self.d/f).read_bytes() for k,f in version['cells'].items()}
        with patch.object(gateway,'reason_checked',return_value=({'changes':'按本次要求调整画面','keep':'','why':'已整理这一镜的改法'},{})):
            cellrec=self.edit.preview(self.pid,'s01',{'cell_id':'c05','request':'换成医生，商品手表要露出'})
        self.assertEqual(cellrec['for_task'],'storyboard-cell:c05')
        self.assertEqual([r['key'] for r in cellrec['storyboard_context']['references']],['current','C1','N1','product'])
        one=Image.new('RGB',tuple(cellrec['storyboard_context']['padding']['size']),'white');one.paste(Image.new('RGB',(90,160),'black'),(cellrec['storyboard_context']['padding']['left'],0));one.save(out)
        jimmy_bridge._image_done(self.pid,cellrec['id'],out)
        state=store.load(self.pid)['storyboard_edits']['sheets']['s01'];v2=state['versions'][-1]
        self.assertEqual(v2['from'],'cell:c05');self.assertEqual(state['current'],'v2')
        for cid,file in v2['cells'].items():
            if cid!='c05':self.assertEqual((self.d/file).read_bytes(),before[cid])
        self.edit.use_version(self.pid,'s01','v1');self.assertEqual(store.load(self.pid)['storyboard_edits']['sheets']['s01']['current'],'v1')
        jimmy_bridge._image_done(self.pid,cellrec['id'],out)
        self.assertEqual(len(store.load(self.pid)['storyboard_edits']['sheets']['s01']['versions']),2)

    def test_warning_conditions_and_stale_preview(self):
        data=self.plan();state=data['storyboard_edits']['sheets']['s01']; state['versions']=[dict(id='v1',plan_sig=state['plan']['sig'])];state['current']='v1'
        store.update(self.pid,lambda d:d.update(storyboard_edits=data['storyboard_edits']))
        self.assertEqual(self.edit.warnings(store.load(self.pid),'s01',self.d),[])
        store.update(self.pid,lambda d:d['replacements']['product'].update(description='换了'))
        self.assertIn('选角或商品改过', ''.join(self.edit.warnings(store.load(self.pid),'s01',self.d)))
        state['plan']['cells'][0]['changes']='新方案';self.edit.save(self.pid,'s01',{'plan':state['plan']})
        self.assertIn('改图方案改过', ''.join(self.edit.warnings(store.load(self.pid),'s01',self.d)))
        rec=self.edit.preview(self.pid,'s01',{})
        store.update(self.pid,lambda d:d['storyboard'].update(built_at='rebuilt'))
        self.assertIn('原片分镜变了', ''.join(self.edit.warnings(store.load(self.pid),'s01',self.d)))
        with self.assertRaisesRegex(ValueError,'重新生成'):self.edit.verify_preview(self.pid,rec)

    def test_local_voice_transcode_cleanup_and_failures(self):
        from console import voice_input, asr
        audio=self.tmp/'tone.webm'
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','sine=frequency=440:duration=0.2','-c:a','libopus',str(audio)],check=True)
        def transcribe(path,**kw):
            with wave.open(str(path)) as wav:self.assertEqual((wav.getframerate(),wav.getnchannels(),wav.getsampwidth()),(16000,1,2));self.assertGreater(wav.getnframes(),0)
            self.assertEqual(kw,dict(model_size='medium',language='zh',local_only=True))
            return {'segments':[{'text':'换成父亲。'}]}
        folder=ROOT/'runtime/temp';before=set(folder.glob('voice-*'))
        with patch.object(asr,'transcribe',side_effect=transcribe):self.assertEqual(voice_input.transcribe_stream(io.BytesIO(audio.read_bytes()),audio.stat().st_size)['text'],'换成父亲。')
        with patch.object(asr,'transcribe',side_effect=RuntimeError('本地模型缺失')):
            with self.assertRaisesRegex(ValueError,'没能转成文字'):voice_input.transcribe_stream(io.BytesIO(audio.read_bytes()),audio.stat().st_size)
        with self.assertRaises(ValueError):voice_input.transcribe_stream(io.BytesIO(b'x'),4)
        self.assertEqual(set(folder.glob('voice-*')),before)

    def test_rebuild_preserves_paid_files_and_marks_old_current_after_new_plan(self):
        from console import jimmy_bridge
        self.plan();rec=self.edit.preview(self.pid,'s01',{})
        out=self.tmp/'result.png';Image.new('RGB',(200,300),'red').save(out)
        jimmy_bridge._image_done(self.pid,rec['id'],out)
        before=store.load(self.pid);version=before['storyboard_edits']['sheets']['s01']['versions'][0]
        hashes={f:store.sha256_file(self.d/f) for f in [version['clean_file'],version['file'],*version['cells'].values()]}
        source=self.d/'source/source.mp4'
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=blue:size=90x160:rate=2:duration=9','-c:v','libx264',str(source)],check=True)
        store.atomic_write_json(self.d/'analysis/boundaries.json',{'merged_shots':[dict(start=i,end=i+1) for i in range(9)]})
        rebuilt=storyboard.build(self.pid)
        self.assertEqual(rebuilt['storyboard']['version'],3)
        self.assertEqual({f:store.sha256_file(self.d/f) for f in hashes},hashes)
        self.assertIn(self.edit.SOURCE_WARNING,self.edit.warnings(rebuilt,'s01',self.d))
        with patch.object(gateway,'reason_checked',return_value=(self.valid(rebuilt),{})):
            self.edit.generate_plan(self.pid,'s01',{'request':'按新的分镜改'})
        self.assertTrue(self.edit.view(store.load(self.pid),self.d)['s01']['source_stale'])
        # A fresh whole-sheet preview is still allowed; old cell coordinates are not.
        self.edit.preview(self.pid,'s01',{})
        with self.assertRaisesRegex(ValueError,'整张新分镜'):
            self.edit.preview(self.pid,'s01',{'cell_id':'c01','request':'改脸'})

    def test_landscape_padding_long_prompt_and_missing_gutter_fallback(self):
        self.plan()
        data=store.load(self.pid)
        for cell in data['storyboard']['cells']:Image.new('RGB',(160,90),'blue').save(self.d/cell['file'])
        store.update(self.pid,lambda d:d['source'].update(probe=dict(width=160,height=90)))
        state=data['storyboard_edits']['sheets']['s01'];state['plan']['cells'][0]['changes']='具体要求'*1200
        self.edit.save(self.pid,'s01',{'plan':state['plan']})
        rec=self.edit.preview(self.pid,'s01',{})
        self.assertEqual(rec['preview']['size'],'1536x1024');self.assertTrue(rec['warnings'])
        self.assertIn('具体要求'*1200,rec['preview']['prompt'])
        from console import storyboard_images
        image=Image.new('RGB',(492,282),(20,30,40))
        tiles=storyboard_images.split_sheet(image,(160,90),9)
        self.assertEqual([t.size for t in tiles],[(160,90)]*9)
        self.assertEqual(tiles[4].getpixel((80,45)),(20,30,40))

    def test_paid_confirmation_is_explicit_idempotent_and_routes_usage(self):
        from console import jimmy_bridge, jobs, openai_image
        self.plan();rec=self.edit.preview(self.pid,'s01',{})
        with patch.object(openai_image,'generate') as generate,patch.object(jobs,'start') as start:
            self.assertEqual(rec['state'],'pending_confirmation');generate.assert_not_called()
            gateway.image_confirm(self.pid,rec['id'])
            start.assert_called_once();generate.assert_not_called()
            with self.assertRaises(jimmy_bridge.BridgeError):gateway.image_confirm(self.pid,rec['id'])
            start.assert_called_once()
        self.assertEqual(store.load(self.pid)['usage'][-1]['step'],'storyboard_image')

    def test_checked_non_json_exception_has_expected_message(self):
        from console.gateway.errors import JSONResponseError, CheckedLLMError
        self.fixture()
        with patch.object(gateway,'reason_checked',side_effect=CheckedLLMError([dict(provider='openai',model='mock',reason='非 JSON',kind='unknown',max_tokens=6000)])):
            with self.assertRaisesRegex(ValueError,self.edit.INCOMPLETE):
                self.edit.generate_plan(self.pid,'s01',{'request':'换父亲'})

    def test_material_toggle_and_photo_change_invalidate_confirm(self):
        self.plan();rec=self.edit.preview(self.pid,'s01',{})
        self.edit.save(self.pid,'s01',{'materials':{'C1':False,'C2':True,'product':True,'N1':True}})
        state=store.load(self.pid)['storyboard_edits']['sheets']['s01']
        self.assertNotEqual(rec['storyboard_context']['plan_sig'],state['plan']['sig'])
        self.assertIn(self.edit.SETTINGS_WARNING,self.edit.warnings(store.load(self.pid),'s01',self.d))
        with self.assertRaisesRegex(ValueError,'重新生成免费预览'):self.edit.verify_preview(self.pid,rec)

        rec=self.edit.preview(self.pid,'s01',{})
        Image.new('RGB',(50,70),'blue').save(self.d/'assets/son.jpg')
        with self.assertRaisesRegex(ValueError,'重新生成免费预览'):self.edit.verify_preview(self.pid,rec)

    def test_two_paid_cell_jobs_cannot_run_against_same_old_version(self):
        from console import jimmy_bridge,jobs
        self.plan();full=self.edit.preview(self.pid,'s01',{})
        out=self.tmp/'full.png';Image.new('RGB',(200,300),'blue').save(out)
        jimmy_bridge._image_done(self.pid,full['id'],out)
        with patch.object(gateway,'reason_checked',return_value=({'changes':'按本次要求调整画面','keep':'','why':'已整理这一镜的改法'},{})):
            a=self.edit.preview(self.pid,'s01',{'cell_id':'c01','request':'改成红色'})
        with patch.object(gateway,'reason_checked',return_value=({'changes':'按本次要求调整画面','keep':'','why':'已整理这一镜的改法'},{})):
            b=self.edit.preview(self.pid,'s01',{'cell_id':'c02','request':'改成绿色'})
        with patch.object(jobs,'start') as start:
            gateway.image_confirm(self.pid,a['id'])
            with self.assertRaisesRegex(jimmy_bridge.BridgeError,'正在生成'):gateway.image_confirm(self.pid,b['id'])
            start.assert_called_once()
        Image.new('RGB',(200,300),'red').save(out);jimmy_bridge._image_done(self.pid,a['id'],out)
        with patch.object(jobs,'start') as start:
            with self.assertRaisesRegex(jimmy_bridge.BridgeError,'重新整理改法并生成预览'):gateway.image_confirm(self.pid,b['id'])
            start.assert_not_called()
        data=store.load(self.pid);self.assertEqual(data['images'][b['id']]['state'],'pending_confirmation')
        version=data['storyboard_edits']['sheets']['s01']['versions'][-1]
        with Image.open(self.d/version['cells']['c01']) as image:self.assertGreater(image.getpixel((45,80))[0],240)

    def test_explicit_source_clear_removes_edit_metadata_and_internal_references(self):
        from console import pipeline
        self.plan();self.edit.preview(self.pid,'s01',{})
        data=pipeline.clear_source(self.pid)
        self.assertFalse(data.get('storyboard_edits'))
        self.assertTrue(all(not a['file'].startswith('analysis/storyboard/') for a in data['assets']))
        self.assertTrue(any(a['id']=='father' for a in data['assets']))

    def test_cell_mentioned_material_overrides_sheet_switch_and_respects_cell_order(self):
        from console import jimmy_bridge
        self.plan();rec=self.edit.preview(self.pid,'s01',{})
        out=self.tmp/'full.png';Image.new('RGB',(200,300),'blue').save(out);jimmy_bridge._image_done(self.pid,rec['id'],out)
        self.edit.save(self.pid,'s01',{'materials':{'C1':True,'C2':False,'N1':False,'product':False}})
        with patch.object(gateway,'reason_checked',return_value=({'changes':'按本次要求调整画面','keep':'','why':'已整理这一镜的改法'},{})):
            one=self.edit.preview(self.pid,'s01',{'cell_id':'c05','request':'加上C2 儿子和医生，让商品手表露出'})
        self.assertEqual([r['key'] for r in one['storyboard_context']['references']],['current','C1','C2','N1','product'])

    def test_routes_and_http_version_guards(self):
        self.fixture()
        self.assertEqual(gateway.routes.DEFAULT_ROUTES['storyboard_plan'],'follow:analysis')
        self.assertEqual(gateway.routes.DEFAULT_ROUTES['storyboard_image'],'official:gpt-image-2')
        self.assertEqual(gateway.routes.resolve('storyboard_image',values={**config.DEFAULTS,'OPENAI_API_KEY':''})['channel'],'third_party')
        import server
        handler=server.Handler.__new__(server.Handler);handler.headers={}
        with self.assertRaises(server.ApiError):handler._api('POST',['projects',self.pid,'storyboard-edits','s01'],{})
        handler.headers={'X-Client-Build':'sb2'}
        with patch.object(server,'build_status',return_value={'restart_pending':False,'build':'sb2','server_build':'sb2','web_build':'sb2'}),patch.object(handler,'_body_json',return_value={'request':'改图'}),patch.object(server,'project_view',side_effect=lambda x:x),patch.object(self.edit,'generate_plan',return_value={'ok':True}) as call:
            self.assertEqual(handler._api('POST',['projects',self.pid,'storyboard-edits','s01','plan'],{}),{'ok':True});call.assert_called_once()


if __name__=='__main__':unittest.main()
