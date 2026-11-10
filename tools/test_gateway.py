"""Round 21 gateway contracts. All configuration and transport are mocked."""
import ast
import copy
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.project_fixture import load_project
from console import config, llm, store

class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.values = {**config.DEFAULTS, 'OPENAI_API_KEY': 'offline', 'JIMMY_API_KEY': 'offline'}
        self.enterContext(patch.object(config, 'load', side_effect=lambda: dict(self.values)))
        self.enterContext(patch.object(config, '_parse', return_value=self.values))
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('mock required')))

    def test_gateway_exists(self):
        self.assertTrue((ROOT / 'console/gateway/__init__.py').exists(), 'unified gateway missing')

    def test_routes(self):
        from console.gateway.routes import resolve
        for step in ('analysis', 'script', 'check', 'dialogue_proposal', 'camera_prompt'):
            self.assertEqual(resolve(step), {'channel':'openai', 'model':'gpt-5.6-sol'})
        self.assertEqual(resolve('camera_image'), {'channel':'official','model':'gpt-image-2'})
        self.assertEqual(resolve('video'), {'channel':'third_party','model':'seedance2.0-mini-video'})
        project = {'script':{'provider':'deepseek'},'routes':{'script':'deepseek:project-model'}}
        self.assertEqual(resolve('check', project)['model'], 'project-model')
        self.assertEqual(resolve('dialogue_proposal', project)['channel'], 'deepseek')
        self.assertEqual(resolve('script', project, 'jimmy:page-model')['model'], 'page-model')
        self.assertEqual(resolve('camera_prompt', {'routes':{'analysis':'jimmy:gemini'}})['model'], 'gemini')
        self.values['OPENAI_API_KEY'] = ''
        self.assertEqual(resolve('analysis')['channel'], 'jimmy')
        self.assertEqual(resolve('camera_image')['channel'], 'third_party')
        self.values['DEFAULT_LLM'] = 'deepseek'
        self.assertEqual(resolve('script')['channel'], 'deepseek')

    def test_settings_routes_save_without_accessing_live_configuration(self):
        from console.gateway import routes
        with tempfile.TemporaryDirectory(dir=ROOT/'runtime/temp') as tmp:
            path=Path(tmp)/'mock-settings.conf'
            with patch.object(config,'ENV_FILE',path):
                config.save({'ROUTE_SCRIPT':'deepseek:chosen','ROUTE_CHECK':'follow:script'})
                self.assertIn('ROUTE_SCRIPT=deepseek:chosen',path.read_text())
                before=path.read_bytes()
                with self.assertRaises(ValueError):config.save({'ROUTE_CHECK':'follow:video'})
                self.assertEqual(path.read_bytes(),before)

    def test_usage_success_failure_and_image(self):
        from console import gateway
        project = {}
        with patch.object(llm,'chat',return_value=('OK', {'usage':{'prompt_tokens':7,'completion_tokens':4,'completion_tokens_details':{'reasoning_tokens':2}}})):
            gateway.reason('script',[],max_tokens=16,project=project)
        self.assertEqual(len(project['usage']),1)
        self.assertEqual(project['usage'][0]['tokens'],{'prompt':7,'completion':4,'reasoning':2})
        self.assertEqual(project['usage'][0]['billing'],{'unit':'tokens','amount':11})
        with patch.object(llm,'chat',side_effect=llm.LLMError('官方额度不足')):
            with self.assertRaises(Exception): gateway.reason('script',[],max_tokens=16,project=project)
        self.assertFalse(project['usage'][-1]['ok'])
        self.assertEqual(project['usage'][-1]['error_kind'],'quota')
        with patch('console.gateway.adapters.official.image_generate',return_value={'usage':{'input_tokens':5,'output_tokens':8}}):
            gateway.image_generate({'model':'gpt-image-2'},Path('unused'),project=project)
        self.assertEqual(len(project['usage']),3)
        self.assertEqual(project['usage'][-1]['tokens']['prompt'],5)

    def test_business_imports(self):
        # llm media encoders and error classes are the explicit compatibility whitelist.
        allowed = {'image_part','video_part','LLMError'}
        for name in ('console/pipeline.py','console/motion.py','server.py'):
            tree = ast.parse((ROOT/name).read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.ImportFrom):
                    self.assertFalse(any(a.name in {'openai_image','chain5_image'} for a in node.names),name)
                if isinstance(node,ast.Attribute) and isinstance(node.value,ast.Name) and node.value.id == 'llm':
                    self.assertIn(node.attr,allowed,name)



