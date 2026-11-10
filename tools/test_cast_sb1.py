"""SB1 photo understanding and story checks: synthetic photos, mocked reasoning."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from console import config, gateway, pipeline, store


class CastSB1Tests(unittest.TestCase):
    def setUp(self):
        from console import cast
        self.cast=cast
        self.tmp=Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT/'runtime/temp')))
        self.enterContext(patch.object(config,'PROJECTS_DIR',self.tmp))
        self.enterContext(patch.object(config,'load',return_value=dict(config.DEFAULTS)))
        self.pid=store.create('cast mock')['id']; self.d=store.project_dir(self.pid)
        Image.new('RGB',(48,64),'blue').save(self.d/'assets/photo.jpg')
        store.update(self.pid,lambda d:d.update(assets=[dict(id='a1',file='assets/photo.jpg',type='image')],breakdown={
            'characters':[dict(id='C1',name='父亲',role='老人',appearance='白发',outfit='衬衫')],
            'productMoments':[dict(product='手环',start=1,end=2)],'beats':[dict(summary='呼救')],
            'dialogue':[dict(text='救我')],'overlays':[dict(text='SOS')]}))

    def test_cast_follows_analysis(self):
        values={**config.DEFAULTS,'OPENAI_API_KEY':'fake','ROUTE_ANALYSIS':'openai:vision'}
        self.assertEqual(gateway.routes.DEFAULT_ROUTES['cast'],'follow:analysis')
        self.assertEqual(gateway.routes.resolve('cast',values=values),{'channel':'openai','model':'vision'})
        gateway.routes.validate(values)

    def test_person_description_preserved_and_suggestion(self):
        look={'gender_age':'男，70 岁左右','face':'皱纹','hair':'白色短发','outfit':'灰色毛衣','body':'偏瘦'}
        for description in ('','用户手写'):
            with patch.object(gateway,'reason_checked',return_value=(look,{})) as call:
                response=self.cast.identify(self.pid,{'kind':'character','source_id':'C1','entry':dict(action='replace',asset_id='a1',description=description)})
            target=response['project']['replacements']['characters'][0]
            self.assertEqual(target['look'],look)
            self.assertIn('白色短发',response['description'])
            self.assertEqual(target['description'], description or response['description'])
            self.assertEqual(response['suggested'],bool(description))
            self.assertEqual(call.call_args.args[0],'cast')
            messages=call.call_args.args[1]
            self.assertIn('老人',str(messages)); self.assertIn('image_url',str(messages))
        self.assertTrue(store.load(self.pid)['log'])

    def test_product_look_story_answers_and_stale(self):
        product=dict(action='replace',asset_id='a1',description='黑色手表')
        look=dict(category='智能手表',look='黑色圆盘',parts='右侧按键',how_used='戴手腕上')
        with patch.object(gateway,'reason_checked',return_value=(look,{})):
            result=self.cast.identify(self.pid,{'kind':'product','entry':product})
        self.assertIn('戴手腕上',result['description'])
        checks=[dict(id='k1',feature='SOS',where='6–9 秒，按键呼救',question='支持 SOS 吗？'),dict(id='k2',feature='通知手机',where='9 秒',question='能通知吗？')]
        with patch.object(gateway,'reason_checked',return_value=({'checks':checks},{})) as call:
            data=self.cast.check_story(self.pid,{'entry':product})['project']
        self.assertIn('救我',str(call.call_args.args[1])); self.assertIn('image_url',str(call.call_args.args[1]))
        saved=data['replacements']['product']; self.assertEqual(len(saved['story_checks']),2)
        self.assertEqual(saved['story_checks'][0]['answer'],'')
        self.assertFalse(self.cast.story_stale(data,self.d))
        saved['story_checks'][0].update(answer='no',note='改成打电话')
        saved['story_checks'][1].update(answer='unsure',note='')
        data=pipeline.save_replacements(self.pid,data['replacements'])
        self.assertEqual(data['replacements']['product']['story_checks'][0]['note'],'改成打电话')
        data['replacements']['product']['description']='新表'
        data=pipeline.save_replacements(self.pid,data['replacements'])
        self.assertTrue(self.cast.story_stale(data,self.d))
        self.assertEqual(len(data['replacements']['product']['story_checks']),2)

    def test_story_fingerprint_freezes_photo_sent_to_model(self):
        def reason(*args, **kwargs):
            Image.new('RGB',(48,64),'red').save(self.d/'assets/photo.jpg')
            return {'checks':[]},{}
        with patch.object(gateway,'reason_checked',side_effect=reason):
            data=self.cast.check_story(self.pid,{'entry':dict(action='replace',asset_id='a1',description='表')})['project']
        self.assertTrue(self.cast.story_stale(data,self.d))

    def test_new_person_saved_and_read(self):
        data=pipeline.save_replacements(self.pid,{'characters':[dict(source_id='N1',action='add',name='医生',asset_id='a1',description='白衣')],'product':{}})
        self.assertEqual(store.load(self.pid)['replacements']['characters'][0]['name'],'医生')
        with patch.object(gateway,'reason_checked',return_value=({'gender_age':'女','face':'','hair':'短发','outfit':'白衣','body':''},{})):
            out=self.cast.identify(self.pid,{'kind':'character','source_id':'N1','entry':data['replacements']['characters'][0]})
        self.assertEqual(out['project']['replacements']['characters'][0]['action'],'add')

    def test_validation_failure_logs_and_never_saves_bad_result(self):
        with patch.object(gateway,'reason_checked',return_value=({'checks':[{}]*6},{})):
            with self.assertRaisesRegex(ValueError,'检查'):
                self.cast.check_story(self.pid,{'entry':dict(asset_id='a1',description='表',action='replace')})
        self.assertNotIn('replacements',store.load(self.pid))
        self.assertEqual(store.load(self.pid)['log'][-1]['level'],'warn')
        with patch.object(gateway,'reason_checked',return_value=({'hair':'a'*31},{})):
            with self.assertRaises(ValueError):
                self.cast.identify(self.pid,{'kind':'character','source_id':'C1','entry':dict(asset_id='a1')})

if __name__=='__main__':unittest.main()
