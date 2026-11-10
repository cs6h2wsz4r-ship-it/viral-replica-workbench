"""V9b contracts: synthetic projects, local previews, and stubbed paid boundaries."""
import copy
import importlib
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image
from tools.test_v2_segment_plan import SegmentFixture
from console import config, director, fission, final_preview, gateway, jobs, segment_boards, segment_plan, store, storyboard_images
from console import storyboard_edits as edits
try:
    from console import fission_dashboard as dashboard
except ImportError:
    dashboard = None

ROOT = Path(__file__).resolve().parents[1]
REAL_JOB_START = jobs.start
REAL_JOB_SNAPSHOT = jobs.snapshot


class V9bTests(SegmentFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.assertIsNotNone(dashboard, 'V9b must provide the batch dashboard module')
        self.enterContext(patch.object(config, 'BATCHES_DIR', self.tmp/'batches'))
        self.enterContext(patch.object(config, 'ENV_FILE', self.tmp/'settings-version'))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('no secret reads')))
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('no network')))
        self.paid_image=self.enterContext(patch.object(gateway,'image_confirm',side_effect=self.image_submit))
        self.paid_video=self.enterContext(patch.object(gateway,'video_confirm',side_effect=self.video_submit))
        dashboard._queues.clear();dashboard._admitted.clear()
        self.generated();self.master=self.pid
        self.callbacks=[];self.running={}
        self.enterContext(patch.object(jobs,'start',side_effect=self.schedule))
        self.enterContext(patch.object(jobs,'snapshot',side_effect=lambda pid:copy.deepcopy(self.running.get(pid,{}))))
        self.children=[]

    def schedule(self,pid,key,fn,label):
        if self.running.get(pid,{}).get(key,{}).get('state')=='running':raise RuntimeError('already running')
        state=dict(key=key,state='running',label=label)
        self.running.setdefault(pid,{})[key]=state
        self.callbacks.append((pid,key,fn))
        return state

    def execute(self):
        while self.callbacks:
            pid,key,fn=self.callbacks.pop(0)
            fn();self.running[pid][key]['state']='done'
            dashboard.task_finished(pid,key)

    def child(self,number=1,batch='b1'):
        pid=f'child{number}';directory=store.project_dir(pid);shutil.copytree(self.d,directory)
        data=store.load(self.master);data.update(id=pid,name=f'母版 · 裂变 {number}',
            derived_from=dict(project_id=self.master,fission_number=number,row=number,master_snapshot={'name':'母版'},changes=[]),
            images={},runs={},director=None,fission_preparation={'segments':{s['id']:{'status':'queued'} for s in data['segments_plan']['segments']}})
        store.atomic_write_json(directory/'project.json',data)
        path=config.BATCHES_DIR/(batch+'.json')
        record=json.loads(path.read_text()) if path.exists() else dict(id=batch,master_id=self.master,created_at='2026-11-08T10:0'+batch[-1],items=[])
        record['items'].append(dict(project_id=pid,row=number));store.atomic_write_json(path,record)
        self.children.append(pid);return pid

    def board_preview(self,pid,sid='d01'):
        data=store.load(pid);segment=segment_plan.get_segment(data,sid)
        answer=dict(summary='调整',remove_text=False,questions=[],cells=[dict(cell=c,changes='使用所选照片',keep='构图') for c in segment['cells']])
        with patch.object(gateway,'reason_checked',return_value=(answer,{})):
            edits.generate_plan(pid,sid,{})
        return edits.preview(pid,sid,{})

    def boards(self,pid):
        for s in store.load(pid)['segments_plan']['segments']:
            rec=self.board_preview(pid,s['id']);output=store.project_dir(pid)/'synthetic.png'
            Image.new('RGB',(360,640),'navy').save(output)
            storyboard_images.complete(pid,rec,output)
            store.update(pid,lambda d:d['images'][rec['id']].update(state='completed'))

    def prompts(self,pid):
        data=store.load(pid);directory=store.project_dir(pid);rows=[]
        for s in data['segments_plan']['segments']:
            row={**s,'mode':'image','images':list(s['gen_images']),'prompt':'图1中的动作','check':['构图'],'needs_rewrite':False,
                 'inputs_sig':director.segment_inputs_sig(data,directory,s['id']),'plan_sig':segment_plan.segment_sig(s)}
            row['prompt']=' '.join(f'图{i+1}' for i in range(len(row['images'])))+' 中的动作'
            rows.append(row)
        store.update(pid,lambda d:d.update(director={'format_version':3,'segments':rows}))

    def video_preview(self,pid,sid='d01',billing=8,output=4,reference=4):
        data=store.load(pid);segment=director.get_segment(data,sid);directory=store.project_dir(pid)
        package=f'packages/{sid}-r1';review=directory/package/'review';review.mkdir(parents=True,exist_ok=True)
        job={'tasks':[{'id':sid,'duration':output,'prompt':segment['prompt']}],'media':[]}
        store.atomic_write_json(review/'jimmy-job.preview.json',job)
        store.atomic_write_json(review/'jimmy-handoff.json',{'state':'ready_for_confirmation'})
        rec=dict(rev='r1',state='pending_confirmation',kind='director',director_format_version=3,job_id=pid+'-'+sid,
            director_sig=director.segment_sig(segment),job_sha256=store.sha256_file(review/'jimmy-job.preview.json'),
            preview=job,package=package,billing_seconds=billing,output_seconds=output,reference_seconds=reference,
            billing_note=f'输出 {output}s + 参考视频 {reference}s',review_inputs={'segment':copy.deepcopy(segment)})
        store.update(pid,lambda d:d.setdefault('runs',{}).setdefault(sid,[]).append(rec));return rec

    def image_submit(self,pid,iid):
        store.update(pid,lambda d:d['images'][iid].update(state='running'));return {'ok':True}

    def video_submit(self,pid,sid,rev,sha):
        store.update(pid,lambda d:next(r for r in d['runs'][sid] if r['rev']==rev).update(state='running'));return {'ok':True}

    def finish_video(self,pid,sid='d01',rev='r1'):
        directory=store.project_dir(pid);file=f'clips/{sid}-{rev}.mp4';(directory/file).write_bytes(b'local video')
        store.update(pid,lambda d:next(r for r in d['runs'][sid] if r['rev']==rev).update(state='completed',result=file))
        dashboard.task_finished(pid,f'gen:{sid}:{rev}')

    def test_multiple_batches_deletion_orphan_and_get_are_read_only(self):
        first=self.child(1);second=self.child(2,'b2')
        before={p:p.read_bytes() for p in self.tmp.rglob('*.json')}
        with patch.object(store,'update',side_effect=AssertionError('GET writes')),patch.object(jobs,'start',side_effect=AssertionError('GET jobs')):
            view=dashboard.view(self.master)
        self.assertEqual([b['id'] for b in view['batches']],['b2','b1']);self.assertEqual(view['total'],2)
        self.assertEqual(before,{p:p.read_bytes() for p in self.tmp.rglob('*.json')})
        shutil.rmtree(store.project_dir(first));shutil.rmtree(self.d)
        view=dashboard.view(self.master)
        self.assertEqual(view['total'],1);self.assertFalse(view['master']['exists'])
        self.assertEqual(view['batches'][0]['items'][0]['project_id'],second)

    def test_progress_tracks_current_board_and_manual_changes(self):
        pid=self.child();self.boards(pid);self.prompts(pid)
        row=dashboard.view(self.master)['batches'][0]['items'][0]
        self.assertEqual(row['progress']['boards']['done'],2);self.assertEqual(row['progress']['prompts']['done'],2)
        self.assertEqual(len(row['boards']),8)
        store.update(pid,lambda d:d['segment_boards']['segments']['d01'].update(current=None))
        row=dashboard.view(self.master)['batches'][0]['items'][0]
        self.assertEqual(row['progress']['boards']['done'],1);self.assertEqual(len(row['boards']),5)
        self.assertFalse(dashboard.view(self.master)['can_prepare_videos'])

    def test_image_pending_latest_whole_segment_only_and_cancel_does_not_write(self):
        pid=self.child();old=self.board_preview(pid);latest=edits.preview(pid,'d01',{})
        fake=copy.deepcopy(latest);fake['id']='single';fake['storyboard_context']['cell_id']='c01'
        store.update(pid,lambda d:d['images'].update(single=fake))
        view=dashboard.view(self.master);pending=view['pending']['images']
        self.assertEqual(pending['image_count'],1);self.assertEqual(pending['items'][0]['image_id'],latest['id'])
        before=(store.project_dir(pid)/'project.json').read_bytes()
        dashboard.view(self.master)
        self.assertEqual(before,(store.project_dir(pid)/'project.json').read_bytes())
        self.assertEqual(store.load(pid)['images'][old['id']]['state'],'pending_confirmation')

    def test_stale_and_tampered_image_preview_not_summarized(self):
        pid=self.child();rec=self.board_preview(pid)
        Path(rec['storyboard_context']['references'][0]['path']).write_bytes(b'changed frozen reference')
        self.assertEqual(dashboard.view(self.master)['pending']['images']['image_count'],0)

    def test_image_token_changes_atomically_reject_before_any_submit(self):
        for mutation in ('new','edit','delete','file'):
            with self.subTest(mutation=mutation):
                pid=self.child(len(self.children)+1);rec=self.board_preview(pid)
                token=dashboard.view(self.master)['pending']['images']['token']
                if mutation=='new':self.board_preview(pid,'d02')
                elif mutation=='edit':store.update(pid,lambda d:d['segment_boards']['segments']['d01']['plan'].update(sig='changed'))
                elif mutation=='delete':shutil.rmtree(store.project_dir(pid))
                elif mutation=='file':Path(rec['storyboard_context']['references'][0]['path']).write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError,'刚才有内容改过，清单已更新，请再确认一次'):
                    dashboard.confirm(self.master,'images',{'token':token})
                self.paid_image.assert_not_called()

    def test_image_confirmation_bounded_live_tasks_and_duplicate_token(self):
        for i in range(1,5):self.board_preview(self.child(i))
        token=dashboard.view(self.master)['pending']['images']['token']
        dashboard.confirm(self.master,'images',{'token':token})
        self.assertEqual(self.paid_image.call_count,2)
        with self.assertRaises(ValueError):dashboard.confirm(self.master,'images',{'token':token})
        pid,iid=self.paid_image.call_args_list[0].args
        store.update(pid,lambda d:d['images'][iid].update(state='completed'))
        dashboard.task_finished(pid,'img:'+iid)
        self.assertEqual(self.paid_image.call_count,3)

    def test_prepare_images_only_missing_or_failed_preparation_and_no_payment(self):
        one=self.child();self.board_preview(one)
        two=self.child(2);store.update(two,lambda d:d['fission_preparation']['segments']['d01'].update(status='failed',error='推理失败'))
        with patch.object(fission,'prepare_segment') as prepare:
            dashboard.prepare(self.master,'images');self.execute()
        self.assertEqual(set(prepare.call_args_list[i].args for i in range(prepare.call_count)),{(one,'d02'),(two,'d01'),(two,'d02')})
        self.paid_image.assert_not_called()

    def test_video_preparation_only_ready_projects_rewrites_missing_prompts(self):
        ready=self.child();self.boards(ready);unready=self.child(2)
        with patch.object(director,'save',side_effect=lambda pid,body:self.prompts(pid)) as write,patch.object(gateway,'video_preview',side_effect=self.video_preview) as preview:
            dashboard.prepare(self.master,'videos');self.execute()
        self.assertEqual(write.call_count,1);self.assertEqual({c.args[0] for c in preview.call_args_list},{ready})
        self.assertEqual(preview.call_count,2);self.paid_video.assert_not_called()
        self.assertEqual(store.load(unready)['runs'],{})

    def test_billing_uses_records_and_reference_values_not_estimation(self):
        pid=self.child();self.boards(pid);self.prompts(pid)
        self.video_preview(pid,billing=9.25,output=5,reference=4.25);self.video_preview(pid,'d02',billing=7,output=6,reference=1)
        pending=dashboard.view(self.master)['pending']['videos']
        self.assertEqual((pending['billing_seconds'],pending['output_seconds'],pending['reference_seconds']),(16.25,11,5.25))
        self.assertEqual(pending['project_count'],1)

    def test_legacy_video_reference_seconds_read_exact_billing_note(self):
        pid=self.child();self.boards(pid);self.prompts(pid);self.video_preview(pid,reference=2.75)
        store.update(pid,lambda d:[d['runs']['d01'][0].pop(k) for k in ('output_seconds','reference_seconds')])
        self.assertEqual(dashboard.view(self.master)['pending']['videos']['reference_seconds'],2.75)

    def test_video_token_prompt_and_package_change_rejects_all(self):
        pid=self.child();self.boards(pid);self.prompts(pid);rec=self.video_preview(pid)
        token=dashboard.view(self.master)['pending']['videos']['token']
        store.update(pid,lambda d:d['director']['segments'][0].update(prompt='图1 改过提示词'))
        with self.assertRaisesRegex(ValueError,'刚才有内容改过'):dashboard.confirm(self.master,'videos',{'token':token})
        self.paid_video.assert_not_called()
        self.prompts(pid);token=dashboard.view(self.master)['pending']['videos']['token']
        (store.project_dir(pid)/rec['package']/'review/jimmy-job.preview.json').write_text('{}')
        with self.assertRaises(ValueError):dashboard.confirm(self.master,'videos',{'token':token})
        self.paid_video.assert_not_called()

    def test_video_limit_holds_until_completion_including_query_pending(self):
        for i in range(1,6):
            pid=self.child(i);self.boards(pid);self.prompts(pid);self.video_preview(pid)
        token=dashboard.view(self.master)['pending']['videos']['token'];dashboard.confirm(self.master,'videos',{'token':token})
        self.assertEqual(self.paid_video.call_count,3)
        pid,sid,rev,_=self.paid_video.call_args_list[0].args
        store.update(pid,lambda d:d['runs'][sid][0].update(state='query_pending'))
        dashboard.task_finished(pid,f'gen:{sid}:{rev}');self.assertEqual(self.paid_video.call_count,3)
        self.finish_video(pid);self.assertEqual(self.paid_video.call_count,4)

    def test_errors_preserve_reason_and_query_retry_never_claims_unpaid(self):
        pid=self.child();rec=self.board_preview(pid)
        store.update(pid,lambda d:d['images'][rec['id']].update(state='query_pending',error='查询超时',remote_task_id='task'))
        row=dashboard.view(self.master)['batches'][0]['items'][0]
        error=next(e for e in row['errors'] if e['stage']=='boards')
        self.assertEqual(error['retry']['kind'],'image-query');self.assertIn('查询超时',error['error'])
        self.assertNotIn('没有扣费',json.dumps(error,ensure_ascii=False))
        self.assertEqual(row['progress']['boards']['state'],'failed')

    def test_completion_selects_one_version_assembles_once_and_preserves_manual(self):
        pid=self.child();self.boards(pid);self.prompts(pid)
        self.video_preview(pid);self.video_preview(pid,'d02')
        with patch.object(final_preview,'generate') as assemble:
            self.finish_video(pid);self.assertEqual(director.get_segment(store.load(pid),'d01')['selected_run'],'r1')
            self.assertEqual(len(self.callbacks),0)
            self.finish_video(pid,'d02');self.execute();assemble.assert_called_once_with(pid)
        store.update(pid,lambda d:d['director']['segments'][0].update(selected_run='manual'))
        dashboard.task_finished(pid,'gen:d01:r1')
        self.assertEqual(director.get_segment(store.load(pid),'d01')['selected_run'],'manual')

    def test_two_successful_versions_not_auto_selected(self):
        pid=self.child();self.boards(pid);self.prompts(pid);self.video_preview(pid)
        directory=store.project_dir(pid);(directory/'clips/manual.mp4').write_bytes(b'video')
        store.update(pid,lambda d:d['runs']['d01'].append({'rev':'r2','state':'completed','result':'clips/manual.mp4'}))
        self.finish_video(pid)
        self.assertFalse(director.get_segment(store.load(pid),'d01').get('selected_run'))

    def test_import_reload_and_reads_do_not_assemble_or_resume(self):
        pid=self.child();self.boards(pid);self.prompts(pid);self.video_preview(pid)
        with patch.object(final_preview,'generate',side_effect=AssertionError('no assembly')),patch.object(jobs,'start',side_effect=AssertionError('no task')):
            importlib.reload(dashboard);dashboard.view(self.master)
        self.paid_video.assert_not_called();self.paid_image.assert_not_called()

    def test_failed_submission_does_not_discard_other_items_or_claim_no_charge(self):
        for i in (1,2):self.board_preview(self.child(i))
        token=dashboard.view(self.master)['pending']['images']['token']
        self.paid_image.side_effect=[RuntimeError('提交结果未知'),{'ok':True}]
        dashboard.confirm(self.master,'images',{'token':token})
        self.assertEqual(self.paid_image.call_count,2)
        view=dashboard.view(self.master)
        self.assertTrue(any(r['errors'] for b in view['batches'] for r in b['items']))
        self.assertNotIn('没有扣费',json.dumps(view,ensure_ascii=False))

    def test_existing_live_videos_take_slots_before_new_batch(self):
        old=self.child();self.boards(old);self.prompts(old)
        self.video_preview(old);self.video_preview(old,'d02')
        store.update(old,lambda d:[r.update(state='running') for runs in d['runs'].values() for r in runs])
        for i in (2,3):
            pid=self.child(i);self.boards(pid);self.prompts(pid);self.video_preview(pid)
        dashboard.confirm(self.master,'videos',{'token':dashboard.view(self.master)['pending']['videos']['token']})
        self.assertEqual(self.paid_video.call_count,1)

    def test_queued_change_is_not_paid_after_slots_open(self):
        for i in (1,2,3):self.board_preview(self.child(i))
        dashboard.confirm(self.master,'images',{'token':dashboard.view(self.master)['pending']['images']['token']})
        queued='child3'
        store.update(queued,lambda d:d['segment_boards']['segments']['d01']['plan'].update(sig='changed after confirmation'))
        pid,iid=self.paid_image.call_args_list[0].args
        store.update(pid,lambda d:d['images'][iid].update(state='completed'))
        dashboard.task_finished(pid,'img:'+iid)
        self.assertEqual(self.paid_image.call_count,2)
        self.assertIn('刚才有内容改过',json.dumps(dashboard.view(self.master),ensure_ascii=False))

    def test_same_token_concurrent_confirmation_claimed_only_once(self):
        pid=self.child();self.board_preview(pid)
        token=dashboard.view(self.master)['pending']['images']['token'];barrier=threading.Barrier(2);results=[]
        def confirm():
            barrier.wait()
            try:dashboard.confirm(self.master,'images',{'token':token});results.append('accepted')
            except ValueError:results.append('rejected')
        threads=[threading.Thread(target=confirm) for _ in range(2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(5);self.assertFalse(thread.is_alive())
        self.assertEqual(sorted(results),['accepted','rejected']);self.assertEqual(self.paid_image.call_count,1)

    def test_older_whole_preview_does_not_reappear_after_newer_generation(self):
        pid=self.child();self.board_preview(pid);rec=edits.preview(pid,'d01',{})
        output=store.project_dir(pid)/'synthetic.png';Image.new('RGB',(360,640),'navy').save(output)
        storyboard_images.complete(pid,rec,output)
        store.update(pid,lambda d:d['images'][rec['id']].update(state='completed'))
        self.assertEqual(dashboard.view(self.master)['pending']['images']['image_count'],0)

    def test_prepare_preserves_existing_manual_plan_and_request(self):
        pid=self.child();rec=self.board_preview(pid)
        store.update(pid,lambda d:d['images'].clear())
        before=copy.deepcopy(store.load(pid)['segment_boards']['segments']['d01']['plan'])
        with patch.object(edits,'generate_plan') as infer,patch.object(edits,'preview',return_value=rec) as preview:
            dashboard.prepare(self.master,'images');self.execute()
        self.assertEqual(store.load(pid)['segment_boards']['segments']['d01']['plan'],before)
        self.assertNotIn((pid,'d01',{}),[call.args for call in infer.call_args_list])
        self.assertIn((pid,'d01',{}),[call.args for call in preview.call_args_list])

    def test_idle_prompt_does_not_claim_running_work(self):
        pid=self.child();row=dashboard.view(self.master)['batches'][0]['items'][0]
        self.assertEqual(row['progress']['prompts']['state'],'idle')
        self.assertEqual(row['progress']['prompts']['detail'],'')

    def test_explicit_last_selection_triggers_local_assembly_event(self):
        pid=self.child();self.boards(pid);self.prompts(pid);self.video_preview(pid);self.video_preview(pid,'d02')
        directory=store.project_dir(pid)
        for sid in ('d01','d02'):
            file='clips/'+sid+'.mp4';(directory/file).write_bytes(b'video')
            store.update(pid,lambda d,sid=sid,file=file:d['runs'][sid][0].update(state='completed',result=file))
        with patch.object(final_preview,'generate') as assemble:
            director.save(pid,{'action':'select-run','segment_id':'d01','rev':'r1'})
            director.save(pid,{'action':'select-run','segment_id':'d02','rev':'r1'})
            self.execute();assemble.assert_called_once_with(pid)

    def test_restart_requires_new_confirmation_token_without_resuming_queue(self):
        pid=self.child();self.board_preview(pid)
        token=dashboard.view(self.master)['pending']['images']['token']
        with patch.object(dashboard,'_pump'):
            dashboard.confirm(self.master,'images',{'token':token})
        dashboard._queues.clear();dashboard._admitted.clear()
        self.assertNotEqual(dashboard.view(self.master)['pending']['images']['token'],token)
        with self.assertRaisesRegex(ValueError,'刚才有内容改过'):
            dashboard.confirm(self.master,'images',{'token':token})
        self.paid_image.assert_not_called()

    def test_claim_write_failure_cannot_leave_a_partially_runnable_queue(self):
        for i in (1,2):self.board_preview(self.child(i))
        token=dashboard.view(self.master)['pending']['images']['token'];update=store.update
        def fail_second(pid,fn):
            if pid=='child2':raise OSError('磁盘写入失败')
            return update(pid,fn)
        with patch.object(store,'update',side_effect=fail_second):
            with self.assertRaises(OSError):dashboard.confirm(self.master,'images',{'token':token})
        dashboard.task_finished('child1','img:unrelated')
        self.paid_image.assert_not_called()

    def test_third_party_image_preview_file_hash_is_validated(self):
        pid=self.child();rec=self.board_preview(pid)
        store.update(pid,lambda d:d['images'][rec['id']].update(channel='third_party'))
        path=store.project_dir(pid)/rec['run_dir']/'image-job.preview.json';path.write_text('{}')
        self.assertEqual(dashboard.view(self.master)['pending']['images']['image_count'],0)

    def test_completion_callback_finishes_before_job_is_marked_idle(self):
        states=[]
        class ImmediateThread:
            def __init__(self,*,target,**kwargs):self.target=target
            def start(self):self.target()
        def callback(pid,key):states.append(REAL_JOB_SNAPSHOT(pid)[key]['state'])
        with patch.object(jobs,'_jobs',{}),patch.object(jobs.threading,'Thread',ImmediateThread),patch.object(dashboard,'task_finished',side_effect=callback):
            REAL_JOB_START(self.master,'gen:d01:r1',lambda:None,'mock task')
        self.assertEqual(states,['running'])

    def test_new_selection_during_final_worker_handoff_still_assembles_new_cut(self):
        pid=self.child();self.boards(pid);self.prompts(pid);self.video_preview(pid);self.video_preview(pid,'d02')
        self.finish_video(pid);self.finish_video(pid,'d02')
        first_pid,first_key,first_work=self.callbacks.pop(0)
        # Old assembly released its project lock; runner has not published done.
        chosen=[{'id':'d01','rev':'r1'},{'id':'d02','rev':'r1'}]
        directory=store.project_dir(pid);(directory/'final/old.mp4').write_bytes(b'old cut')
        (directory/'clips/new.mp4').write_bytes(b'new selected clip')
        store.update(pid,lambda d:d.update(final_preview={'file':'final/old.mp4','segments':chosen}))
        store.update(pid,lambda d:d['runs']['d01'].append({'rev':'r2','state':'completed','result':'clips/new.mp4'}))
        director.save(pid,{'action':'select-run','segment_id':'d01','rev':'r2'})
        self.running[first_pid][first_key]['state']='done'
        dashboard.task_finished(first_pid,first_key)
        with patch.object(final_preview,'generate') as assemble:
            self.execute();assemble.assert_called_once_with(pid)

    def test_video_progress_rejects_selected_clip_bound_to_another_range(self):
        pid=self.child();self.boards(pid);self.prompts(pid);self.video_preview(pid)
        self.finish_video(pid)
        store.update(pid,lambda d:d['runs']['d01'][0].update(source_start=999))
        row=dashboard.view(self.master)['batches'][0]['items'][0]
        self.assertEqual(row['progress']['videos']['done'],0)

    def test_only_current_preparation_segment_claims_running_action(self):
        pid=self.child()
        self.running[pid]={'fission-prepare':{'state':'running'}}
        store.update(pid,lambda d:d['fission_preparation']['segments'].update(d01={'status':'pending_confirmation'},d02={'status':'preparing','phase':'preview'}))
        row=dashboard.view(self.master)['batches'][0]['items'][0]
        self.assertEqual(row['progress']['boards']['detail'],'第 2 段正在准备预览')

    def test_gateway_invalid_preview_is_omitted_without_breaking_dashboard(self):
        pid=self.child();rec=self.board_preview(pid)
        path=store.project_dir(pid)/rec['run_dir']/'image-job.preview.json'
        preview=json.loads(path.read_text());preview['images'][0]['sha256']='outdated hash'
        store.atomic_write_json(path,preview)
        store.update(pid,lambda d:d['images'][rec['id']].update(preview_sha256=store.sha256_file(path)))
        self.assertEqual(dashboard.view(self.master)['pending']['images']['image_count'],0)
        self.boards(pid);self.prompts(pid);self.video_preview(pid)
        store.update(pid,lambda d:d['runs']['d01'][0].update(director_format_version=2))
        self.assertEqual(dashboard.view(self.master)['pending']['videos']['project_count'],0)

    def test_manual_successful_final_hides_previous_assembly_failure(self):
        pid=self.child();self.boards(pid);self.prompts(pid);self.video_preview(pid);self.video_preview(pid,'d02')
        self.finish_video(pid);self.finish_video(pid,'d02')
        directory=store.project_dir(pid);(directory/'final/done.mp4').write_bytes(b'manually assembled video')
        store.update(pid,lambda d:d.update(fission_final={'error':'previous local assembly failure'},
            final_preview={'file':'final/done.mp4','segments':[{'id':'d01','rev':'r1'},{'id':'d02','rev':'r1'}]}))
        row=dashboard.view(self.master)['batches'][0]['items'][0]
        self.assertEqual(row['progress']['final']['state'],'completed')
        self.assertFalse([e for e in row['errors'] if e['stage']=='final'])

    def test_deleted_unresolved_video_keeps_its_remote_slot(self):
        for i in range(1,5):
            pid=self.child(i);self.boards(pid);self.prompts(pid);self.video_preview(pid)
        dashboard.confirm(self.master,'videos',{'token':dashboard.view(self.master)['pending']['videos']['token']})
        self.assertEqual(self.paid_video.call_count,3)
        pid,sid,rev,_=self.paid_video.call_args_list[0].args
        store.update(pid,lambda d:d['runs'][sid][0].update(state='query_pending'))
        shutil.rmtree(store.project_dir(pid))
        dashboard.task_finished('child2','gen:unrelated:r1')
        self.assertEqual(self.paid_video.call_count,3)
        self.assertEqual(dashboard.view(self.master)['total'],3)

    def test_delete_derived_project_rejects_unresolved_remote_image_or_video(self):
        import server
        handler=object.__new__(server.Handler)
        handler.headers={'X-Client-Build':server.build_status()['build']}
        with patch.object(server,'_remove_trash'):
            for kind,state in (('video','query_pending'),('video','unknown'),('image','query_pending'),('image','unknown')):
                with self.subTest(kind=kind,state=state):
                    pid=self.child(len(self.children)+1)
                    store.update(pid,lambda d:d.update(runs={'d01':[{'rev':'r1','state':state}]} if kind=='video' else {},
                        images={'i1':{'state':state}} if kind=='image' else {}))
                    with self.assertRaisesRegex(server.ApiError,'查询|恢复'):
                        handler._api('DELETE',['projects',pid],{})
                    self.assertTrue((store.project_dir(pid)/'project.json').exists())

    def test_removed_run_record_keeps_unresolved_remote_slot(self):
        for i in range(1,5):
            pid=self.child(i);self.boards(pid);self.prompts(pid);self.video_preview(pid)
        dashboard.confirm(self.master,'videos',{'token':dashboard.view(self.master)['pending']['videos']['token']})
        pid,sid,rev,_=self.paid_video.call_args_list[0].args
        store.update(pid,lambda d:d.update(runs={}))
        dashboard.task_finished('child2','gen:unrelated:r1')
        self.assertEqual(self.paid_video.call_count,3)

    def test_recreated_revision_cannot_release_previous_claim_slot(self):
        for i in range(1,5):
            pid=self.child(i);self.boards(pid);self.prompts(pid);self.video_preview(pid)
        dashboard.confirm(self.master,'videos',{'token':dashboard.view(self.master)['pending']['videos']['token']})
        pid,sid,rev,_=self.paid_video.call_args_list[0].args
        store.update(pid,lambda d:d['runs'][sid][0].update(state='completed',batch_confirmation='different claim'))
        dashboard.task_finished('child2','gen:unrelated:r1')
        self.assertEqual(self.paid_video.call_count,3)

    def test_legacy_interruption_with_remote_retry_keeps_image_slot_and_query_action(self):
        from console import pipeline
        for i in (1,2,3):self.board_preview(self.child(i))
        dashboard.confirm(self.master,'images',{'token':dashboard.view(self.master)['pending']['images']['token']})
        pid,iid=self.paid_image.call_args_list[0].args
        store.update(pid,lambda d:d['images'][iid].update(channel='third_party',remote_task_id='remote-existing',query_retry={'exhausted':False}))
        pipeline.recover_stale(pid,{'img:'+iid:{'state':'done'}})
        self.assertEqual(store.load(pid)['images'][iid]['state'],'failed')
        dashboard.task_finished('child2','img:unrelated')
        self.assertEqual(self.paid_image.call_count,2)
        row=next(r for b in dashboard.view(self.master)['batches'] for r in b['items'] if r['project_id']==pid)
        self.assertEqual(next(e for e in row['errors'] if e['stage']=='boards')['retry']['kind'],'image-query')
        store.update(pid,lambda d:d['images'][iid].pop('query_retry'))
        dashboard.task_finished(pid,'img:'+iid)
        self.assertEqual(self.paid_image.call_count,3)

    def test_terminal_state_with_retry_evidence_releases_only_after_retry_clears(self):
        for i in (1,2,3):self.board_preview(self.child(i))
        dashboard.confirm(self.master,'images',{'token':dashboard.view(self.master)['pending']['images']['token']})
        pid,iid=self.paid_image.call_args_list[0].args
        store.update(pid,lambda d:d['images'][iid].update(state='completed',query_retry={'exhausted':True}))
        dashboard.task_finished(pid,'img:'+iid);self.assertEqual(self.paid_image.call_count,2)
        store.update(pid,lambda d:d['images'][iid].pop('query_retry'))
        dashboard.task_finished(pid,'img:'+iid);self.assertEqual(self.paid_image.call_count,3)

    def test_submit_exception_after_record_disappears_keeps_unknown_slot(self):
        for i in (1,2,3):self.board_preview(self.child(i))
        def uncertain(pid,iid):
            if pid=='child1':
                store.update(pid,lambda d:d['images'].clear())
                raise RuntimeError('提交后响应丢失')
            return self.image_submit(pid,iid)
        self.paid_image.side_effect=uncertain
        dashboard.confirm(self.master,'images',{'token':dashboard.view(self.master)['pending']['images']['token']})
        self.assertEqual(self.paid_image.call_count,2)
        dashboard.task_finished('child2','img:unrelated');self.assertEqual(self.paid_image.call_count,2)
        self.assertIn('提交后响应丢失',json.dumps(dashboard.view(self.master),ensure_ascii=False))

    def test_clearing_derived_source_preserves_unresolved_task_evidence(self):
        import server
        pid=self.child();handler=object.__new__(server.Handler)
        handler.headers={'X-Client-Build':server.build_status()['build']}
        store.update(pid,lambda d:d.update(runs={'d01':[{'rev':'r1','state':'query_pending'}]}))
        with patch.object(server.pipeline,'clear_source',return_value={'ok':True}) as clear:
            with self.assertRaisesRegex(server.ApiError,'查询|恢复'):
                handler._api('DELETE',['projects',pid,'source'],{})
            clear.assert_not_called()
            # Existing non-derived deletion semantics remain unchanged.
            store.update(self.master,lambda d:d.update(runs={'d01':[{'rev':'r1','state':'query_pending'}]}))
            handler._api('DELETE',['projects',self.master,'source'],{})
            clear.assert_called_once_with(self.master)

    def test_parent_confirmation_and_child_as_master_creation_share_lock_order(self):
        pid=self.child();self.board_preview(pid)
        token=dashboard.view(self.master)['pending']['images']['token']
        project_lock=store._lock;batch_lock=fission._batch_lock
        child_held=threading.Event();confirm_waiting_for_child=threading.Event();errors=[]
        class TimedLock:
            def __init__(self,lock,label,entered=None,timeout=2):
                self.lock=lock;self.label=label;self.entered=entered;self.timeout=timeout
            def __enter__(self):
                if self.entered:self.entered.set()
                if not self.lock.acquire(timeout=self.timeout):raise TimeoutError(self.label)
                return self
            def __exit__(self,*args):self.lock.release()
        def locks(project):
            if project==pid and threading.current_thread().name=='confirm-parent':
                return TimedLock(project_lock(project),'parent waiting for child',confirm_waiting_for_child)
            return project_lock(project)
        def create_from_child():
            try:
                with project_lock(pid):
                    child_held.set()
                    if not confirm_waiting_for_child.wait(3):raise TimeoutError('confirm did not request child lock')
                    # fission.create holds its master project lock before saving
                    # the batch; this child is itself a valid new master.
                    fission._save_batch({'id':'grandchild-batch','master_id':pid,'created_at':'2026-11-08','items':[]})
            except Exception as exc:errors.append(str(exc))
        def confirm_parent():
            try:
                if not child_held.wait(3):raise TimeoutError('child lock was not held')
                dashboard.confirm(self.master,'images',{'token':token})
            except Exception as exc:errors.append(str(exc))
        with patch.object(store,'_lock',side_effect=locks),patch.object(fission,'_batch_lock',TimedLock(batch_lock,'child waiting for batch',timeout=.5)):
            creator=threading.Thread(target=create_from_child,name='create-from-child',daemon=True)
            confirmer=threading.Thread(target=confirm_parent,name='confirm-parent',daemon=True)
            creator.start();confirmer.start()
            creator.join(5);confirmer.join(5)
            self.assertFalse(creator.is_alive());self.assertFalse(confirmer.is_alive())
        self.assertEqual(errors,[],'Project locks must be acquired before the batch lock')
        self.paid_image.assert_called_once()
        self.assertTrue((config.BATCHES_DIR/'grandchild-batch.json').exists())

    def test_api_contract_allows_orphan_dashboard(self):
        import server
        pid=self.child();shutil.rmtree(self.d)
        handler=object.__new__(server.Handler);handler.headers={"X-Client-Build":server.build_status()["build"]}
        self.assertEqual(handler._api('GET',['fission-boards',self.master],{})['total'],1)
        with patch.object(dashboard,'prepare',return_value={'ok':True}) as prepare:
            self.assertEqual(handler._api('POST',['fission-boards',self.master,'images','prepare'],{}),{'ok':True})
            prepare.assert_called_once_with(self.master,'images')
        handler._body_json=lambda:{'token':'frozen'}
        with patch.object(dashboard,'confirm',return_value={'ok':True}) as confirm:
            handler._api('POST',['fission-boards',self.master,'videos','confirm'],{})
            confirm.assert_called_once_with(self.master,'videos',{'token':'frozen'})


if __name__=='__main__':unittest.main()
