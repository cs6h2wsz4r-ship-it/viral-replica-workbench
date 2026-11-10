"""SB3 director contracts using synthetic local images and mocked reasoning only."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import config, gateway, llm, store, storyboard, storyboard_edits
from console.gateway import routes
from console.gateway.errors import JSONResponseError
from tools.v2_fixture import configure, choose, CONTENT_KEYS

try:
    from console import director
except ImportError:
    director = None


class DirectorSB3Tests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(director, 'SB3 director module is required')
        (ROOT/'runtime/temp').mkdir(parents=True, exist_ok=True)
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT/'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.tmp))
        self.enterContext(patch.object(config, 'load', return_value=dict(config.DEFAULTS)))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('no secret reads')))
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('no live network')))
        self.pid = store.create('SB3 synthetic')['id']
        self.d = store.project_dir(self.pid)

    def raw_fixture(self, count=9, seconds=1):
        folder = self.d/'analysis/storyboard'
        folder.mkdir(parents=True, exist_ok=True)
        cells = []
        for i in range(1, count+1):
            file = f'analysis/storyboard/cell-{i:02d}.png'
            Image.new('RGB', (30, 50), ((i*17)%230, 30, 70)).save(self.d/file)
            cells.append(dict(id=f'c{i:02d}', index=i, start=(i-1)*seconds, end=i*seconds,
                              frame_at=(i-.5)*seconds, file=file, characters=['C1','C2'],
                              product='SOS 手环' if i==1 else '', action_full='父亲举起左手；儿子向前走', dialogue='别担心'))
        sheets = storyboard.render_sheets(cells, folder)
        for name in ('father','son','doctor','watch'):
            Image.new('RGB',(20,30),'gray').save(self.d/f'assets/{name}.png')
        (self.d/'source/source.mp4').write_bytes(b'synthetic source; never submitted')
        data = store.update(self.pid, lambda d:d.update(
            source={'file':'source/source.mp4','probe':{'duration':count*seconds}},
            breakdown={'overview':{'oneLine':'父子求助','duration':count*seconds},
                       'characters':[{'id':'C1','name':'父亲'},{'id':'C2','name':'儿子'}],
                       'shots':[dict(start=c['start'],end=c['end'],characters=c['characters'],action=c['action_full']) for c in cells],
                       'beats':[dict(start=0,end=count*seconds,function='转折',summary='完成求助',performance='从担心转为放心')],
                       'dialogue':[dict(start=0,end=1,speaker='C2',text='别担心')],
                       'productMoments':[dict(start=0,end=1,product='SOS 手环',presentation='手腕特写',sellingPoint='按键呼救')]},
            assets=[dict(id=n,type='image',file=f'assets/{n}.png') for n in ('father','son','doctor','watch')],
            storyboard=dict(version=3,built_at='synthetic',source_sig='source',cells=cells,sheets=sheets),
            replacements={'characters':[dict(source_id='C1',action='replace',asset_id='father',description='白发父亲'),
                                        dict(source_id='C2',action='replace',asset_id='son',description='短发儿子'),
                                        dict(source_id='N1',action='add',asset_id='doctor',name='医生',description='中年医生')],
                          'product':dict(action='replace',asset_id='watch',description='黑色手表戴左手腕',
                                         story_checks=[dict(question='支持呼救吗',answer='unsure'),dict(question='是否防水',answer='')])}))
        states = {}
        for sheet in sheets:
            sid=sheet['id']; current={}
            for cid in sheet['cells']:
                file=f'analysis/storyboard/new-{cid}.png'
                i=int(cid[1:]); Image.new('RGB',(30,50),(20,(i*19)%230,100)).save(self.d/file)
                current[cid]=file
            labeled=f'analysis/storyboard/new-{sid}.jpg'
            Image.new('RGB',(102,162),'pink').save(self.d/labeled)
            bsig=storyboard_edits.board_sig(data,sid)
            plan={'cells':[dict(cell=cid,changes='替换父子和手表',keep='场景光线') for cid in sheet['cells']], 'sig':'plan'}
            states[sid]=dict(current='v1',storyboard_sig=bsig,settings_sig='fixture',plan=plan,
                             versions=[dict(id='v1',file=labeled,cells=current,storyboard_sig=bsig,plan_sig='plan')])
        return store.update(self.pid,lambda d:d.update(storyboard_edits={'sheets':states}))

    def fixture(self,count=9,seconds=1):
        return configure(DirectorSB3Tests.raw_fixture(self,count,seconds),self.d)

    def segment(self, ids, sid='d1', **kw):
        data=store.load(self.pid)
        planned=next((s for s in data.get('segments_plan',{}).get('segments',[]) if s['id']==sid),{})
        from console import segment_plan
        images=segment_plan.generation_images(data,planned,self.d) if planned else [ids[0],'C1','C2','product']
        mode=planned.get('mode','video')
        row=dict(id=sid,cells=ids,mode=mode,images=images,gen_images=images,what='父亲举腕，儿子扶稳父亲。',check=['左腕手表表盘朝外'],
                 prompt='写实手机竖屏视频。'+'；'.join(f'图{i} 提供目标外观' for i in range(1,len(images)+1))+
                        ('；视频1 只提供场景与动作节奏' if mode=='video' else '')+'。人物动作连贯，商品状态正确。')
        row.update(kw)
        return row

    def plan(self,data,groups=None):
        if groups is not None:configure(data,self.d,groups)
        return dict(summary='按完整求助事件复刻',questions=['呼救能力不确定'],segments=[
            {k:v for k,v in self.segment(s['cells'],s['id']).items() if k in CONTENT_KEYS}
            for s in data['segments_plan']['segments']])

    def generated(self,count=9,seconds=1,groups=None):
        data=self.fixture(count,seconds); plan=self.plan(data,groups)
        with patch.object(gateway,'reason_checked',return_value=(plan,{})):
            director.generate(self.pid)
        return store.load(self.pid)

    def test_route_follows_script_and_rejects_other_follow(self):
        self.assertEqual(routes.DEFAULT_ROUTES['director'],'follow:script')
        self.assertEqual(routes.STEP_LABELS['director'],'导演方案')
        with patch.object(routes,'resolve',return_value={}):
            routes.validate({'ROUTE_DIRECTOR':'follow:script'})
            with self.assertRaisesRegex(ValueError,'不支持'):
                routes.validate({'ROUTE_DIRECTOR':'follow:analysis'})

    def test_generate_requires_every_current_sheet_and_source_alignment_before_model(self):
        for mutate in (lambda d:d['segment_boards']['segments']['d2'].pop('current'),
                       lambda d:d['segment_boards']['segments']['d1']['versions'][0].update(storyboard_sig='changed')):
            self.fixture(10);store.update(self.pid,mutate)
            with patch.object(gateway,'reason_checked') as call:
                with self.assertRaisesRegex(ValueError,'第 04 步（对照和修改）.*新分镜'):
                    director.generate(self.pid)
                call.assert_not_called()
            self.assertNotIn('director',store.load(self.pid))

    def test_one_generation_call_has_all_context_and_labeled_images(self):
        data=self.fixture(10);seen=[]
        def image(path,*args,**kw):
            seen.append(Path(path).relative_to(self.d).as_posix())
            return [{'type':'image_url','image_url':{'url':'synthetic://image'}}]
        with patch.object(llm,'image_part',side_effect=image),patch.object(gateway,'reason_checked',return_value=(self.plan(data),{})) as call:
            director.generate(self.pid)
        call.assert_called_once();self.assertEqual(call.call_args.args[0],'director')
        text=json.dumps(call.call_args.args[1],ensure_ascii=False)
        for value in ('父子求助','转折','从担心转为放心','别担心','C2','手腕特写','按键呼救','父亲举起左手','替换父子和手表','白发父亲','THE FATHER','支持呼救吗','不确定'):
            self.assertIn(value,text)
        self.assertIn('原片分镜',text);self.assertIn('当前新分镜',text)
        self.assertEqual(len(seen),11) # second segment has no product: source/new + live photos + target cell
        for state in data['segment_boards']['segments'].values():
            self.assertIn(state['source']['file'],seen)
        saved=store.load(self.pid)['director']
        self.assertEqual(saved['inputs_sig'],director.inputs_sig(store.load(self.pid),self.d))
        self.assertTrue(saved['generated_at']);self.assertFalse(saved['segments'][0]['edited']);self.assertFalse(saved['segments'][0]['needs_rewrite'])

    def test_system_states_director_split_reference_and_brief_contract(self):
        for value in ('第 04 步','只写提示词','4500','完整提示词','JSON','起止状态','配乐','旁白','商品按真实用法使用或摆放','正面或功能面朝向镜头'):
            self.assertIn(value,director.SYSTEM)
        for value in ('固定 8 节','replace_keep','timeline','assemble_brief','每段原片不少于 3 秒'):
            self.assertNotIn(value,director.SYSTEM)
        self.assertFalse(hasattr(director,'assemble_brief'))

    def test_duration_exact_source_and_channel_clamp(self):
        for seconds,expected in ((.7,4),(4.0000001,5),(4.01,5),(14.7,15),(15,15)):
            data=self.fixture(1,seconds)
            plan=director.validate_plan(self.plan(data),data,self.d)
            row=plan['segments'][0]
            self.assertEqual(row['source_start'],0);self.assertEqual(row['source_end'],seconds)
            self.assertEqual(row['source_seconds'],seconds);self.assertEqual(row['target_seconds'],expected);self.assertAlmostEqual(row['stretch'],expected/seconds)
            self.assertEqual(row['duration'],expected)

    def test_complete_coverage_continuity_order_and_safe_unique_ids(self):
        from console import segment_plan
        data=self.fixture(4)
        bad=[[['c01','c02'],['c02','c03','c04']],[['c01','c03'],['c02','c04']],[['c03','c04'],['c01','c02']],[['c01','c02','c03']],[['c01','c02','c03','c99']]]
        for groups in bad:
            value={'segments':[dict(id=f'd{i}',cells=ids,mode='video',refs=['C1']) for i,ids in enumerate(groups,1)]}
            with self.subTest(groups=groups),self.assertRaisesRegex(ValueError,'分段方案不完整：'):
                segment_plan.validate_plan(value,data,self.d)
        for sid in ('../evil','x/y','',None,'a'*65):
            value=copy.deepcopy(data['segments_plan']);value['segments'][0]['id']=sid
            with self.assertRaisesRegex(ValueError,'分段方案不完整：'):
                segment_plan.validate_plan(value,data,self.d)
        value={'segments':[dict(id='d1',cells=ids,mode='video',refs=[]) for ids in (['c01','c02'],['c03','c04'])]}
        with self.assertRaisesRegex(ValueError,'分段方案不完整：'):segment_plan.validate_plan(value,data,self.d)

    def test_rejects_long_source_missing_material_or_invalid_mode_atomically(self):
        from console import segment_plan
        data=DirectorSB3Tests.raw_fixture(self,2,8)
        value={'segments':[dict(id='d1',cells=['c01','c02'],mode='video',refs=['C1'],gen_images=['c01'])]}
        with self.assertRaisesRegex(ValueError,'15'):segment_plan.validate_plan(value,data,self.d)
        data=self.generated(2);before=copy.deepcopy(data)
        for change in ({'refs':['C1',1]},{'refs':['missing']},{'mode':'bad'}):
            with self.assertRaisesRegex(ValueError,'分段方案不完整：'):
                segment_plan.save(self.pid,'d1',change)
            self.assertEqual(store.load(self.pid),before)
        (self.d/'assets/watch.png').unlink()
        with self.assertRaisesRegex(ValueError,'分段方案不完整：'):
            segment_plan.validate_plan(data['segments_plan'],data,self.d)

    def test_removed_reference_and_short_brief_warnings_stay_absent(self):
        self.generated(2)
        row=director.save(self.pid,{'action':'prompt','segment_id':'d1','prompt':'图9 视频1'})['director']['segments'][0]
        self.assertEqual(row['warnings'],[])
        self.assertTrue(row['needs_rewrite'])
        self.assertIn('素材编号',row['rewrite_reason'])

    def test_signature_changes_on_board_versions_cast_bytes_and_product_checks(self):
        data=self.generated();sig=director.inputs_sig(data,self.d)
        for mutate in (lambda d:d['storyboard'].update(built_at='other'),
                       lambda d:d['segment_boards']['segments']['d1'].update(current='v2'),
                       lambda d:d['replacements']['characters'][0].update(description='眼镜'),
                       lambda d:d['replacements']['product']['story_checks'][0].update(answer='yes')):
            changed=copy.deepcopy(data);mutate(changed)
            self.assertNotEqual(sig,director.inputs_sig(changed,self.d))
            self.assertTrue(director.view(changed,self.d)['stale'])
        (self.d/'assets/watch.png').write_bytes(b'changed image bytes')
        self.assertNotEqual(sig,director.inputs_sig(data,self.d))
        self.assertIn(director.STALE_WARNING,director.view(data,self.d)['warnings'])

    def test_merge_and_split_are_removed_without_mutation(self):
        before=self.generated(4,groups=[['c01','c02'],['c03','c04']])
        for action in ('merge','split'):
            with self.assertRaisesRegex(ValueError,'未知的导演方案操作'):
                director.save(self.pid,{'action':action,'segment_id':'d1','before_cell':'c03'})
            self.assertEqual(store.load(self.pid),before)

    def test_merge_over_limit_and_invalid_split_do_not_save(self):
        data=self.generated(4,4,[['c01','c02'],['c03','c04']])
        for body in ({'action':'merge','segment_id':'d1'},{'action':'split','segment_id':'d1','before_cell':'c01'},
                     {'action':'split','segment_id':'d1','before_cell':'c04'}):
            with self.assertRaises(ValueError):director.save(self.pid,body)
            self.assertEqual(store.load(self.pid),data)


    def test_mode_images_brief_edit_preserves_rewrite_flag(self):
        from console import segment_plan
        self.generated(2)
        segment_plan.save(self.pid,'d1',{'mode':'image','refs':['C1','C2','product']})
        saved=director.save(self.pid,{'action':'prompt','segment_id':'d1','prompt':'A new English brief.'})
        row=saved['director']['segments'][0]
        self.assertTrue(row['edited']);self.assertEqual(row['prompt'],'A new English brief.')
        self.assertTrue(director.view(saved,self.d)['segments'][0]['stale'])
        self.assertEqual(saved['segments_plan']['segments'][0]['refs'],['C1','C2','product'])

    def test_rewrite_one_call_full_context_preserves_other_segment_and_rejects_cells(self):
        data=self.generated(4,groups=[['c01','c02'],['c03','c04']])
        choose(data,self.d,'d1',mode='image')
        before=store.load(self.pid);target=copy.deepcopy(before['director']['segments'][0])
        target.update(prompt=self.segment(['c01','c02'])['prompt'],cells=['c01'],mode='video',images=['storyboard'])
        with patch.object(gateway,'reason_checked',return_value=(target,{})) as reason:
            result=director.save(self.pid,{'action':'rewrite','segment_id':'d1'})
        reason.assert_called_once();self.assertEqual(reason.call_args.args[0],'director')
        text=json.dumps(reason.call_args.args[1],ensure_ascii=False)
        for token in ('c04','父亲举起左手','起止状态'):self.assertIn(token,text)
        row=result['director']['segments'][0]
        self.assertFalse(row['needs_rewrite']);self.assertEqual(row['cells'],['c01','c02'])
        self.assertEqual(row['mode'],'image');self.assertEqual(row['images'],['c01','C1','C2','product'])
        self.assertEqual(result['director']['segments'][1],before['director']['segments'][1])
        bad=copy.deepcopy(target);bad['id']='wrong'
        before=store.load(self.pid)
        with patch.object(gateway,'reason_checked',return_value=(bad,{})),self.assertRaisesRegex(ValueError,'导演方案不完整：'):
            director.save(self.pid,{'action':'rewrite','segment_id':'d1'})
        after=store.load(self.pid)
        self.assertEqual({k:v for k,v in after.items() if k!='log'}, {k:v for k,v in before.items() if k!='log'})
        self.assertEqual(len(after['log']),len(before['log'])+1)
        self.assertIn('analysis/director/responses/',after['log'][-1]['msg'])
        self.assertIn('导演方案不完整：',after['log'][-1]['msg'])

    def test_concurrent_generation_edit_is_rejected_without_replacing_plan(self):
        data=self.generated(2);old=copy.deepcopy(data['director'])
        def model(*args,**kwargs):
            store.update(self.pid,lambda d:d['replacements']['product'].update(description='刚修改'))
            return self.plan(data),{}
        with patch.object(gateway,'reason_checked',side_effect=model),self.assertRaises(ValueError):director.generate(self.pid)
        self.assertEqual(store.load(self.pid)['director'],old)

    def test_collage_layouts_are_clean_ordered_white_and_rebuilt(self):
        for count,cols,rows in ((1,2,1),(2,2,1),(3,2,2),(4,2,2),(5,3,2),(6,3,2)):
            data=self.generated(count)
            refs=director.references(data,data['director']['segments'][0],self.d)
            self.assertEqual([r['key'] for r in refs],['c01','C1','C2','product'])
            asset=director.build_storyboard(self.pid,'d1');self.assertEqual(asset['file'],'analysis/director/seg-d1-storyboard.jpg')
            self.assertEqual(asset['label'],'分镜图');self.assertIn('storyboard',asset['roles'])
            with Image.open(self.d/asset['file']) as img:
                self.assertEqual(img.size,(cols*30+(cols-1)*6,rows*50+(rows-1)*6))
                for i in range(count):
                    actual=img.getpixel(((i%cols)*36+15,(i//cols)*56+25));expected=(20,((i+1)*19)%230,100)
                    self.assertLess(sum(abs(a-b) for a,b in zip(actual,expected)),12)
                if count<cols*rows:self.assertTrue(all(c>245 for c in img.getpixel((img.width-10,img.height-10))))
            self.assertTrue(any(a['id']==asset['id'] for a in store.load(self.pid)['assets']))
        file=data['segment_boards']['segments']['d1']['versions'][0]['cells']['c01']
        Image.new('RGB',(30,50),'red').save(self.d/file)
        director.build_storyboard(self.pid,'d1')
        with Image.open(self.d/asset['file']) as img:self.assertGreater(img.getpixel((15,25))[0],240)

    def test_view_ready_stale_materials_and_segment_signature(self):
        data=self.generated(2);view=director.view(data,self.d)
        self.assertTrue(view['ready']);self.assertFalse(view['stale'])
        self.assertEqual(view['segments'][0]['id'],'d1')
        self.assertIn('material_options',view['segments'][0]);self.assertEqual(len(view['segments'][0]['cell_images']),2)
        row=data['director']['segments'][0];sig=director.segment_sig(row)
        self.assertEqual(sig,director.segment_sig({**row,'selected_run':'r1'}))
        self.assertNotEqual(sig,director.segment_sig({**row,'prompt':'changed'}))


    def test_json_error_has_incomplete_message_and_does_not_replace_plan(self):
        self.generated(2);before=store.load(self.pid)
        for action in ('generate','rewrite'):
            with patch.object(gateway,'reason_checked',side_effect=JSONResponseError('invalid JSON')):
                with self.assertRaisesRegex(ValueError,'导演方案不完整：'):
                    if action=='generate':director.generate(self.pid)
                    else:director.save(self.pid,{'action':'rewrite','segment_id':'d1'})
            self.assertEqual(store.load(self.pid),before)

    def test_selected_run_requires_completed_existing_result(self):
        self.generated(2)
        store.update(self.pid,lambda d:d['runs'].update(d1=[dict(rev='r1',state='running',result='clips/result.mp4')]))
        with self.assertRaisesRegex(ValueError,'已完成'):director.save(self.pid,{'action':'select-run','segment_id':'d1','rev':'r1'})
        store.update(self.pid,lambda d:d['runs']['d1'][0].update(state='completed'))
        with self.assertRaisesRegex(ValueError,'文件不存在'):director.save(self.pid,{'action':'select-run','segment_id':'d1','rev':'r1'})
        (self.d/'clips/result.mp4').write_bytes(b'synthetic finished')
        row=director.save(self.pid,{'action':'select-run','segment_id':'d1','rev':'r1'})['director']['segments'][0]
        self.assertEqual(row['selected_run'],'r1')
        with self.assertRaises(ValueError):director.save(self.pid,{'action':'select-run','segment_id':'d1','rev':'unknown'})

    def test_save_cannot_clear_rewrite_flag_and_signature_ignores_selected_version(self):
        data=self.generated(2)
        plan=data['director'];plan['segments'][0].update(prompt='',needs_rewrite=False)
        row=director.save(self.pid,{'action':'save','plan':plan})['director']['segments'][0]
        self.assertTrue(row['needs_rewrite'])
        self.assertEqual(director.segment_sig(row),director.segment_sig({**row,'selected_run':'r1'}))

    def test_request_normalizes_unknown_checks_everywhere(self):
        data=self.fixture(2)
        text=json.dumps(director.messages(data,self.d),ensure_ascii=False)
        self.assertNotIn('unsure',text)
        self.assertIn('不确定',text)

    def test_saved_reference_order_matches_program_assembled_numbering(self):
        data=self.fixture(2);choose(data,self.d,'d1',refs=['product','C1'])
        with patch.object(gateway,'reason_checked',return_value=(self.plan(data),{})):
            director.generate(self.pid)
        row=store.load(self.pid)['director']['segments'][0]
        self.assertEqual(row['images'],['c01','product','C1','C2'])
        refs=director.references(data,row,self.d)
        self.assertEqual([r['label'] for r in refs],['图1','图2','图3','图4'])
        self.assertEqual(refs[1]['description'],'新商品照片')
        self.assertEqual(refs[2]['description'],'THE FATHER 的人物照')
        self.assertFalse(row['needs_rewrite']);self.assertEqual(row['warnings'],[])

    def test_manual_edits_and_rewrite_keep_selected_result_for_stale_review(self):
        from console import segment_plan
        self.generated(2)
        store.update(self.pid,lambda d:d['director']['segments'][0].update(selected_run='r1'))
        row=director.save(self.pid,{'action':'prompt','prompt':'Manual English text.','segment_id':'d1'})['director']['segments'][0]
        self.assertEqual(row['selected_run'],'r1')
        for change in ({'mode':'image'},{'refs':['product','C1','C2']}):
            data=segment_plan.save(self.pid,'d1',change)
            self.assertEqual(data['director']['segments'][0]['selected_run'],'r1')
        choose(data,self.d,'d1')
        with patch.object(gateway,'reason_checked',return_value=(data['director']['segments'][0],{})):
            director.save(self.pid,{'action':'rewrite','segment_id':'d1'})
        plan=store.load(self.pid)['director'];plan['segments'][0]['selected_run']='r99'
        row=director.save(self.pid,{'action':'save','plan':plan})['director']['segments'][0]
        self.assertEqual(row['selected_run'],'r1')
        changed=copy.deepcopy(row);changed.update(brief='Rewritten prompt.',selected_run='r99')
        with patch.object(gateway,'reason_checked',return_value=(changed,{})):
            row=director.save(self.pid,{'action':'rewrite','segment_id':'d1'})['director']['segments'][0]
        self.assertEqual(row['selected_run'],'r1')

    def test_director_ids_cannot_shadow_legacy_script_tasks(self):
        data=self.fixture(2);data['script']={'tasks':[{'id':'d1','kind':'legacy'}]}
        with self.assertRaisesRegex(ValueError,'导演方案不完整：.*ID'):
            director.validate_plan(self.plan(data),data,self.d)

    def test_old_product_forbidden_names_have_no_warning_and_narration_rule_remains(self):
        data=self.fixture(1);data['breakdown']['productMoments']=[{'product':'SENVIA'}]
        row=self.segment(['c01']);row['forbid']=['SENVIA']
        self.assertEqual(director.segment_warnings(data,row),[])
        self.assertIn('没有旁白',director.SYSTEM)
        self.assertNotIn('除非原片明确有旁白',director.SYSTEM)

    def test_old_product_and_brand_are_kept_as_forbidden_content_only(self):
        data=self.fixture(1);plan=self.plan(data)
        plan['segments'][0]['prompt']+=' 禁止出现 SOS手环、Casio。'
        row=director.validate_plan(plan,data,self.d)['segments'][0]
        self.assertEqual(row['prompt'],plan['segments'][0]['prompt'])
        self.assertIn('禁止出现 SOS手环、Casio。',row['prompt'])
        self.assertEqual(row['warnings'],[])

    def test_select_completed_run_only_changes_selection_when_source_plan_is_stale(self):
        self.generated(4,groups=[['c01','c02'],['c03','c04']])
        (self.d/'clips/completed.mp4').write_bytes(b'synthetic completed run')
        def stale(d):
            d['storyboard']['cells'][-1]['end']=20
            d['runs']['d1']=[dict(rev='r1',state='completed',result='clips/completed.mp4')]
        before=store.update(self.pid,stale)
        expected=copy.deepcopy(before['director']);expected['segments'][0]['selected_run']='r1'
        with patch.object(gateway,'reason_checked') as reason:
            after=director.save(self.pid,{'action':'select-run','segment_id':'d1','rev':'r1'})
            reason.assert_not_called()
        self.assertEqual(after['director'],expected)
        self.assertTrue(director.view(after,self.d)['stale'])

    def test_rewrite_validates_only_target_despite_stale_unrelated_segment(self):
        self.generated(4,groups=[['c01','c02'],['c03','c04']])
        before=store.update(self.pid,lambda d:d['segment_boards']['segments']['d2'].update(current='missing-version'))
        target=copy.deepcopy(before['director']['segments'][0]);target['prompt']+=' Rewrite only the valid first event. Keep the identities distinct.'
        self.assertFalse(director.view(before,self.d)['ready'])
        with patch.object(gateway,'reason_checked',return_value=(target,{})) as reason:
            after=director.save(self.pid,{'action':'rewrite','segment_id':'d1'})
        reason.assert_called_once()
        self.assertIn(target['prompt'],after['director']['segments'][0]['prompt'])
        self.assertEqual(after['director']['segments'][1],before['director']['segments'][1])
        self.assertTrue(director.view(after,self.d)['segments'][1]['stale'])

    def test_rewrite_still_rejects_target_over_fifteen_seconds(self):
        self.generated(2)
        before=store.update(self.pid,lambda d:d['storyboard']['cells'][-1].update(end=20))
        with patch.object(gateway,'reason_checked') as reason:
            with self.assertRaisesRegex(ValueError,'第 04 步'):
                director.save(self.pid,{'action':'rewrite','segment_id':'d1'})
        reason.assert_not_called();self.assertEqual(store.load(self.pid),before)


from tools.director_sb3_bridge_cases import DirectorBridgeTests


if __name__ == '__main__':
    unittest.main()
