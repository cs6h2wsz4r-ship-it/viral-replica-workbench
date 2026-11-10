"""V2 segment planning, synthetic files and mocked model replies only."""
import base64
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
from console import config, gateway, store, storyboard


class SegmentFixture:
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT/'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.tmp))
        self.enterContext(patch.object(config, 'load', return_value={**config.DEFAULTS, 'OPENAI_API_KEY':'mock-only'}))
        self.pid = store.create('V2 watch synthetic')['id']
        self.d = store.project_dir(self.pid)

    def fixture(self, count=8):
        folder = self.d/'analysis/storyboard'; folder.mkdir(parents=True, exist_ok=True)
        boundaries = [0, 1.8, 3.6, 5.4, 6.8, 8.2, 9.6, 10.9, 12.3]
        cells = []
        for i in range(count):
            file = f'analysis/storyboard/cell-{i+1:02d}.jpg'
            Image.new('RGB', (90, 160), (20+i*20, 40+i*15, 70)).save(self.d/file)
            cells.append(dict(id=f'c{i+1:02d}', index=i+1, start=boundaries[i], end=boundaries[i+1],
                              frame_at=(boundaries[i]+boundaries[i+1])/2, file=file, characters=['C1'],
                              product='' if i<3 else '白色手环', action='父亲跌倒' if i<3 else '展示手表'))
        assets=[]
        for name in ('father', 'son', 'doctor', 'watch'):
            Image.new('RGB', (50, 70), 'gray').save(self.d/f'assets/{name}.jpg')
            assets.append(dict(id=name, type='image', file=f'assets/{name}.jpg'))
        return store.update(self.pid, lambda d:d.update(
            storyboard=dict(version=2, built_at='first', source_sig='source', cells=cells,
                            sheets=storyboard.render_sheets(cells, folder)),
            assets=assets, breakdown=dict(overview='老人跌倒，儿子送表，按表求助',
                characters=[dict(id='C1', name='父亲'), dict(id='C2', name='儿子')],
                beats=['跌倒', '求助'], dialogue=[], productMoments=[], scene='门廊和室内', sound='脚步声'),
            replacements=dict(characters=[dict(source_id='C1', action='replace', asset_id='father', description='白发父亲'),
                dict(source_id='C2', action='replace', asset_id='son'), dict(source_id='N1', action='add', asset_id='doctor', name='医生')],
                product=dict(action='replace', asset_id='watch', description='黑色智能手表', story_checks=[dict(question='能否求助', answer='yes')]))))

    def value(self):
        return dict(summary='c01 跌倒后在 c04 展示手表', why_segments='c03 和 c04 之间换了地点和商品状态',
                    skip_cells=[], questions=['c05 是否有台词？'], segments=[
            dict(id='d01', cells=['c01','c02','c03'], title='门廊跌倒', what='父亲在 c02 跌倒', mode='video', mode_reason='本段没有商品', refs=['C1'], gen_images=['c01']),
            dict(id='d02', cells=['c04','c05','c06','c07','c08'], title='室内求助', what='展示手表，佩戴并求助', mode='image', mode_reason='旧商品和旧包装占画面主体', refs=['C1','product'], gen_images=['c04','product'])])

    def generated(self):
        from console import segment_plan
        self.fixture()
        with patch.object(gateway, 'reason_checked', return_value=(self.value(), {})):
            return segment_plan.generate(self.pid)


