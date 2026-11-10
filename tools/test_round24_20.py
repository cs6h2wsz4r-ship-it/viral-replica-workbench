"""24.20: synthetic projects; CLI, timers and all network boundaries mocked."""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import config, jimmy_bridge as bridge, jobs, media, motion, remote_query, replacement_guard as guard, store


class Round24_20Tests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT/'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.folder))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('禁止密钥读取')))
        self.enterContext(patch.object(config, 'jimmy_env_file', return_value=self.folder/'mock.env'))
        self.enterContext(patch('subprocess.run', side_effect=AssertionError('禁止真实子进程')))
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('禁止真实请求')))
        self.clock = self.enterContext(patch.object(bridge.time, 'time', return_value=10000))
        self.timers = []
        class Timer:
            def __init__(inner, delay, fn):
                inner.delay, inner.fn, inner.cancelled = delay, fn, False
                self.timers.append(inner)
            def start(inner): pass
            def cancel(inner): inner.cancelled = True
        self.enterContext(patch('threading.Timer', Timer))
        self.queue, self.active = [], {}
        def start(pid, key, fn, label):
            if self.active.get(key, {}).get('state') == 'running': raise RuntimeError('duplicate')
            self.active[key] = {'state': 'running'}
            self.queue.append((key, fn))
            return self.active[key]
        self.enterContext(patch.object(jobs, 'start', side_effect=start))
        self.enterContext(patch.object(jobs, 'snapshot', side_effect=lambda pid: copy.deepcopy(self.active)))
        self.cli = self.enterContext(patch.object(bridge, '_call', return_value='{}'))
        self.pid = store.create('24.20 自造任务')['id']
        self.directory = store.project_dir(self.pid)
        self.run_dir = self.directory/'jimmy-runs/run'
        self.run_dir.mkdir()
        store.update(self.pid, lambda d:d['runs'].update({'seg-01':[{'rev':'r1','state':'failed','run_dir':'jimmy-runs/run','submitted_at':9990}]}))
        self.write()
        self.addCleanup(lambda: [remote_query.cancel(key) for key in list(remote_query._timers)
                                if key[0] == str(self.directory.resolve())])

    def write(self, message='Jimmy request failed with HTTP 521', **kw):
        child = {'phase':'failed','status':'failed','jimmy_task_id':'same-remote-id',
                 'error':{'message':message,'type':'SeedanceApiError'}, **kw}
        store.atomic_write_json(self.run_dir/'state.json', {'tasks':{'seg-01':child}})

    def record(self): return store.load(self.pid)['runs']['seg-01'][0]
    def refresh(self): return bridge.refresh(self.pid,'seg-01','r1')
    def fire(self):
        timer = self.timers[-1]
        self.clock.return_value += timer.delay
        timer.fn()
    def drain(self):
        while self.queue:
            key, fn = self.queue.pop(0)
            try: fn()
            finally: self.active[key]['state'] = 'done'

    def test_transient_poll_and_download_errors_are_running(self):
        for message in ['HTTP 521','HTTP 502','HTTP 429','GET transport failed','request timed out',
                        'invalid UTF-8/JSON','download failed','下载失败']:
            with self.subTest(message=message):
                self.write(message);self.refresh()
                self.assertEqual(self.record()['state'],'running')
                self.assertEqual(self.record()['query_retry']['attempts'],0)
                self.assertIsNone(self.record()['error'])
        self.assertEqual(len(self.timers),1)
        self.assertEqual(self.timers[0].delay,30)

    def test_remote_terminal_error_is_failed_and_never_retried(self):
        self.write(error={'message':'HTTP 502 remote rendering failed'},status='failed')
        self.refresh();self.assertEqual(self.record()['state'],'failed');self.assertEqual(self.timers,[])
        self.write(error={'message':'HTTP 502','type':'SeedanceApiError'},status='cancelled')
        self.refresh();self.assertEqual(self.record()['state'],'failed');self.assertEqual(self.timers,[])

    def test_backoff_same_remote_id_no_submit_and_concurrent_dedup(self):
        with ThreadPoolExecutor(max_workers=4) as pool: list(pool.map(lambda _:self.refresh(),range(8)))
        self.assertEqual(len(self.timers),1)
        for expected in [30,60,120,240,300,300]:
            self.assertEqual(self.timers[-1].delay,expected)
            self.fire();self.drain()
        self.assertEqual(self.record()['query_retry']['attempts'],6)
        commands=[c.args[0] for c in self.cli.call_args_list]
        self.assertEqual([a[1] for a in commands],['recover-task-id','resume']*6)
        for args in commands[::2]:self.assertEqual(args[args.index('--remote-task-id')+1],'same-remote-id')

    def test_two_hours_pending_manual_once_and_restart_reconnect(self):
        store.update(self.pid, lambda d:d['runs']['seg-01'][0].update(submitted_at=2700))
        self.refresh();r=self.record()
        self.assertEqual(r['state'],'query_pending');self.assertTrue(r['query_retry']['exhausted'])
        self.assertEqual(self.timers,[])
        bridge.resume(self.pid,'seg-01','r1');self.drain()
        self.assertEqual([c.args[0][1] for c in self.cli.call_args_list],['recover-task-id','resume'])
        self.assertEqual(self.record()['state'],'query_pending')
        store.update(self.pid, lambda d:d['runs']['seg-01'][0].update(submitted_at=9990,query_retry={}))
        bridge.refresh_all(self.pid)
        self.assertEqual(self.record()['state'],'running');self.assertEqual(len(self.timers),1)

    def test_manual_failed_resume_recovers_same_id_and_deduplicates(self):
        self.write(error={'message':'remote failure'})
        bridge.resume(self.pid,'seg-01','r1');bridge.resume(self.pid,'seg-01','r1')
        self.assertEqual(len(self.queue),1);self.drain()
        self.assertEqual([c.args[0][1] for c in self.cli.call_args_list],['recover-task-id','resume'])

    def test_completed_cancels_scheduled_retry(self):
        self.refresh();timer=self.timers[-1]
        self.write(phase='completed',status='completed',error=None)
        self.refresh();self.assertEqual(self.record()['state'],'completed')
        timer.fn();self.assertEqual(self.queue,[])

    def test_restart_preserves_deadline_attempts_and_due_time(self):
        self.refresh();self.fire();self.drain()
        expected = copy.deepcopy(self.record()['query_retry'])
        remote_query.cancel(bridge._query_key(self.pid,'gen:seg-01:r1'))
        self.clock.return_value += 20
        bridge.refresh_all(self.pid)
        self.assertEqual(self.record()['query_retry'],expected)
        self.assertEqual(self.timers[-1].delay,40)

    def test_deadline_timer_stops_even_without_browser_refresh(self):
        store.update(self.pid,lambda d:d['runs']['seg-01'][0].update(submitted_at=2820))
        self.refresh();self.assertEqual(self.timers[-1].delay,20)
        self.fire();self.assertEqual(self.record()['state'],'query_pending')
        self.assertEqual(self.queue,[])

    def test_deleted_project_timer_does_not_recreate_project(self):
        self.refresh()
        (self.directory/'project.json').unlink()
        self.fire();self.assertFalse((self.directory/'project.json').exists());self.assertEqual(self.queue,[])

    def test_missing_remote_id_never_authorizes_query_recovery(self):
        self.write(jimmy_task_id=None)
        self.refresh();self.assertEqual(self.record()['state'],'failed');self.assertEqual(self.timers,[])

    def test_recover_process_transport_error_is_also_retried(self):
        self.refresh();self.write(phase='submitted',status='submitted',error=None)
        self.cli.side_effect=bridge.BridgeError('GET transport failed')
        self.fire();self.drain()
        self.assertEqual(self.record()['state'],'running')
        self.assertEqual(self.timers[-1].delay,60)

    def test_recovered_running_task_continues_until_completed(self):
        self.refresh()
        def call(args,cwd,timeout=600):
            if args[1]=='resume':self.write(phase='running',status='running',error=None)
            return '{}'
        self.cli.side_effect=call;self.fire();self.drain()
        self.assertEqual(self.record()['state'],'running')
        self.assertEqual(self.record()['query_retry']['attempts'],1)
        self.assertEqual(self.timers[-1].delay,60)

    def test_images_query_exception_stays_running_and_restart_resumes_only(self):
        image_dir=self.directory/'image-runs/img';image_dir.mkdir()
        store.atomic_write_json(image_dir/'image-state.json',{'phase':'submitted','remote_task_id':'same-image-id'})
        store.update(self.pid,lambda d:d['images'].update({'img':{'id':'img','state':'failed','run_dir':'image-runs/img','started_at':9990,'error':'image query/download failed; resume this same task ID without creating another task'}}))
        # Restarted history stays untouched; polling only resumes jobs admitted
        # by an explicit action in this process.
        bridge.refresh_all(self.pid)
        self.assertEqual(store.load(self.pid)['images']['img']['state'],'failed')
        self.assertEqual(self.timers,[])
        self.active['img:img']={'state':'done'}
        bridge.refresh_all(self.pid)
        rec=store.load(self.pid)['images']['img'];self.assertEqual(rec['state'],'running')
        self.cli.side_effect=bridge.BridgeError('image query/download failed; resume this same task ID without creating another task')
        self.fire();self.drain()
        rec=store.load(self.pid)['images']['img'];self.assertEqual(rec['state'],'running')
        self.assertEqual([c.args[0][1] for c in self.cli.call_args_list],['resume'])
        self.assertEqual(self.timers[-1].delay,60)

    def motion_data(self):
        data=store.load(self.pid)
        task={'id':'seg-01','kind':'broll','mode':'video','image_ids':['photo'],'duration':4,
              'source_start':0,'source_end':4,'story':'A calm expression.'}
        photo=self.directory/'assets/photo.jpg';photo.write_bytes(b'synthetic')
        data.update(assets=[{'id':'photo','type':'image','label':'自造图','file':'assets/photo.jpg','sha256':store.sha256_file(photo)}],
                    breakdown={'shots':[{'start':0,'end':4,'action':'Door opens'}]},
                    script={'at':'12:00:00','provider':'mock','model':'mock','tasks':[task]})
        data['script']['replacements_fingerprint']=guard.fingerprint(data,self.directory)
        store.atomic_write_json(self.directory/'project.json',data)
        return data,task

    def test_motion_warning_does_not_block_and_dismissal_tracks_script_and_task(self):
        data,task=self.motion_data()
        error='原片时机不一致；原句：A calm expression.'
        def compare(data,task,rows,report):
            report.update(status='completed',model='mock',errors=[error]);return [error],[]
        with patch.object(motion,'compare_actions',side_effect=compare):
            check=bridge.replacement_check(data,task,self.directory,compare_motion=True)
        self.assertEqual(check['errors'],[])
        self.assertEqual(check['motion_warning']['details'],[error])
        store.update(self.pid,lambda d:d.update(action_comparisons={'seg-01':{'fingerprint':bridge.preflight_fingerprint(d,task,self.directory),'comparison':check['action_comparison']}}))
        before=bridge.preflight_fingerprint(data,task,self.directory)
        bridge.dismiss_motion_warning(self.pid,'seg-01',check['motion_warning']['fingerprint'])
        data=store.load(self.pid)
        self.assertTrue(bridge.replacement_check(data,task,self.directory)['motion_warning']['dismissed'])
        self.assertEqual(before,bridge.preflight_fingerprint(data,task,self.directory))
        changed={**task,'story':'A different expression.'}
        with patch.object(motion,'compare_actions',side_effect=compare):
            self.assertFalse(bridge.replacement_check(data,changed,self.directory,compare_motion=True)['motion_warning']['dismissed'])
        with self.assertRaises(bridge.BridgeError):bridge.dismiss_motion_warning(self.pid,'seg-01','outdated')
        blocked=bridge.replacement_check(data,{**task,'image_ids':['missing']},self.directory)
        self.assertTrue(blocked['errors'])

    def test_model_additions_allow_real_preview_and_confirmation_under_mocks(self):
        data,task=self.motion_data()
        source=self.directory/'source/source.mp4';source.write_bytes(b'synthetic-video')
        store.update(self.pid,lambda d:d.update(source={'file':'source/source.mp4','probe':{'duration':4}}))
        def compare(data,task,rows,report):
            report.update(status='completed',model='mock',errors=['视线时机不一致；原句：A calm expression.'])
            return report['errors'],[]
        def package(args,cwd,timeout=600):
            if args[1]=='prepare':
                raw=json.dumps({'tasks':[{'id':'seg-01','model':bridge.MINI_VIDEO}]}).encode()
                (cwd/'review').mkdir()
                (cwd/'review/jimmy-job.preview.json').write_bytes(raw)
                store.atomic_write_json(cwd/'review/jimmy-handoff.json',{
                    'state':'ready_for_confirmation','job_sha256':hashlib.sha256(raw).hexdigest()})
            return '{}'
        with patch.object(motion,'compare_actions',side_effect=compare) as model, \
                patch.object(media,'cut_clip',side_effect=lambda src,a,b,out,**kw:out.write_bytes(b'clip')), \
                patch.object(media,'probe',return_value={'duration':4}), \
                patch.object(config,'jimmy_configured',return_value=True):
            self.cli.side_effect=package
            preview=bridge.preview(self.pid,'seg-01')
            self.assertEqual(preview['state'],'pending_confirmation')
            self.assertTrue(preview['action_comparison']['errors'])
            check=bridge.replacement_check(store.load(self.pid),task,self.directory)
            bridge.dismiss_motion_warning(self.pid,'seg-01',check['motion_warning']['fingerprint'])
            bridge.confirm(self.pid,'seg-01',preview['rev'],preview['job_sha256'])
            self.assertEqual(model.call_count,1)
        self.assertEqual(store.load(self.pid)['runs']['seg-01'][-1]['state'],'running')
        self.assertEqual([c.args[0][1] for c in self.cli.call_args_list],['prepare','verify'])

    def test_legacy_deadline_uses_immutable_job_time(self):
        import os
        job=self.run_dir/'job.json';job.write_text('{}');os.utime(job,(2700,2700))
        store.update(self.pid,lambda d:d['runs']['seg-01'][0].pop('submitted_at'))
        self.refresh();self.assertEqual(self.record()['state'],'query_pending')

    def image_record(self, *, start=9990, phase='submitted', channel='third_party'):
        folder=self.directory/'image-runs/img';folder.mkdir(exist_ok=True)
        store.atomic_write_json(folder/'image-state.json',{'phase':phase,'remote_task_id':'same-image-id'})
        store.update(self.pid,lambda d:d['images'].update({'img':{'id':'img','state':'failed','run_dir':'image-runs/img','started_at':start,'channel':channel}}))
        return folder

    def test_image_deadline_manual_query_and_terminal_failure_clear_retry(self):
        folder=self.image_record(start=2700)
        bridge._image_query_failure(self.pid,'img',bridge.BridgeError('HTTP 521'))
        rec=store.load(self.pid)['images']['img']
        self.assertEqual(rec['state'],'query_pending');self.assertEqual(self.timers,[])
        def terminal(args,cwd,timeout=600):
            state={'phase':'failed','remote_task_id':'same-image-id'}
            store.atomic_write_json(folder/'image-state.json',state)
            return json.dumps(state)
        self.cli.side_effect=terminal
        bridge.image_resume(self.pid,'img');bridge.image_resume(self.pid,'img')
        self.assertEqual(len(self.queue),1)
        with self.assertRaises(bridge.BridgeError):self.drain()
        rec=store.load(self.pid)['images']['img']
        self.assertEqual(rec['state'],'failed');self.assertNotIn('query_retry',rec)
        self.assertEqual([c.args[0][1] for c in self.cli.call_args_list],['resume'])

    def test_image_done_from_same_task_clears_retry_without_submit(self):
        folder=self.image_record()
        bridge._image_query_failure(self.pid,'img',bridge.BridgeError('image query/download failed'))
        output=folder/'result.png';output.write_bytes(b'synthetic-image')
        state={'phase':'completed','remote_task_id':'same-image-id','output':str(output)}
        self.cli.side_effect=lambda *a,**kw:json.dumps(state)
        self.fire();self.drain()
        rec=store.load(self.pid)['images']['img']
        self.assertEqual(rec['state'],'completed');self.assertNotIn('query_retry',rec)
        self.assertEqual([c.args[0][1] for c in self.cli.call_args_list],['resume'])

    def test_official_image_and_real_remote_image_failure_are_not_auto_retried(self):
        self.image_record(phase='failed')
        self.assertFalse(bridge._image_query_failure(self.pid,'img',bridge.BridgeError('HTTP 502')))
        self.image_record(channel='official')
        self.assertFalse(bridge._image_query_failure(self.pid,'img',bridge.BridgeError('HTTP 502')))
        self.assertEqual(self.timers,[])

    def test_image_fifteen_minute_poll_timeout_continues_two_hour_policy(self):
        folder=self.image_record()
        with patch.object(bridge.time,'time',side_effect=[10000,10901]):
            with self.assertRaises(bridge.BridgeError) as caught:
                bridge._image_poll(self.pid,'img',folder,'mock.env',wait=True)
        self.assertTrue(bridge._image_query_failure(self.pid,'img',caught.exception))
        self.assertEqual(store.load(self.pid)['images']['img']['state'],'running')
        self.assertEqual(self.timers[-1].delay,30)

    def test_legacy_sanitized_image_error_recovers_from_technical_history(self):
        self.image_record()
        self.write(phase='completed',status='completed',error=None)
        store.update(self.pid,lambda d:bridge._record_error(d['images']['img'],
            bridge.BridgeError('image query/download failed; resume this same task ID without creating another task')))
        before=store.load(self.pid)['images']['img']
        self.assertNotIn('query/download',before['error'])
        self.assertTrue(any('query/download' in e for e in before['previous_errors']))
        bridge.refresh_all(self.pid)
        self.assertEqual(store.load(self.pid)['images']['img'],before)
        self.assertEqual(self.timers,[])
        self.active['img:img']={'state':'done'}
        bridge.refresh_all(self.pid)
        rec=store.load(self.pid)['images']['img']
        self.assertEqual(rec['state'],'running');self.assertEqual(rec['query_retry']['attempts'],0)
        self.assertEqual(len(self.timers),1)

    def test_manually_requery_terminal_image_uses_same_id_and_resume_only(self):
        folder=self.image_record(phase='failed')
        output=folder/'result.png'
        def requery(args,cwd,timeout=600):
            self.assertEqual(args[1],'resume')
            state=bridge._read_state(folder/'image-state.json')
            self.assertEqual(state['phase'],'submitted','terminal state must be recovered before the engine GET')
            self.assertEqual(state['remote_task_id'],'same-image-id')
            output.write_bytes(b'synthetic recovered image')
            return json.dumps({'phase':'completed','output':str(output)})
        self.cli.side_effect=requery
        bridge.image_resume(self.pid,'img');self.drain()
        self.assertEqual(store.load(self.pid)['images']['img']['state'],'completed')

    def test_manually_requeried_terminal_image_can_be_running_or_failed_again(self):
        folder=self.image_record(phase='failed')
        self.cli.return_value=json.dumps({'phase':'submitted','remote_task_id':'same-image-id'})
        bridge.image_resume(self.pid,'img');self.drain()
        rec=store.load(self.pid)['images']['img']
        self.assertEqual(rec['state'],'running');self.assertTrue(rec['query_retry'])
        self.assertEqual(len(self.timers),1)
        def failed(args,cwd,timeout=600):
            state={'phase':'failed','remote_task_id':'same-image-id'}
            store.atomic_write_json(folder/'image-state.json',state)
            return json.dumps(state)
        self.cli.side_effect=failed;self.fire()
        with self.assertRaises(bridge.BridgeError):self.drain()
        rec=store.load(self.pid)['images']['img']
        self.assertEqual(rec['state'],'failed');self.assertNotIn('query_retry',rec)

    def test_same_script_regenerated_even_in_same_second_renews_warning(self):
        from console import gateway,pipeline
        data,task=self.motion_data()
        store.update(self.pid,lambda d:d.update(source={'probe':{'duration':4,'ratio_hint':'9:16'}},
            motion_warning_dismissals={'seg-01':guard.motion_warning_fingerprint(d,task)}))
        answer={'segments':[{'kind':'broll','sourceStart':0,'sourceEnd':4,'story':'Calm expression'}]}
        with patch.object(config,'load',return_value=dict(config.DEFAULTS)), \
                patch.object(gateway,'reason',return_value=(answer,{'model':'mock'})), \
                patch.object(pipeline,'_attach_motion_evidence'),patch.object(pipeline,'apply_replacement_safety'), \
                patch.object(motion,'cleanup_performance'):
            pipeline.run_script(self.pid,'openai','keep','',None)
        current=store.load(self.pid)
        self.assertNotIn('motion_warning_dismissals',current)
        self.assertTrue(current['motion_warning_epoch'])


if __name__=='__main__':unittest.main()