def frozen(name):
    spec = importlib.util.spec_from_file_location('console._round21_' + name,
        ROOT/'tools/fixtures/round21'/f'{name}_before.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ByteCompatibilityTests(unittest.TestCase):
    def setUp(self):
        GatewayTests.setUp(self)
        self.values.update(DEEPSEEK_API_KEY='offline',DOUBAO_API_KEY='offline',CUSTOM_API_KEY='offline',
                           CUSTOM_MODEL='custom',CUSTOM_BASE_URL='https://offline.invalid/v1')
        self.before = frozen('llm')
        for module in (llm,self.before):
            self.enterContext(patch.object(module,'_ssl_context',return_value=None))
            self.enterContext(patch.object(module,'_jimmy_dns_shim'))
        self.requests = []

    def test_reason_request_bytes_all_formats(self):
        from console import gateway
        messages = [{'role':'system','content':'只回答 OK'}, {'role':'user','content':[
            {'type':'text','text':'中文 / image'}, {'type':'image_url','image_url':{'url':'data:image/png;base64,b2ZmbGluZQ=='}}]}]
        payload = {'choices':[{'message':{'content':'OK'}}], 'usage':{'prompt_tokens':7,'completion_tokens':4}}
        for channel,model in [('openai','gpt-5.6-sol'),('deepseek','deepseek/deepseek-v4.1-flash-1017'),
                              ('jimmy','gpt-5.6-sol'),('jimmy','gemini-3.1-pro-preview'),('doubao','doubao-seed-2-0-pro-260215'),('custom','custom')]:
            self.values['JIMMY_LLM_MODEL'] = model if channel=='jimmy' else self.values['JIMMY_LLM_MODEL']
            response = {'code':0,'data':{'candidates':[{'content':{'parts':[{'text':'OK'}]}}]}} if model.startswith('gemini') else payload
            captured=[]
            def send(req,**kw):
                captured.append((req.full_url,req.method,req.data,dict(req.headers)))
                return io.BytesIO(json.dumps(response).encode())
            with self.subTest(channel=channel,model=model),patch('urllib.request.urlopen',side_effect=send):
                self.before.chat(channel,copy.deepcopy(messages),max_tokens=123,temperature=.3,model=model)
                gateway.reason('analysis',copy.deepcopy(messages),override={'channel':channel,'model':model},max_tokens=123,temperature=.3)
                self.assertEqual(captured[0],captured[1])
                self.assertEqual(len(captured),2)
                print(f'字节一致：{channel}/{model} 推理 {len(captured[0][2])} bytes')

    def test_official_image_multipart_bytes(self):
        import base64, hashlib
        from types import SimpleNamespace
        from console import gateway
        baseline = frozen('openai_image')
        with tempfile.TemporaryDirectory(dir=ROOT/'runtime/temp') as tmp:
            folder=Path(tmp); image=folder/'reference.png';image.write_bytes(b'original reference bytes')
            preview={'model':'gpt-image-2','prompt':'中文提示词\nsecond line','size':'1024x1536','quality':'auto',
                     'images':[{'path':str(image),'sha256':hashlib.sha256(image.read_bytes()).hexdigest()}]}
            captures=[]
            def send(req,**kw):
                captures.append((req.full_url,req.data,dict(req.headers)))
                return io.BytesIO(json.dumps({'data':[{'b64_json':base64.b64encode(b'image-output').decode()}]}).encode())
            with patch('uuid.uuid4',return_value=SimpleNamespace(hex='fixed-boundary')), patch('urllib.request.urlopen',side_effect=send):
                baseline.generate(preview,folder/'old.png')
                gateway.image_generate(preview,folder/'new.png')
            self.assertEqual(captures[0],captures[1])
            print(f'字节一致：官方生图 multipart {len(captures[0][1])} bytes')

    def test_third_party_image_job_bytes(self):
        from console import gateway,jimmy_bridge
        baseline=frozen('jimmy_bridge')
        with tempfile.TemporaryDirectory(dir=ROOT/'runtime/temp') as tmp,patch.object(config,'PROJECTS_DIR',Path(tmp)):
            pid=store.create('gateway image byte test')['id'];directory=store.project_dir(pid)
            (directory/'assets/ref.png').write_bytes(b'ref')
            store.update(pid,lambda d:d['assets'].append({'id':'ref','type':'image','file':'assets/ref.png'}))
            captures=[]
            def prepare(args,cwd):
                raw=Path(args[args.index('--job')+1]).read_bytes();captures.append((raw,args))
                folder=Path(args[args.index('--run')+1]);folder.mkdir(exist_ok=True)
                store.atomic_write_json(folder/'image-job.preview.json',json.loads(raw))
                return 'offline'
            with patch.object(store,'new_id',return_value='img-byte-fixed'),patch.object(baseline,'_call',side_effect=prepare),patch.object(jimmy_bridge,'_call',side_effect=prepare):
                baseline.image_preview(pid,'固定提示词',['ref'],'9:16','gpt-image-2','2k',None)
                gateway.image_preview('camera_image',pid,'固定提示词',['ref'],'9:16','gpt-image-2','2k',None,override='third_party:gpt-image-2')
            self.assertEqual(captures[0],captures[1])
            print(f'字节一致：三方渠道生图 job JSON {len(captures[0][0])} bytes')

    def test_two_old_video_projects_gateway_bytes(self):
        from console import gateway,jimmy_bridge,media,motion
        baseline=frozen('jimmy_bridge')
        spec=importlib.util.spec_from_file_location('_round21_package',config.CHAIN5_SCRIPTS/'chain5_package.py')
        package=importlib.util.module_from_spec(spec);spec.loader.exec_module(package)
        for source_pid in ('p1027232428-166a','p1027195039-ccc0'):
            with self.subTest(project=source_pid),tempfile.TemporaryDirectory(dir=ROOT/'runtime/temp') as tmp,patch.object(config,'PROJECTS_DIR',Path(tmp)):
                original=load_project(source_pid)
                pid=store.create('gateway video byte test')['id'];directory=store.project_dir(pid)
                for entry in original['assets']+[original['source']]:
                    path=directory/entry['file'];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'mocked media')
                hashes={str((directory/a['file']).resolve()):a['sha256'] for a in original['assets']}
                checksum=lambda path:hashes.get(str(Path(path).resolve()),'0'*64)
                info={'duration':15,'fps':30,'width':720,'height':1280,'audio_stream_count':0,'size_bytes':64}
                def cut(src,start,length,out,**kw):out.write_bytes(b'mock processed video')
                commands=[]
                def prepare(args,cwd,**kw):
                    commands.append(args)
                    package.prepare(Path(args[args.index('--plan')+1]),Path(args[args.index('--output')+1]));return 'offline'
                captures=[]
                with patch.object(store,'sha256_file',side_effect=checksum),patch.object(package,'_file_digest',side_effect=checksum), \
                     patch.object(package,'_probe',return_value=info),patch.object(package,'_image_format',return_value='png'), \
                     patch.object(media,'cut_clip',side_effect=cut),patch.object(media,'probe',return_value=info), \
                     patch.object(media,'caption_residue',return_value={'status':'checked','samples':[],'suspected':[]}), \
                     patch.object(motion,'compare_actions',return_value=([],[])),patch.object(baseline,'_call',side_effect=prepare), \
                     patch.object(jimmy_bridge,'_call',side_effect=prepare),patch.object(baseline,'_next_rev',return_value='r1'), \
                     patch.object(jimmy_bridge,'_next_rev',return_value='r1'):
                    for invoke in (baseline.preview,gateway.video_preview):
                        store.update(pid,lambda d:d.update(copy.deepcopy(original),runs={}))
                        record=invoke(pid,original['script']['tasks'][0]['id'])
                        captures.append((directory/record['package']/'review/jimmy-job.preview.json').read_bytes())
                        import shutil
                        shutil.rmtree(directory/record['package'])
                self.assertEqual(captures[0],captures[1]);self.assertEqual(commands[0],commands[1])
                print(f'字节一致：{source_pid} 视频预览 {len(captures[0])} bytes')