class SegmentPlanTests(SegmentFixture, unittest.TestCase):
    def test_system_generic_principles_limits_and_route(self):
        from console import segment_plan
        for fragment in ('链路五', '较少段数', '镜头切点只是候选', '每段只放本段出现的人物和商品照片',
                         '清楚可见时，无论大小', '动作的价值和漏回风险', 'mode_reason', '15 秒', '6 格', '4 张', 'skip_cells'):
            self.assertIn(fragment, segment_plan.SYSTEM)
        for fragment in ('15 秒以内能一段就一段', '至少 3 秒', '参考案例', '12.3', '5.4', '19.4', '9.4'):
            self.assertNotIn(fragment, segment_plan.SYSTEM)
        self.assertNotIn('仅小面积出现且替换风险低时，可以用 video', segment_plan.SYSTEM)
        for fragment in ('4–15 的整数秒', '生成时长 = max(4, ceil(原片跨度))',
                         '均匀拉伸', '放慢倍数 = 生成时长 ÷ 原片跨度',
                         '原片跨度越短', '不足 4 秒', '动作节奏会被拖慢',
                         '每段独立生成', '段越多', '人物外观、光线和画面', '不一致',
                         '参考视频和输出都要计费', '接近整数秒', '不少于 4 秒',
                         '短段只有在有具体理由', '地点、人物或商品状态的硬变化',
                         '不能并入相邻段', '理由写进 why_segments'):
            with self.subTest(cost_or_guidance=fragment):
                self.assertIn(fragment, segment_plan.SYSTEM)
        self.assertIn('判断每格的地点、人物、商品，以这一格的原片画面为准；拆解文字和人物列表可能混入相邻镜头的内容，与画面不符时以画面为准。分界的地点、人物出场、商品状态变化，都按画面来定。',
                      segment_plan.SYSTEM)
        self.assertEqual(gateway.routes.DEFAULT_ROUTES['segment_plan'], 'follow:script')
        gateway.routes.validate(config.DEFAULTS)

    def test_request_states_generation_range_and_preserves_cell_times(self):
        from console import segment_plan
        data=self.fixture()
        request=segment_plan.messages(data,self.d)[1]
        self.assertEqual(request['role'],'user')
        text='\n'.join(part['text'] for part in request['content'] if part['type']=='text')
        for fragment in ('生成时长', '4–15 秒', '最短 4 秒', '最长 15 秒', '整数秒'):
            with self.subTest(limit=fragment):
                self.assertIn(fragment,text)
        context=json.loads(text.split('\n每格信息：',1)[1].split('\n第 03 步',1)[0])
        self.assertEqual([(row['id'],row['start'],row['end']) for row in context],
                         [(cell['id'],cell['start'],cell['end']) for cell in data['storyboard']['cells']])

    def test_short_segments_remain_model_choice_without_required_reason(self):
        from console import segment_plan
        data=self.fixture(count=2)
        answer=dict(segments=[dict(cells=[cell['id']],mode=mode,refs=[],gen_images=[cell['id']])
                              for cell,mode in zip(data['storyboard']['cells'],('video','image'))])
        with patch.object(gateway,'reason_checked',return_value=(answer,{})) as reason:
            plan=segment_plan.generate(self.pid)['segments_plan']
        reason.assert_called_once()
        self.assertEqual([segment['cells'] for segment in plan['segments']],[['c01'],['c02']])
        self.assertEqual([segment['mode'] for segment in plan['segments']],['video','image'])
        self.assertEqual([segment['duration'] for segment in plan['segments']],[4,4])
        self.assertEqual(plan['why_segments'],'')
        for segment in plan['segments']:
            self.assertLess(segment['source_seconds'],4)
            self.assertAlmostEqual(segment['stretch'],4/segment['source_seconds'])

    def test_watch_plan_mapping_defaults_and_input_evidence(self):
        from console import segment_plan
        data=self.fixture(); checked=segment_plan.validate_plan(self.value(), data, self.d)
        self.assertEqual([s['cells'] for s in checked['segments']], [self.value()['segments'][i]['cells'] for i in range(2)])
        self.assertEqual(checked['segments'][0]['refs'], ['C1'])
        self.assertEqual(checked['segments'][0]['duration'], 6)
        self.assertEqual(checked['segments'][1]['duration'], 7)
        self.assertIn('第 3 格', checked['why_segments']); self.assertIn('第 5 格', checked['questions'][0])
        empty=self.value(); del empty['summary']; del empty['segments'][0]['what']
        checked=segment_plan.validate_plan(empty, data, self.d)
        self.assertEqual(checked['summary'], ''); self.assertEqual(checked['segments'][0]['what'], '')
        ranged=self.value();ranged['summary']='c01至c03连续；c04严格按图，abc01 和 c010 不改'
        self.assertEqual(segment_plan.validate_plan(ranged,data,self.d)['summary'],
                         '第 1–3 格连续；第 4 格严格按图，abc01 和 c010 不改')
        messages=segment_plan.messages(data, self.d)
        text=json.dumps(messages, ensure_ascii=False)
        for fragment in ('门廊和室内', '脚步声', '能否求助', '白发父亲', '黑色智能手表', 'c08', '序号'):
            self.assertIn(fragment, text)
        self.assertTrue(any(p.get('type')=='image_url' for p in messages[1]['content']))

    def test_messages_cell_context_uses_helper_and_marks_adjacent_shot_evidence(self):
        from console import segment_plan
        data=self.fixture();cells=data['storyboard']['cells']
        for cell in cells:
            cell.update(action='CACHED_ACTION_SENTINEL_'+cell['id'],
                        action_full='CACHED_FULL_SENTINEL_'+cell['id'])
        cells[2]['characters']=['C1','C2']
        data['breakdown']['shots']=[
            dict(start=5.2,end=8.2,action='室内送表；前一镜头仍在门廊。',characters=['C1','C2']),
            dict(start=0,end=5.6,action='门廊跌倒；下一镜头儿子走入室内。',characters=['C1']),
            dict(start=8.2,end=12.3,action='戴表求助。',characters=['C1','C2']),
        ]
        messages=segment_plan.messages(data,self.d)
        context=json.loads(messages[1]['content'][0]['text'].split('\n每格信息：',1)[1].split('\n第 03 步',1)[0])
        self.assertEqual(len(context),len(cells))
        for cell,row in zip(cells,context):
            with self.subTest(cell=cell['id']):
                self.assertEqual(row['id'],cell['id'])
                self.assertEqual(row['动作'],storyboard.cell_text(cell,data['breakdown'])['action'])
                self.assertEqual(row.get('note'),'拆解文字可能混入相邻镜头的内容，以这一格的分镜图为准')
                self.assertIn('拆解识别的人物（可能混入相邻镜头）',row)
                self.assertEqual(row['拆解识别的人物（可能混入相邻镜头）'],cell['characters'])
                self.assertNotIn('人物',row)
        self.assertEqual(context[2]['动作'],'门廊跌倒；下一镜头儿子走入室内。；室内送表；前一镜头仍在门廊。')
        self.assertEqual(context[3]['动作'],context[2]['动作'])
        request=json.dumps(messages,ensure_ascii=False)
        for fragment in ('action_full','CACHED_ACTION_SENTINEL','CACHED_FULL_SENTINEL'):
            self.assertNotIn(fragment,request)

    def test_messages_images_follow_overview_cells_and_reference_photos(self):
        from console import segment_plan
        data=self.fixture();board=data['storyboard']
        board['cells'][0]['start']=0.0
        for i,asset in enumerate(data['assets']):
            Image.new('RGB',(50,70),(100+i*20,50,80)).save(self.d/asset['file'])
        parts=segment_plan.messages(data,self.d)[1]['content']
        images=[(i,part) for i,part in enumerate(parts) if part['type']=='image_url']
        photos=[photo for photo in segment_plan.material_options(data,self.d) if photo['available']]
        expected=board['sheets']+board['cells']+photos
        self.assertEqual(len(images),len(expected))
        for (index,part),source in zip(images,expected):
            with self.subTest(file=source['file']):
                self.assertEqual(base64.b64decode(part['image_url']['url'].split(',',1)[1],validate=True),
                                 (self.d/source['file']).read_bytes())
        for number,cell in enumerate(board['cells'],1):
            index,_=images[len(board['sheets'])+number-1]
            self.assertEqual(parts[index-1],dict(type='text',
                text=f"第 {number} 格（{cell['id']}）原片画面，{cell['start']:g}–{cell['end']:g} 秒"))

    def test_each_structural_error_rejects_whole_plan(self):
        from console import segment_plan
        data=self.fixture()
        mutations=[
            lambda p:p['segments'][0].update(id='../x'),
            lambda p:p['segments'][1].update(id='d01'),
            lambda p:p['segments'][0].update(cells=['c01','c03']),
            lambda p:p['segments'][0].update(cells=['c01','c01','c03']),
            lambda p:p['segments'][1].update(cells=['c05','c06','c07','c08']),
            lambda p:p['segments'][0].update(mode='other'),
            lambda p:p['segments'][0].update(refs=['missing']),
            lambda p:p['segments'][0].update(gen_images=['c01','c02','C1','C2','N1']),
            lambda p:p.update(skip_cells=['c02']),
            lambda p:p['segments'][0].update(cells=[f'c{i:02d}' for i in range(1,8)]),
        ]
        for mutate in mutations:
            candidate=self.value(); mutate(candidate)
            with self.subTest(candidate=candidate), self.assertRaisesRegex(ValueError, '分段方案不完整'):
                segment_plan.validate_plan(candidate, data, self.d)
        longer=copy.deepcopy(data); longer['storyboard']['cells'][2]['end']=16
        with self.assertRaisesRegex(ValueError, '15 秒'):
            segment_plan.validate_plan(self.value(), longer, self.d)
        skip=self.value(); skip['segments'][1]['cells'].pop(); skip['skip_cells']=['c08']
        self.assertEqual(segment_plan.validate_plan(skip, data, self.d)['skip_cells'], ['c08'])

    def test_generate_archives_before_validation_and_preserves_old_on_failure(self):
        from console import segment_plan
        before=self.generated(); old=copy.deepcopy(before['segments_plan'])
        invalid=self.value(); invalid['segments'][0]['cells']=['c01','c03']
        original=segment_plan.validate_plan
        def check(value,*args,**kw):
            paths=list((self.d/'analysis/segments/responses').glob('*.json'))
            self.assertTrue(any(json.loads(p.read_text())==invalid for p in paths))
            return original(value,*args,**kw)
        with patch.object(gateway,'reason_checked',return_value=(invalid,{})), patch.object(segment_plan,'validate_plan',side_effect=check):
            with self.assertRaises(ValueError):segment_plan.generate(self.pid)
        after=store.load(self.pid)
        self.assertEqual(after['segments_plan'],old)
        self.assertIn('模型原始回复',after['log'][-1]['msg'])
        raw='invalid { JSON'
        from console.gateway.errors import JSONResponseError
        def broken(*args,**kwargs):
            kwargs['on_response'](raw)
            raise JSONResponseError('bad JSON')
        with patch.object(gateway,'reason_checked',side_effect=broken),self.assertRaisesRegex(ValueError,'JSON'):
            segment_plan.generate(self.pid)
        self.assertIn(raw,[p.read_text() for p in (self.d/'analysis/segments/responses').glob('*.json')])

    def test_save_independence_source_staleness_and_legacy_preservation(self):
        from console import segment_plan, segment_boards
        data=self.generated(); other=segment_plan.segment_sig(data['segments_plan']['segments'][1])
        original=copy.deepcopy(data['storyboard'])
        saved=segment_plan.save(self.pid,'d01',{'mode':'image','refs':['C1','C2']})
        self.assertEqual(segment_plan.segment_sig(saved['segments_plan']['segments'][1]),other)
        self.assertEqual(saved['storyboard'],original)
        self.assertEqual(len(segment_boards.sheets(saved)),2)
        self.assertFalse(segment_plan.status(saved,self.d)['stale'])
        changed=copy.deepcopy(saved);changed['storyboard']['built_at']='new'
        self.assertTrue(segment_plan.status(changed,self.d)['stale'])
        with self.assertRaises(ValueError):segment_plan.save(self.pid,'d01',{'cells':['c01']})

    def test_all_repair_replies_are_archived_and_concurrent_edit_is_preserved(self):
        from console import segment_plan
        data=self.generated();old=copy.deepcopy(data['segments_plan'])
        raw='broken { first reply'
        def repaired(*args,**kwargs):
            kwargs['on_response'](raw)
            kwargs['on_response'](json.dumps(self.value(),ensure_ascii=False))
            return self.value(),{}
        before=set((self.d/'analysis/segments/responses').glob('*.json'))
        with patch.object(gateway,'reason_checked',side_effect=repaired) as reason:segment_plan.generate(self.pid)
        reason.assert_called_once()
        paths=sorted(set((self.d/'analysis/segments/responses').glob('*.json'))-before)
        self.assertEqual(len(paths),2);self.assertEqual(paths[0].read_text(),raw)
        self.assertEqual(json.loads(paths[1].read_text()),self.value())
        def racing(*args,**kwargs):
            segment_plan.save(self.pid,'d01',{'mode':'image'})
            return self.value(),{}
        with patch.object(gateway,'reason_checked',side_effect=racing),self.assertRaisesRegex(ValueError,'刚刚改过'):
            segment_plan.generate(self.pid)
        self.assertEqual(store.load(self.pid)['segments_plan']['segments'][0]['mode'],'image')
        self.assertEqual(store.load(self.pid)['segments_plan']['segments'][1],old['segments'][1])


if __name__=='__main__':unittest.main()
