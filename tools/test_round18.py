"""Round 18 offline transport/recovery/version regression tests."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from console import config, jimmy_bridge as bridge, jobs, llm, runtime_env, store
with patch.object(runtime_env, 'apply'):
    import server
ROOT = Path(__file__).resolve().parents[1]

class Round18Tests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.temp))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('禁止配置读取')))
        self.enterContext(patch.object(config, 'jimmy_env_file', return_value=self.temp / 'mock.env'))
        self.enterContext(patch.object(jobs, 'start', side_effect=lambda pid,key,fn,label: fn()))
        self.enterContext(patch.object(bridge.time, 'sleep'))
        self.pid = store.create('第18轮离线')['id']
        self.d = store.project_dir(self.pid)
        self.run = self.d / 'image-runs/img-test'
        self.run.mkdir(parents=True)
        self.output = self.run / 'result.png'
        self.output.write_bytes(b'offline image')
        self.sha = store.sha256_file(self.output)
        store.update(self.pid, lambda d: d['images'].update({'img-test': {
            'id':'img-test','state':'running','run_dir':'image-runs/img-test',
            'error':'old error','output_sha256':self.sha,'remote_task_id':'remote-test'}}))

    def test_unknown_status_then_completed(self):
        responses = [bridge.BridgeError('Image execution stopped: Jimmy image task reported an unrecognized status')]*2 + [json.dumps({'phase':'completed','output':str(self.output)})]
        states = []
        def poll(*a, **kw):
            states.append(store.load(self.pid)['images']['img-test']['state'])
            result = responses.pop(0)
            if isinstance(result, Exception): raise result
            return result
        with patch.object(bridge, '_call', side_effect=poll):
            bridge._image_poll(self.pid, 'img-test', self.run, 'mock', wait=True)
        rec = store.load(self.pid)['images']['img-test']
        self.assertEqual(rec['state'],'completed')
        self.assertNotIn('error',rec)
        self.assertEqual(rec['previous_errors'],['old error'])
        self.assertNotIn('failed',states)
        self.assertEqual(sum('三方渠道返回生成中状态，继续等待' in str(x) for x in store.load(self.pid)['log']),1)

    def test_resume_unknown_status_keeps_polling(self):
        store.update(self.pid, lambda d: d['images']['img-test'].update(state='failed'))
        responses = [bridge.BridgeError('unrecognized status'), json.dumps({'phase':'completed','output':str(self.output)})]
        with patch.object(bridge, '_call', side_effect=responses) as cli:
            bridge.image_resume(self.pid,'img-test')
        self.assertEqual(cli.call_count,2)
        self.assertEqual(store.load(self.pid)['images']['img-test']['state'],'completed')

    def test_restore_local_without_cli(self):
        store.update(self.pid, lambda d: d['images']['img-test'].update(state='completed'))
        with patch.object(bridge, '_call', side_effect=AssertionError('本地恢复不可调用远端')):
            bridge.image_resume(self.pid,'img-test')
            bridge.image_resume(self.pid,'img-test')
        data = store.load(self.pid)
        self.assertEqual(len([a for a in data['assets'] if a['id']=='img-test']),1)
        self.assertNotIn('error',data['images']['img-test'])

    def test_restore_download_checks_hash_without_submit(self):
        self.output.unlink()
        store.update(self.pid, lambda d: d['images']['img-test'].update(state='completed'))
        def resume(args, *a, **kw):
            self.assertEqual(args[1],'resume')
            self.output.write_bytes(b'offline image')
            return json.dumps({'phase':'completed','output':str(self.output)})
        with patch.object(bridge, '_call', side_effect=resume):
            bridge.image_resume(self.pid,'img-test')
        self.assertEqual(store.load(self.pid)['assets'][0]['sha256'],self.sha)
        store.update(self.pid, lambda d: d['images']['img-test'].update(output_sha256='bad'))
        with self.assertRaisesRegex(bridge.BridgeError,'校验'):
            bridge._image_done(self.pid,'img-test',self.output)

    def test_restore_transient_failure_can_retry_same_completed_task(self):
        self.output.unlink()
        store.update(self.pid,lambda d:d['images']['img-test'].update(state='completed'))
        store.atomic_write_json(self.run / 'image-state.json', {'phase':'completed','remote_task_id':'remote-test','output':str(self.output),'output_sha256':self.sha})
        def retry(args,*a,**kw):
            self.assertTrue(args[0].endswith('restore_image.py'))
            self.output.write_bytes(b'offline image')
            return json.dumps({'phase':'completed','output':str(self.output)})
        with patch.object(bridge,'_call',side_effect=bridge.BridgeError('temporary download error')):
            with self.assertRaises(bridge.BridgeError): bridge.image_resume(self.pid,'img-test')
        with patch.object(bridge,'_call',side_effect=retry):
            bridge.image_resume(self.pid,'img-test')
        self.assertEqual(store.load(self.pid)['images']['img-test']['state'],'completed')
        self.assertNotIn('restore_error',store.load(self.pid)['images']['img-test'])

    def test_completed_engine_state_restore_uses_get_and_validates_digest(self):
        from tools.restore_image import restore
        from unittest.mock import Mock
        self.output.unlink()
        store.atomic_write_json(self.run / 'image-state.json', {'phase':'completed','remote_task_id':'remote-test','output':str(self.output),'output_sha256':self.sha})
        client = Mock()
        client._request.return_value = {'data':{'status':'completed','result':{'image_url':'https://offline.invalid/result.png'}}}
        client.download_video.side_effect = lambda url,out: out.write_bytes(b'offline image')
        result = restore(self.run, client)
        self.assertEqual(result['output_sha256'], self.sha)
        client._request.assert_called_once_with('GET','/images/remote-test',None)
        self.output.unlink()
        client.download_video.side_effect = lambda url,out: out.write_bytes(b'corrupt image')
        with self.assertRaisesRegex(ValueError,'校验'):
            restore(self.run, client)
        self.assertFalse(self.output.exists())

    def test_temporary_body_retry_and_auth(self):
        transient = {'code':500,'msg':'上游请求失败'}
        for payloads, count, fail in [([transient,transient,{'choices':[]}],3,False),([transient]*4,4,True),([{'code':401}],1,True)]:
            responses = [io.BytesIO(json.dumps(p).encode()) for p in payloads]
            with patch.object(llm,'_ssl_context',return_value=None), patch('urllib.request.urlopen',side_effect=responses) as call:
                if fail:
                    with self.assertRaises(llm.LLMError) as error: llm._post('https://offline.invalid',{},'mock',1,'jimmy')
                    if count == 4: self.assertIn('已自动重试 3 次',str(error.exception))
                else: self.assertEqual(llm._post('https://offline.invalid',{},'mock',1,'jimmy'),{'choices':[]})
                self.assertEqual(call.call_count,count)

    def test_outdated_write_rejected_before_mutation(self):
        handler = object.__new__(server.Handler)
        handler.headers = {'X-Client-Build':'outdated'}
        handler.path = f'/api/projects/{self.pid}/breakdown'
        before = (self.d / 'project.json').read_bytes()
        with patch.object(handler,'_body_json',return_value={'breakdown':{'ok':True}}), patch.object(handler,'_json') as output:
            handler._dispatch('PUT')
            self.assertEqual(output.call_args.args[1],409)
            self.assertEqual((self.d / 'project.json').read_bytes(),before)
            handler.headers = {'X-Client-Build': server.build_status()['build']}
            handler._dispatch('PUT')
        self.assertEqual(store.load(self.pid)['breakdown'],{'ok':True})

    def test_delete_response_refreshes_camera_validity_without_reordering_script(self):
        store.update(self.pid,lambda d:d.update(assets=[{'id':'img-test','type':'image','file':'image-runs/img-test/result.png'}],
            replacements={'characters':[{'source_id':'C1','action':'replace','camera_assets':{'S1':'img-test'}}]},
            script={'tasks':[{'id':'seg-01','image_ids':['img-test','product']}]}))
        handler=object.__new__(server.Handler);handler.headers={'X-Client-Build': server.build_status()['build']}
        handler.path=f'/api/projects/{self.pid}/assets/img-test'
        with patch.object(handler,'_json') as output:
            handler._dispatch('DELETE')
        result=output.call_args.args[0]
        self.assertIsNone(result['camera_current']['C1']['S1'])
        self.assertEqual(result['script']['tasks'][0]['image_ids'],['img-test','product'])
        self.assertEqual(store.load(self.pid)['assets'],[])

    def test_build_changes_with_content(self):
        web = self.temp / 'web'; web.mkdir()
        page = web / 'index.html'; page.write_text('one')
        with patch.object(server,'WEB',web):
            first = server.read_build(); page.write_text('two')
            self.assertNotEqual(first,server.read_build())

if __name__ == '__main__': unittest.main()
