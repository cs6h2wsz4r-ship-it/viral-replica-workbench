"""V6b bounded visual checks and sanitized failure diagnostics; no remote I/O."""
import copy
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from console import config, final_preview, gateway, llm, media, segment_review, store
from console.gateway import errors, reasoning, retry
from console.gateway.adapters import official
from tools import test_v6
from tools.v6b_plan_cases import V6bPlanTests


class ReviewBudgetTests(unittest.TestCase):
    setUp = test_v6.V6Tests.setUp
    frame = test_v6.V6Tests.frame
    report = test_v6.V6Tests.report
    run_record = test_v6.V6Tests.run_record
    fix = test_v6.V6Tests.fix
    output = test_v6.V6Tests.output

    def request(self, duration):
        folder = self.d / 'review-budget'; folder.mkdir(exist_ok=True)
        context = self.run_record()['review_inputs']
        with patch.object(media, 'probe', return_value={'duration': duration, 'video_duration': duration, 'fps': 25}):
            return segment_review.messages(context, self.d, self.d / 'clips/d1.mp4', folder)[0]

    def test_long_sampling_covers_last_decodable_frame_short_request_unchanged(self):
        request = self.request(13)
        self.assertEqual(len(self.frames), 20)
        self.assertEqual(self.frames[0], 0)
        self.assertAlmostEqual(self.frames[-1], 12.96, places=3)
        gaps = [b-a for a,b in zip(self.frames, self.frames[1:])]
        self.assertLess(max(gaps)-min(gaps), .002)
        labels = [p['text'] for p in request[1]['content'] if p.get('text', '').startswith('t=')]
        self.assertEqual(labels, [f't={t:.1f}s' for t in self.frames])
        self.assertNotIn('每 0.5 秒', request[1]['content'][0]['text'])
        self.frames.clear()
        short = self.request(7)
        self.assertEqual(self.frames, [i/2 for i in range(14)])
        self.assertEqual(short[0]['content'], segment_review.SYSTEM)
        self.assertEqual(short[1]['content'][0]['text'], '结果视频时长：7.000s。以下按每 0.5 秒采样。')
        # V6a request contract: independently reconstruct the old short request,
        # including every reference label, frozen requirement and image byte.
        context=self.run_record()['review_inputs']; segment=context['segment']
        old=[dict(type='text',text='结果视频时长：7.000s。以下按每 0.5 秒采样。')]
        for i in range(14):
            old.append(dict(type='text',text=f't={i/2:.1f}s'))
            old.extend(llm.image_part(self.d/'review-budget'/f'frame-{i:03d}.jpg'))
        for ref in context['references']+context['source_cells']:
            key=ref.get('key')
            label=((str(ref.get('tag') or '')+' '+str(ref.get('description') or ref.get('label') or key)).strip()
                   if key else f'原片单格 {ref["id"]}')
            old.append(dict(type='text',text=label));old.extend(llm.image_part(self.d/ref['file']))
        old.append(dict(type='text',text='本版生成要求：'+json.dumps({k:segment.get(k) for k in
            ('cells','what','check','prompt','mode','source_start','source_end','stretch')},ensure_ascii=False)
            +'\n原片格子时间（原片时钟，结果时间以生成要求为准）：'+json.dumps([{k:c.get(k) for k in ('id','start','end')} for c in context['source_cells']],ensure_ascii=False)
            +'\n商品已确认信息：'+json.dumps(context.get('product') or {},ensure_ascii=False)))
        old.append(dict(type='text',text='未转录'))
        legacy=[dict(role='system',content=segment_review.SYSTEM),dict(role='user',content=old)]
        self.assertEqual(json.dumps(short,ensure_ascii=False).encode(),json.dumps(legacy,ensure_ascii=False).encode())
        self.frames.clear()
        self.request(10)
        self.assertEqual(len(self.frames), 20)

    def test_budget_width_before_count_retains_all_fixed_evidence(self):
        from console import review_request
        seen = []
        fixed = {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,REF'}}
        def build(times, width):
            seen.append((list(times), width))
            parts = [fixed]
            for second in times:
                parts.extend([{'type':'text','text':f't={second:.1f}s'}, {'type':'image_url','image_url':{'url':'x'*(250_000 if width==540 else 180_000)}}])
            return [{'role':'user','content':parts}]
        request = review_request.bounded(build, list(range(20)))
        self.assertEqual(seen[0], (list(range(20)), 540))
        self.assertEqual(seen[1], (list(range(20)), 432))
        self.assertTrue(all(w==432 for _,w in seen[2:]))
        self.assertGreaterEqual(len(seen[-1][0]),12)
        self.assertEqual((seen[-1][0][0],seen[-1][0][-1]),(0,19))
        self.assertEqual(request[0]['content'][0],fixed)
        self.assertLessEqual(review_request.metrics(request)['request_bytes'],3_000_000)

    def test_budget_never_drops_below_twelve_or_removes_photos(self):
        from console import review_request
        counts=[]
        def build(times,width):
            counts.append(len(times))
            return [{'role':'user','content':[{'type':'image_url','image_url':{'url':'x'*260_000}} for _ in times]}]
        with self.assertRaises(errors.LLMError) as error:
            review_request.bounded(build, list(range(20)))
        self.assertEqual(min(counts),12)
        self.assertEqual(error.exception.kind,'too_large')
        self.assertEqual(error.exception.error_detail['image_count'],12)
        request=[{'role':'user','content':[{'type':'image_url','image_url':{'url':'x'}} for _ in range(33)]}]
        original=copy.deepcopy(request)
        with self.assertRaises(errors.LLMError): review_request.enforce(request)
        self.assertEqual(request,original)

    def test_final_combined_frames_are_bounded_and_keep_seam_sides(self):
        from console import review_request
        folder=self.d/'final-budget';folder.mkdir()
        data=store.load(self.pid)
        saved={'timeline':[{'id':'d1','start':0,'end':10,'what':'前段'},{'id':'d2','start':10,'end':20,'what':'后段'}]}
        with patch.object(media,'probe',return_value={'duration':20,'fps':25}):
            request,_,_=final_preview.check_messages(data,saved,self.d,self.d/'clips/d1.mp4',folder)
        self.assertLessEqual(len(self.frames),20)
        self.assertEqual(self.frames[0],0)
        self.assertAlmostEqual(self.frames[-1],19.96,places=3)
        self.assertTrue(any(8.5<=t<10 for t in self.frames))
        self.assertTrue(any(10<t<=11.5 for t in self.frames))
        self.assertLessEqual(review_request.metrics(request)['image_count'],32)
        self.assertEqual(request[0]['content'],final_preview.FINAL_SYSTEM)

    def test_fix_plan_uses_same_budget_without_removing_reference_images(self):
        from console import review_request, fix_plan, director
        data=store.load(self.pid); run=self.run_record()
        oversized=[{'role':'system','content':'rules'},{'role':'user','content':[{'type':'image_url','image_url':{'url':'x'}} for _ in range(33)]}]
        with patch.object(director,'messages',return_value=oversized):
            with self.assertRaises(errors.LLMError) as error:
                fix_plan.messages(data,'d1',run,[0],'',self.d)
        self.assertEqual(error.exception.kind,'too_large')

    def test_review_saves_diagnostics_and_records_too_large_usage(self):
        detail=errors.diagnostic_detail(status=413,detail={'error':{'type':'invalid_request_error',
            'message':'Too many images {"request":{"prompt":"PRIVATE"}}'}})
        failure=errors.LLMError('请求太大',kind='too_large',error_detail=detail)
        segment_review.start(self.pid,'d1','r1',force=True)
        with patch.object(gateway,'reason_checked',side_effect=lambda step,request,**kw:gateway._reason(step,request,checked=True,**kw)), \
                patch.object(llm,'chat_json_checked',side_effect=failure) as remote:
            self.workers.pop()()
        review=self.run_record()['review']
        self.assertEqual(review['status'],'failed')
        self.assertEqual(review['error_kind'],'too_large')
        self.assertEqual(review['error_detail']['http_status'],413)
        self.assertGreater(review['error_detail']['request_bytes'],0)
        self.assertGreater(review['error_detail']['image_count'],0)
        self.assertNotIn('PRIVATE',json.dumps(review))
        self.assertEqual(store.load(self.pid)['usage'][-1]['error_kind'],'too_large')
        remote.assert_called_once()


class SafeDiagnosticsTests(unittest.TestCase):
    def test_413_and_text_too_large_are_not_unknown_or_retryable(self):
        for status,detail in [(413,''),(400,'{"error":{"type":"invalid_request_error","message":"Too many images"}}'),(0,'图片太多或太大，超过模型渠道的请求上限'),(400,'Request body too large'),(400,'maximum number of images is 32')]:
            with self.subTest(status=status,detail=detail):
                self.assertEqual(retry.classify(status,detail),'too_large')
                self.assertFalse(retry.retryable(status,detail))
        report=segment_review.failure_report(errors.LLMError('请求过大',kind='too_large'))
        self.assertEqual(report['error_reason'],'请求太大')

    def test_official_wrapped_error_keeps_only_safe_diagnostics(self):
        payload={'error':{'type':'invalid_request_error','message':'Too many images Authorization: Bearer sk-secret headers={cookie: hidden} request={images: PRIVATE}'}}
        request=[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/jpeg;base64,PRIVATE'}}]}]
        def send(*args,**kw):
            raise urllib.error.HTTPError('https://example.invalid',413,'Too large',{},io.BytesIO(json.dumps(payload).encode()))
        with patch.object(llm,'_ssl_context',return_value=None),patch('urllib.request.urlopen',side_effect=send),patch.object(llm,'chat_json',side_effect=lambda *args,**kw:official.post('https://example.invalid',{'model':'mock','messages':request},'fake',1)):
            with self.assertRaises(errors.CheckedLLMError) as caught:
                reasoning.chat_json_checked('openai',request,model='mock')
        report=segment_review.failure_report(caught.exception,request)
        detail=report['error_detail']
        self.assertEqual(report['error_kind'],'too_large')
        self.assertEqual(detail['http_status'],413)
        self.assertEqual(detail['error_type'],'invalid_request_error')
        self.assertEqual(detail['image_count'],1)
        self.assertGreater(detail['request_bytes'],0)
        serialized=json.dumps(detail)
        for forbidden in ('sk-secret','Authorization','headers','cookie','hidden','request=','PRIVATE','data:image'):
            self.assertNotIn(forbidden,serialized)
        self.assertLessEqual(len(detail['message']),200)

    def test_arbitrary_exception_diagnostics_drop_body_headers_and_images(self):
        exc=TimeoutError('network timed out Authorization: Bearer sk-hidden request={secret: sensitive}')
        report=segment_review.failure_report(exc)
        text=json.dumps(report)
        for secret in ('sk-hidden','sensitive','Authorization','request={'):
            self.assertNotIn(secret,text)
        self.assertEqual(report['error_kind'],'transient')

    def test_json_echoed_request_headers_and_size_wording(self):
        for message in ('Too many images {"headers": {"X-Request-Token": "fake-sensitive"}, "request": {"prompt": "PRIVATE"}}',
                        'Failure {"messages":[{"role":"user","content":"PRIVATE"}]}',
                        'Failure Authorization Bearer other-secret'):
            detail=errors.diagnostic_detail(status=400,detail={'error':{'message':message}})
            for forbidden in ('headers','X-Request-Token','fake-sensitive','request','prompt','PRIVATE','messages','Authorization','other-secret'):
                self.assertNotIn(forbidden,detail['message'])
        for message in ('Request exceeds maximum size','payload_too_large','too_many_images','image_limit_exceeded','maximum images allowed: 32'):
            self.assertEqual(retry.classify(400,message),'too_large')

    def test_json_repair_also_respects_review_budget_before_second_call(self):
        from console import review_request
        messages=review_request.enforce([{'role':'user','content':[{'type':'image_url','image_url':{'url':'x'*2_990_000}}]}])
        with patch.object(llm,'chat',return_value={'content':'x'*12_000}) as chat:
            with self.assertRaises(errors.LLMError) as error:
                reasoning.chat_json('openai',messages)
        self.assertEqual(error.exception.kind,'too_large')
        chat.assert_called_once()


class RealFrameTests(unittest.TestCase):
    def test_rounded_video_duration_cannot_seek_past_last_frame(self):
        from console import review_request
        with tempfile.TemporaryDirectory(dir=config.ROOT/'runtime/temp') as temp, \
                patch.dict(os.environ,{'PATH':str(config.ROOT/'runtime/ffmpeg')+os.pathsep+os.environ['PATH']}):
            folder=Path(temp)
            for fps,count in [(24,313),(60,781)]:
                with self.subTest(fps=fps):
                    clip=folder/f'video-{fps}.mp4'
                    media._run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',
                                f'testsrc2=s=64x96:r={fps}', '-frames:v',str(count),'-c:v','libx264','-preset','ultrafast',str(clip)])
                    info=media.probe(clip)
                    second=review_request.last_frame(info['video_duration'],info['fps'])
                    self.assertAlmostEqual(second,13,places=3)
                    result=media.extract_frame(clip,second,folder/f'last-{fps}.jpg')
                    self.assertGreater(result.stat().st_size,0)

    def test_fractional_fps_last_frame_decodes_and_request_has_twenty_frames(self):
        from PIL import Image
        from console import review_request
        with tempfile.TemporaryDirectory(dir=config.ROOT/'runtime/temp') as temp, \
                patch.dict(os.environ,{'PATH':str(config.ROOT/'runtime/ffmpeg')+os.pathsep+os.environ['PATH']}):
            folder=Path(temp); clip=folder/'video.mp4'; frame=folder/'ref.png'; samples=folder/'samples';samples.mkdir()
            Image.new('RGB',(16,16),'gray').save(frame)
            media._run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',
                        'testsrc2=s=64x96:r=30000/1001:d=13','-c:v','libx264','-preset','ultrafast',str(clip)])
            context=dict(segment={},references=[dict(key='ref',file='ref.png',sha256=store.sha256_file(frame))],
                         source_cells=[dict(id='c01',file='ref.png',sha256=store.sha256_file(frame))])
            with patch.object(media,'extract_frame',wraps=media.extract_frame) as extract:
                request,duration=segment_review.messages(context,folder,clip,samples)
            self.assertEqual(extract.call_count,20)
            self.assertEqual(extract.call_args_list[0].args[1],0)
            self.assertLess(extract.call_args_list[-1].args[1],duration)
            self.assertTrue(all(p.stat().st_size for p in samples.glob('frame-*.jpg')))
            self.assertEqual(review_request.metrics(request)['image_count'],22)


if __name__=='__main__':
    unittest.main()