class AsyncUsageTests(unittest.TestCase):
    def test_nested_worker_cannot_finish_parent_ledger(self):
        from console.gateway import usage
        project={}
        state,token=usage.begin(project,'video','third_party','m','video')
        def parent():
            usage.wrap_worker(lambda:None)()
            raise RuntimeError('quota exceeded')
        worker=usage.wrap_worker(parent)
        usage.reset(token)
        with self.assertRaises(RuntimeError):worker()
        self.assertEqual(len(project['usage']),1)
        self.assertFalse(project['usage'][0]['ok'])
        self.assertEqual(project['usage'][0]['error_kind'],'quota')

    def test_deferred_bridge_failure_classified(self):
        from console.gateway import usage
        from console.jimmy_bridge import BridgeError
        project={};state,token=usage.begin(project,'camera_image','third_party','m','image')
        def failed(): raise BridgeError('insufficient_quota')
        worker=usage.wrap_worker(failed);usage.reset(token)
        with self.assertRaises(BridgeError):worker()
        self.assertEqual(project['usage'][0]['error_kind'],'quota')


class ErrorAndConnectionTests(unittest.TestCase):
    def setUp(self):
        GatewayTests.setUp(self)
        self.values.update(DEEPSEEK_API_KEY='offline',DOUBAO_API_KEY='offline',CUSTOM_API_KEY='offline',CUSTOM_MODEL='custom',CUSTOM_BASE_URL='https://offline.invalid')
        self.enterContext(patch.object(llm,'_ssl_context',return_value=None))
        self.enterContext(patch.object(llm,'_jimmy_dns_shim'))
        self.sleep=self.enterContext(patch.object(llm.time,'sleep'))

    def test_all_channels_error_kinds_no_quota_retry(self):
        import urllib.error
        from console import gateway
        for channel in ('openai','jimmy','deepseek','doubao','custom'):
            for status,code,expected in [(401,'invalid_api_key','auth'),(429,'insufficient_quota','quota'),(500,'server_error','transient'),(200,'server_error','transient')]:
                count=[];project={}
                detail=json.dumps({'error':{'code':code,'message':'Jimmy upstream' if expected=='transient' else code}}).encode()
                def send(req,**kw):
                    count.append(req.full_url)
                    if status==200:return io.BytesIO(detail)
                    raise urllib.error.HTTPError(req.full_url,status,'offline',{},io.BytesIO(detail))
                with self.subTest(channel=channel,status=status),patch('urllib.request.urlopen',side_effect=send):
                    with self.assertRaises(gateway.GatewayError) as caught:
                        gateway.reason_checked('check',[],override=channel,max_tokens=16,project=project,analysis={'provider':'jimmy','model':'fallback'})
                    self.assertEqual(caught.exception.kind,expected)
                    self.assertEqual(len(count),1 if expected in {'auth','quota'} else 4)
                    self.assertTrue(all(url==count[0] for url in count),'must not cross channel')
                    self.assertRegex(caught.exception.message,r'[\u4e00-\u9fff]')
                    self.assertNotRegex(caught.exception.message,r'(?i)jimmy|吉米')
                    self.assertNotRegex(caught.exception.technical,r'(?i)jimmy|吉米')
                    self.assertEqual(project['usage'][-1]['error_kind'],expected)

    def test_connection_wire_request_and_no_ledger(self):
        from console import gateway
        captures=[]
        def send(req,**kw):
            captures.append(json.loads(req.data))
            return io.BytesIO(b'{"choices":[{"message":{"content":"OK"}}]}')
        with patch('urllib.request.urlopen',side_effect=send),patch.object(store,'update',side_effect=AssertionError('no project ledger')):
            self.assertEqual(gateway.test_connection('openai')['status'],'可用')
            self.assertEqual(gateway.test_connection('deepseek')['status'],'可用')
        for body in captures:
            self.assertEqual(body['messages'],[{'role':'user','content':'Reply with OK'}])
            self.assertEqual(body.get('max_completion_tokens',body.get('max_tokens')),16)

    def test_legacy_checked_fallback_accounts_per_actual_channel(self):
        from console import gateway
        project={}
        def exhausted(n):
            return {'choices':[{'message':{'content':''},'finish_reason':'length'}],
                    'usage':{'prompt_tokens':1,'completion_tokens':n,'completion_tokens_details':{'reasoning_tokens':n}}}
        responses=[exhausted(16),exhausted(64),{'choices':[{'message':{'content':'{}'}}],'usage':{'prompt_tokens':2,'completion_tokens':3}}]
        with patch('urllib.request.urlopen',side_effect=[io.BytesIO(json.dumps(p).encode()) for p in responses]):
            value,meta=gateway.reason_checked('check',[],override='deepseek:model-a',max_tokens=16,project=project,analysis={'provider':'jimmy','model':'model-b'})
        rows=project['usage']
        self.assertEqual([(r['channel'],r['model']) for r in rows],[('deepseek','model-a'),('jimmy','model-b')])
        self.assertEqual([r['billing']['amount'] for r in rows],[82,5])
        self.assertEqual([r['ok'] for r in rows],[False,True])
        self.assertEqual(rows[0]['error_kind'],'reasoning_exhausted')

    def test_checked_fallback_without_usage_keeps_unknown_numbers(self):
        from console import gateway
        project={}
        def exhausted(n):return {'choices':[{'message':{'content':''},'finish_reason':'length'}],'usage':{'prompt_tokens':1,'completion_tokens':n,'completion_tokens_details':{'reasoning_tokens':n}}}
        payloads=[exhausted(16),exhausted(64),{'choices':[{'message':{'content':'{}'}}]}]
        with patch('urllib.request.urlopen',side_effect=[io.BytesIO(json.dumps(p).encode()) for p in payloads]):
            gateway.reason_checked('check',[],override='deepseek:model-a',max_tokens=16,project=project,analysis={'provider':'jimmy','model':'model-b'})
        self.assertEqual([(r['channel'],r['ok']) for r in project['usage']],[('deepseek',False),('jimmy',True)])
        self.assertEqual([r['billing']['amount'] for r in project['usage']],[82,None])

    def test_failed_checked_fallback_keeps_each_channel_error(self):
        import urllib.error
        from console import gateway
        project={}
        def exhausted(n):return {'choices':[{'message':{'content':''},'finish_reason':'length'}],'usage':{'prompt_tokens':1,'completion_tokens':n,'completion_tokens_details':{'reasoning_tokens':n}}}
        responses=[io.BytesIO(json.dumps(exhausted(n)).encode()) for n in (16,64)]
        responses.append(urllib.error.HTTPError('https://offline.invalid',429,'offline',{},io.BytesIO(b'{"error":{"code":"insufficient_quota"}}')))
        with patch('urllib.request.urlopen',side_effect=responses):
            with self.assertRaises(gateway.GatewayError):gateway.reason_checked('check',[],override='deepseek:model-a',max_tokens=16,project=project,analysis={'provider':'jimmy','model':'model-b'})
        self.assertEqual([(r['channel'],r['error_kind']) for r in project['usage']],[('deepseek','reasoning_exhausted'),('jimmy','quota')])
        self.assertEqual([r['billing']['amount'] for r in project['usage']],[82,None])

    def test_missing_official_key_fallback_is_before_request_and_test_stays_pinned(self):
        from console import gateway
        self.values['OPENAI_API_KEY']=''
        self.assertEqual(gateway.routes.resolve('script',override='openai:gpt-5.6-sol')['channel'],'jimmy')
        self.assertEqual(gateway.routes.resolve('camera_image',{'routes':{'camera_image':'official:gpt-image-2'}})['channel'],'third_party')
        with patch('urllib.request.urlopen',side_effect=AssertionError('missing key cannot send')):
            self.assertEqual(gateway.test_connection('openai')['status'],'key 无效')

    def test_video_route_reaches_adapter_without_rewriting_default(self):
        from console import gateway
        with patch.object(store,'load',return_value={'routes':{'video':'third_party:seedance2.0-md'}}), \
             patch.object(store,'update'),patch('console.gateway.adapters.third_party.video',return_value={'model':'seedance2.0-md'}) as call:
            gateway.video_preview('offline','seg-01')
            self.assertEqual(call.call_args.kwargs,{'model':'seedance2.0-md'})


