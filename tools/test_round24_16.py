"""24.16 deletion races: temporary projects only, no server/network/model I/O."""
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from console import config, gateway, jimmy_bridge, jobs, pipeline, runtime_env, store


class Round24_16Tests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory(dir=config.ROOT / 'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.folder / 'mock-projects'))
        self.enterContext(patch.object(config, 'ENV_FILE', self.folder / 'mock-settings.conf'))
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('禁止真实请求')))
        with patch.object(runtime_env, 'apply'):
            import server
        self.server = server
        self.enterContext(patch.object(server, 'build_status', return_value={
            'build': 'offline', 'server_build': 'offline', 'web_build': 'offline', 'restart_pending': False}))
        self.handler = object.__new__(server.Handler)
        self.handler.headers = {'X-Client-Build': 'offline'}
        self.handler._body_json = lambda: {}
        self.pid = store.create('临时删除测试')['id']
        self.directory = store.project_dir(self.pid)
        self.workers = []
        self.errors = []
        self.addCleanup(self.join_workers)

    def worker(self, fn):
        def run():
            try:
                fn()
            except Exception as exc:
                self.errors.append(exc)
        thread = threading.Thread(target=run, daemon=True)
        self.workers.append(thread)
        thread.start()
        return thread

    def join_workers(self):
        for thread in self.workers:
            thread.join(3)
            self.assertFalse(thread.is_alive(), '并发测试不能留下工作线程')
        self.assertEqual(self.errors, [])

    def delete(self):
        return self.handler._api('DELETE', ['projects', self.pid], {})

    def dispatch(self, method, suffix=''):
        self.handler.path = f'/api/projects/{self.pid}{suffix}'
        response = []
        self.handler._json = lambda body, status=200: response.append((status, body))
        self.handler._dispatch(method)
        return response[0]

    def test_delete_returns_before_cleanup_and_renames_out_of_list(self):
        started, release, removed = threading.Event(), threading.Event(), threading.Event()
        original = self.server.shutil.rmtree
        def slow_remove(path, **kwargs):
            self.assertEqual(Path(path).parent, config.PROJECTS_DIR / '.trash')
            self.assertTrue(kwargs.get('ignore_errors'))
            started.set()
            release.wait(3)
            original(path, **kwargs)
            removed.set()
        try:
            with patch.object(self.server.shutil, 'rmtree', side_effect=slow_remove):
                self.worker(self.delete)
                self.assertTrue(started.wait(2), '后台清理应启动')
                self.join_workers()  # DELETE must finish while rmtree is still blocked.
                self.assertFalse(removed.is_set())
                self.assertFalse(self.directory.exists())
                trashed = list((config.PROJECTS_DIR / '.trash').iterdir())
                self.assertEqual(len(trashed), 1)
                self.assertTrue(trashed[0].name.startswith(self.pid + '-'))
                self.assertTrue((trashed[0] / 'project.json').is_file())
                self.assertEqual(store.list_projects(), [])
        finally:
            release.set()
            self.assertTrue(removed.wait(3))

    def test_cleanup_failure_only_logs_and_does_not_fail_delete(self):
        finished = threading.Event()
        def fail(*args, **kwargs):
            raise OSError('offline cleanup failure')
        def logged(*args, **kwargs):
            finished.set()
        with patch.object(self.server.shutil, 'rmtree', side_effect=fail), \
                patch.object(self.server, '_server_log', side_effect=logged):
            self.assertEqual(self.delete(), {'deleted': self.pid})
            self.assertTrue(finished.wait(2))
            self.assertFalse(self.directory.exists())

    def test_running_local_job_is_rechecked_while_holding_project_lock(self):
        def snapshot(pid):
            self.assertTrue(store._lock(pid)._is_owned())
            return {'analyze': {'state': 'running'}}
        with patch.object(jobs, 'snapshot', side_effect=snapshot):
            status, data = self.dispatch('DELETE')
        self.assertEqual(status, 400)
        self.assertIn('任务在运行', data['error'])
        self.assertTrue(self.directory.exists())

    def test_remote_generation_is_rejected_without_local_worker(self):
        for state in ('submitting', 'running'):
            with self.subTest(state=state):
                store.update(self.pid, lambda d: d.update(runs={'seg': [{'rev': 'r1', 'state': state}]}))
                with patch.object(jobs, 'snapshot', return_value={}):
                    status, data = self.dispatch('DELETE')
                self.assertEqual(status, 400)
                self.assertEqual(data['error'], '这个项目还有视频在生成，等它结束后再删除')
                self.assertTrue(self.directory.exists())

    def test_pending_failed_completed_and_unknown_keep_existing_semantics(self):
        for state in ('pending_confirmation', 'failed', 'completed', 'unknown', 'superseded'):
            with self.subTest(state=state):
                self.pid = store.create('可删除状态')['id']
                store.update(self.pid, lambda d: d.update(runs={'seg': [{'rev': 'r1', 'state': state}]}))
                # Retain trash until the assertion, and never outlive temp patches.
                with patch.object(self.server, '_remove_trash'):
                    self.assertEqual(self.delete(), {'deleted': self.pid})
                self.assertFalse(store.project_dir(self.pid).exists())

    def test_deleted_pid_get_put_post_patch_delete_are_clear_404(self):
        with patch.object(self.server, '_remove_trash'):
            self.delete()
        for method, suffix in [('GET', ''), ('PUT', '/breakdown'), ('POST', '/assets'),
                               ('POST', '/analyze'), ('PATCH', ''), ('DELETE', '')]:
            with self.subTest(method=method, suffix=suffix):
                status, data = self.dispatch(method, suffix)
                self.assertEqual(status, 404)
                self.assertEqual(data, {'error': '项目已删除'})
                self.assertFalse(self.directory.exists())
        with self.assertRaises(ValueError):
            store.load('.trash')

    def test_missing_load_update_and_background_refresh_do_not_recreate(self):
        with patch.object(self.server, '_remove_trash'):
            self.delete()
        for operation in (lambda: store.load(self.pid), lambda: store.update(self.pid, lambda d: d.update(x=1))):
            with self.assertRaisesRegex(FileNotFoundError, '项目已删除'):
                operation()
        gateway.video_refresh_all(self.pid)
        jimmy_bridge.refresh_all(self.pid)
        self.assertIsNone(jimmy_bridge.refresh(self.pid, 'seg', 'r1'))
        pipeline.recover_stale(self.pid, {})
        self.assertFalse(self.directory.exists())

    def test_update_and_delete_serialize_without_partial_delete_or_recreation(self):
        entered, release, deleting = threading.Event(), threading.Event(), threading.Event()
        def update(data):
            entered.set()
            release.wait(3)
            data['marker'] = '完成写回'
        self.worker(lambda: store.update(self.pid, update))
        self.assertTrue(entered.wait(2))
        def delete():
            deleting.set()
            self.delete()
        try:
            with patch.object(self.server, '_remove_trash'):
                self.worker(delete)
                self.assertTrue(deleting.wait(2))
                release.set()
                self.join_workers()
            self.assertFalse(self.directory.exists())
            trash = next((config.PROJECTS_DIR / '.trash').iterdir())
            self.assertEqual(json.loads((trash / 'project.json').read_text())['marker'], '完成写回')
            with self.assertRaises(FileNotFoundError):
                store.update(self.pid, lambda d: d.update(late=True))
            self.assertFalse(self.directory.exists())
        finally:
            release.set()

    def test_inflight_video_refresh_does_not_recreate_project_after_delete(self):
        store.update(self.pid, lambda d: d.update(runs={'seg': [{'rev': 'r1', 'state': 'failed'}]}))
        entered, release = threading.Event(), threading.Event()
        original = gateway.video_refresh
        def paused(*args):
            entered.set()
            release.wait(3)
            return original(*args)
        try:
            with patch.object(gateway, 'video_refresh', side_effect=paused), \
                    patch.object(jobs, 'snapshot', return_value={'gen:seg:r1': {'state': 'done'}}), \
                    patch.object(self.server, '_remove_trash'):
                self.worker(lambda: gateway.video_refresh_all(self.pid))
                self.assertTrue(entered.wait(2))
                self.worker(self.delete)
                release.set()
                self.join_workers()
            self.assertFalse(self.directory.exists())
            gateway.video_refresh_all(self.pid)
            self.assertFalse(self.directory.exists())
        finally:
            release.set()

    def test_list_ignores_trash_even_with_direct_project_json(self):
        trash = config.PROJECTS_DIR / '.trash'
        trash.mkdir()
        (trash / 'project.json').write_text(json.dumps({'id': 'hidden', 'name': '隐藏项目'}))
        self.assertEqual([p['id'] for p in store.list_projects()], [self.pid])

    def test_slow_request_does_not_block_delete_feedback_or_project_list(self):
        entered, release, listed, refused = (threading.Event() for _ in range(4))
        result = []
        self.handler._body_json = lambda: {'task_id': 'seg'}
        def proposal(*args, **kwargs):
            entered.set()
            release.wait(3)
            return {'ok': True}
        def delete():
            result.append(self.dispatch('DELETE'))
            refused.set()
        def listing():
            self.assertEqual(len(self.handler._api('GET', ['projects'], {})), 1)
            listed.set()
        try:
            with patch.object(pipeline, 'dialogue_proposal', side_effect=proposal):
                self.worker(lambda: self.handler._api('POST', ['projects', self.pid, 'script', 'dialogue-proposal'], {}))
                self.assertTrue(entered.wait(2))
                self.worker(delete)
                self.worker(listing)
                self.assertTrue(refused.wait(1), '慢请求期间删除应立即给原因，不能等待模型超时')
                self.assertTrue(listed.wait(1), '项目列表不能等待慢请求')
                self.assertEqual(result[0][0], 400)
                self.assertIn('任务在运行', result[0][1]['error'])
                self.assertTrue(self.directory.exists())
        finally:
            release.set()
            self.join_workers()
        with patch.object(self.server, '_remove_trash'):
            self.assertEqual(self.delete(), {'deleted': self.pid})

    def test_failed_request_releases_deletion_guard(self):
        self.handler._body_json = lambda: {'task_id': 'seg'}
        with patch.object(pipeline, 'dialogue_proposal', side_effect=ValueError('offline failure')):
            with self.assertRaisesRegex(ValueError, 'offline failure'):
                self.handler._api('POST', ['projects', self.pid, 'script', 'dialogue-proposal'], {})
        with patch.object(self.server, '_remove_trash'):
            self.assertEqual(self.delete(), {'deleted': self.pid})

    def test_startup_cleans_residual_trash_offline(self):
        residue = config.PROJECTS_DIR / '.trash' / 'old-project-123'
        residue.mkdir(parents=True)
        (residue / 'project.json').write_text('{}')
        cleaned = threading.Event()
        original = self.server.shutil.rmtree
        def remove(path, **kwargs):
            original(path, **kwargs)
            cleaned.set()
        httpd = unittest.mock.MagicMock()
        with patch.object(self.server, 'ThreadingHTTPServer', return_value=httpd), \
                patch.object(self.server, 'PID_FILE', self.folder / 'server.pid'), \
                patch.object(self.server, '_watch_code_and_restart'), \
                patch.object(runtime_env, 'report', return_value={'runtime': 'offline', 'runtime_mb': 0, 'projects': 'temp', 'running_in_venv': True}), \
                patch.object(self.server.asr, 'available', return_value=False), \
                patch.object(self.server.shutil, 'rmtree', side_effect=remove), \
                patch.object(self.server, '_server_log'), patch('sys.stdout', new=io.StringIO()):
            self.server.main()
            self.assertTrue(cleaned.wait(2))
        self.assertFalse(residue.exists())
        self.assertTrue(self.directory.exists())


if __name__ == '__main__':
    unittest.main()