class BackgroundLedgerTests(unittest.TestCase):
    def setUp(self):
        GatewayTests.setUp(self)
        self.temp=Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT/'runtime/temp')))
        self.enterContext(patch.object(config,'PROJECTS_DIR',self.temp))
        self.pid=store.create('async ledger')['id']
        from console import jobs
        self.enterContext(patch.object(jobs,'_jobs',{}))

    def test_actual_worker_records_after_response_once_and_uses_real_cost(self):
        from console import gateway,jobs
        from console.gateway import usage
        pid=self.pid
        store.update(pid,lambda d:d.update(images={'img':{'channel':'third_party','preview':{'model':'gpt-image-2'},'run_dir':'image-runs/img'}}))
        directory=store.project_dir(pid)/'image-runs/img';directory.mkdir()
        queue=[]
        class Thread:
            def __init__(self,*,target,**kw):self.target=target
            def start(self):queue.append(self.target)
        def confirm(pid,iid):
            def work():store.atomic_write_json(directory/'image-state.json',{'cost':{'amount':.031,'unit':'JimiCoin'}})
            jobs.start(pid,'img:'+iid,work,'offline');return {'ok':True}
        # Bypass the suite's synchronous job shim only for this controlled queue.
        import importlib.util
        spec=importlib.util.spec_from_file_location('console._gateway_jobs_test',ROOT/'console/jobs.py')
        real=importlib.util.module_from_spec(spec);spec.loader.exec_module(real)
        with patch.object(jobs,'start',side_effect=real.start),patch('threading.Thread',Thread), \
             patch('console.gateway.adapters.third_party.image_confirm',side_effect=confirm):
            gateway.image_confirm(pid,'img')
            self.assertEqual(store.load(pid).get('usage',[]),[])
            queue.pop()()
        rows=store.load(pid)['usage'];self.assertEqual(len(rows),1);self.assertEqual(rows[0]['billing'],{'unit':'点','amount':.031})
        with patch('console.gateway.adapters.third_party.image_resume',return_value={'ok':True}):gateway.image_resume(pid,'img')
        rows=store.load(pid)['usage'];self.assertEqual(len(rows),2);self.assertEqual(sum(r['billing']['amount'] or 0 for r in rows),.031)

    def test_video_refresh_counts_measured_seconds_once(self):
        from console import gateway
        store.update(self.pid,lambda d:d.update(runs={'seg':[{'rev':'r1','model':'seedance2.0-mini-video','billing_seconds':23,'remote_task_id':'offline-task','state':'completed'}]}))
        with patch('console.gateway.adapters.third_party.video',return_value={'phase':'completed'}):
            gateway.video_refresh(self.pid,'seg','r1');gateway.video_refresh(self.pid,'seg','r1')
        rows=store.load(self.pid)['usage'];self.assertEqual(len(rows),2);self.assertEqual([r['billing']['amount'] for r in rows],[23,0])


if __name__ == '__main__':
    import sys
    from tools.run_tests_offline import audit
    sys.addaudithook(audit)
    unittest.main()
