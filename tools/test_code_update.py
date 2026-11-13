"""Code-only updates, tested against temporary installations without network."""
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
import sys
import os
import subprocess
import threading
import time
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import updater


def package(files, version='2026.11.13.1'):
    manifest = dict(format=1, runtime=1, version=version,
                    files={name: hashlib.sha256(data).hexdigest() for name, data in files.items()})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as archive:
        archive.writestr('release/code-update.json', json.dumps(manifest))
        for name, data in files.items():
            archive.writestr('release/' + name, data)
    return buf.getvalue(), manifest


class CodeUpdateFixture:
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('No real network')))
        self.files = {'server.py': b'print("new")\n', 'console/example.py': b'VALUE = 2\n',
                      'web/app.js': b'const version = 2;', 'VERSION': b'2026.11.13.1\n',
                      'updater.py': b'# updater\n'}
        self.service = updater.Updater(self.root)
        for name, data in {'server.py': b'print("old")\n', 'console/example.py': b'VALUE = 1\n',
                           'VERSION': b'old\n', 'projects/p1/project.json': b'{"saved":true}',
                           'console.env': b'PRIVATE_KEY=keep', 'runtime/models/model.bin': b'model',
                           'runtime/venv/keep': b'dependency', 'library/photo.jpg': b'photo',
                           'batches/batch.json': b'{}'}.items():
            p = self.root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
        self.protected = {name: (self.root / name).read_bytes() for name in
                          ('projects/p1/project.json', 'console.env', 'runtime/models/model.bin',
                           'runtime/venv/keep', 'library/photo.jpg', 'batches/batch.json')}

    def install(self, files=None, validate=lambda: ''):
        archive, manifest = package(files or self.files)
        return updater.install(self.root, archive, manifest, validate=validate)

    def assert_data_unchanged(self):
        self.assertEqual(self.protected, {name: (self.root / name).read_bytes() for name in self.protected})


class CodeUpdateTests(CodeUpdateFixture, unittest.TestCase):
    def test_updates_code_and_keeps_all_user_data_and_dependencies(self):
        backup = self.install()
        self.assertEqual((self.root / 'console/example.py').read_bytes(), self.files['console/example.py'])
        self.assertTrue(backup)
        self.assert_data_unchanged()

    def test_validation_failure_restores_previous_code_and_removes_added_files(self):
        with self.assertRaisesRegex(updater.UpdateError, '导入'):
            self.install(validate=lambda: '导入检查失败')
        self.assertEqual((self.root / 'server.py').read_bytes(), b'print("old")\n')
        self.assertFalse((self.root / 'web/app.js').exists())
        self.assertFalse((self.root / 'code-update.json').exists())
        self.assert_data_unchanged()

    def test_bad_hash_or_python_syntax_never_replaces_running_code(self):
        archive, manifest = package(self.files)
        manifest['files']['server.py'] = '0' * 64
        with self.assertRaises(updater.UpdateError):
            updater.install(self.root, archive, manifest)
        with self.assertRaises(updater.UpdateError):
            self.install({**self.files, 'console/example.py': b'def broken('})
        self.assertEqual((self.root / 'server.py').read_bytes(), b'print("old")\n')
        self.assert_data_unchanged()

    def test_valid_python_with_broken_server_import_rolls_back(self):
        files = {**self.files, 'server.py': b'import missing_workbench_dependency_for_update_test\n'}
        with self.assertRaisesRegex(updater.UpdateError, '无法.*加载'):
            self.install(files, validate=lambda: updater.validate_installed(self.root))
        self.assertEqual((self.root / 'server.py').read_bytes(), b'print("old")\n')
        self.assert_data_unchanged()

    def test_rollback_clears_same_second_same_size_python_cache(self):
        write = updater.atomic_write
        def frozen_write(path, data, mode=None):
            write(path, data, mode)
            if path.suffix == '.py': os.utime(path, (1700000000, 1700000000))
        def import_value():
            return subprocess.check_output([sys.executable, '-c', 'from console.example import VALUE; print(VALUE)'],
                                           cwd=self.root, text=True).strip()
        def invalid():
            self.assertEqual(import_value(), '2')
            return '模拟导入失败'
        with patch.object(updater, 'atomic_write', side_effect=frozen_write):
            with self.assertRaises(updater.UpdateError): self.install(validate=invalid)
        self.assertEqual(import_value(), '1')

    def test_protected_and_traversal_paths_cannot_be_installed(self):
        for path in ('console.env', 'projects/p1/project.json', 'runtime/venv/keep', '../outside.py',
                     '/outside.py', 'console/../console.env', 'console\\escape.py', 'web/.secret',
                     'update.command', 'update.bat', 'docs/code-updates.md'):
            with self.subTest(path=path), self.assertRaises(updater.UpdateError):
                self.install({**self.files, path: b'overwrite'})
        self.assert_data_unchanged()

    def test_symlink_destination_is_rejected(self):
        outside = self.root / 'projects/p1/project.json'
        (self.root / 'console/example.py').unlink()
        (self.root / 'console/example.py').symlink_to(outside)
        with self.assertRaises(updater.UpdateError): self.install()
        self.assert_data_unchanged()

    def test_manual_rollback_restores_code_without_touching_project_changes(self):
        self.install()
        (self.root / 'projects/p1/project.json').write_bytes(b'{"edited_after_update":true}')
        updater.rollback(self.root)
        self.assertEqual((self.root / 'server.py').read_bytes(), b'print("old")\n')
        self.assertEqual((self.root / 'projects/p1/project.json').read_bytes(), b'{"edited_after_update":true}')

    def test_recovery_restores_interrupted_install(self):
        self.install()
        latest = json.loads((self.root / 'runtime/updates/latest.json').read_text())
        (self.root / 'runtime/updates/pending.json').write_text(json.dumps({'backup': latest['backup'], 'state': 'installing'}))
        updater.recover(self.root)
        self.assertEqual((self.root / 'server.py').read_bytes(), b'print("old")\n')
        self.assert_data_unchanged()

    def test_only_previous_managed_files_are_removed(self):
        old = {'format': 1, 'runtime': 1, 'version': '2026.1', 'files': {'console/retired.py': '0' * 64}}
        (self.root / 'code-update.json').write_text(json.dumps(old))
        (self.root / 'console/retired.py').write_text('OLD = True')
        (self.root / 'console/local_extra.py').write_text('LOCAL = True')
        self.install()
        self.assertFalse((self.root / 'console/retired.py').exists())
        self.assertTrue((self.root / 'console/local_extra.py').exists())
        updater.rollback(self.root)
        self.assertTrue((self.root / 'console/retired.py').exists())

    def test_busy_project_or_request_blocks_install(self):
        self.service.release = {'sha': 'a' * 40, 'manifest': package(self.files)[1]}
        with self.assertRaisesRegex(updater.UpdateError, '任务'):
            self.service.start('a' * 40, busy=lambda: True)
        with self.service.activity():
            with self.assertRaisesRegex(updater.UpdateError, '请求'):
                self.service.start('a' * 40)

    def test_stale_confirmation_cannot_install_different_revision(self):
        self.service.release = {'sha': 'a' * 40, 'manifest': package(self.files)[1]}
        with self.assertRaisesRegex(updater.UpdateError, '重新检查'):
            self.service.start('b' * 40)

    def test_check_pins_manifest_to_commit_and_never_applies_code(self):
        manifest = package(self.files)[1]
        replies = [json.dumps({'sha': 'a' * 40}).encode(), json.dumps(manifest).encode()]
        with patch.object(updater, 'download', side_effect=replies) as fetch:
            result = self.service.check()
        self.assertEqual(result['available']['sha'], 'a' * 40)
        self.assertIn('/' + 'a' * 40 + '/', fetch.call_args_list[1].args[0])
        self.assertEqual((self.root / 'server.py').read_bytes(), b'print("old")\n')

    def test_validation_does_not_initialize_runtime_directories(self):
        (self.root / 'console/runtime_env.py').write_text(
            'from pathlib import Path\ndef apply():\n    Path("runtime/should-not-exist").mkdir()\n')
        (self.root / 'server.py').write_text('from console import runtime_env\nruntime_env.apply()\n')
        self.assertEqual(updater.validate_installed(self.root), '')
        self.assertFalse((self.root / 'runtime/should-not-exist').exists())

    def test_manifest_builder_only_writes_manifest(self):
        from tools.build_code_update import build
        (self.root / 'VERSION').write_text('20261114-0100\n')
        before = {p.relative_to(self.root) for p in self.root.rglob('*')}
        build(self.root)
        after = {p.relative_to(self.root) for p in self.root.rglob('*')}
        self.assertEqual(after - before, {Path(updater.MANIFEST)})
        manifest = updater.validate_manifest(json.loads((self.root / updater.MANIFEST).read_text()))
        self.assertEqual(manifest['files']['server.py'], hashlib.sha256((self.root / 'server.py').read_bytes()).hexdigest())

    def test_previous_manifest_can_include_retired_upgrade_files(self):
        old = {'format': 1, 'runtime': 1, 'version': '2026.1', 'files': {
            'server.py': '0' * 64, 'update.command': '0' * 64,
            'update.bat': '0' * 64, 'docs/code-updates.md': '0' * 64}}
        (self.root / updater.MANIFEST).write_text(json.dumps(old))
        self.install()
        self.assertEqual((self.root / 'server.py').read_bytes(), self.files['server.py'])

    def test_old_backups_with_retired_paths_can_recover_and_roll_back(self):
        backup = 'b' * 32
        folder = self.root / 'runtime/updates/backups' / backup
        files = {'server.py': b'# previous code\n', 'update.command': b'# old launcher\n',
                 'update.bat': b'@echo off\n', 'docs/code-updates.md': b'old instructions'}
        record = {'files': {}}
        for name, data in files.items():
            updater.atomic_write(folder / 'files' / name, data)
            record['files'][name] = {'exists': True, 'sha256': hashlib.sha256(data).hexdigest(), 'mode': 0o644}
        updater.write_json(folder / 'record.json', record)
        updater.write_json(self.root / 'runtime/updates/pending.json', {'state': 'installing', 'backup': backup})
        self.assertTrue(updater.recover(self.root))
        self.assertEqual({name: (self.root / name).read_bytes() for name in files}, files)
        (self.root / 'server.py').write_bytes(b'# changed again')
        updater.write_json(self.root / 'runtime/updates/latest.json', {'backup': backup})
        updater.rollback(self.root)
        self.assertEqual((self.root / 'server.py').read_bytes(), files['server.py'])
        self.assert_data_unchanged()


class AutoUpdateTests(CodeUpdateFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict(os.environ, {'VRC_NO_AUTO_UPDATE': '0'}))
        self.raw, self.manifest = package(self.files)
        self.sha = 'a' * 40
        self.urls, self.timeouts = [], []
        self.fetch = self.enterContext(patch.object(updater, 'download', side_effect=self.fake_download))
        self.validate = self.enterContext(patch.object(updater, 'validate_installed', return_value=''))

    def fake_download(self, url, limit=updater.MAX_DOWNLOAD, **kwargs):
        self.urls.append(url)
        self.timeouts.append(kwargs.get('timeout'))
        if url.endswith('/commits/main'):
            return json.dumps({'sha': self.sha}).encode()
        if url.endswith('/' + self.sha + '/' + updater.MANIFEST):
            return json.dumps(self.manifest).encode()
        if url.endswith('/zipball/' + self.sha):
            return self.raw
        raise AssertionError('Unexpected update URL')

    def run_auto(self):
        output = io.StringIO()
        original_open = Path.open
        def guarded_open(path, *args, **kwargs):
            if path.name == 'console.env':
                raise AssertionError('Auto updater must not read configuration')
            return original_open(path, *args, **kwargs)
        with patch.object(updater, '__file__', str(self.root / 'updater.py')), \
                patch.object(sys, 'argv', ['updater.py', '--auto']), \
                patch('builtins.input', side_effect=AssertionError('No confirmation')), \
                patch.object(Path, 'open', guarded_open), redirect_stdout(output):
            self.assertEqual(updater.main(), 0)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1, output.getvalue())
        self.assert_data_unchanged()
        return lines[0]

    def test_already_current_does_not_download_zip_or_validate(self):
        (self.root / updater.MANIFEST).write_text(json.dumps(self.manifest))
        self.assertIn('已是最新版本', self.run_auto())
        self.assertEqual(len(self.urls), 2)
        self.validate.assert_not_called()

    def test_installs_pinned_release_and_validates_without_confirmation(self):
        self.assertIn('已更新到 2026.11.13.1 版本', self.run_auto())
        self.assertEqual((self.root / 'server.py').read_bytes(), self.files['server.py'])
        self.assertEqual(json.loads((self.root / updater.MANIFEST).read_text()), self.manifest)
        self.validate.assert_called_once_with(self.root)
        self.assertEqual(len(self.urls), 3)
        self.assertTrue(all(0 < t <= 15 for t in self.timeouts[:2]))
        self.assertTrue(15 < self.timeouts[2] <= 120)

    def test_offline_and_unexpected_errors_never_fail_startup(self):
        for error in (urllib.error.URLError('offline'), TimeoutError('timeout'),
                      RuntimeError('unexpected\nsecond line'), OSError('disk'),
                      updater.UpdateError('更新服务暂时限流'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                self.fetch.side_effect = error
                self.assertIn('跳过', self.run_auto())
                self.assertEqual((self.root / 'server.py').read_bytes(), b'print("old")\n')

    def test_hash_mismatch_keeps_previous_code(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as archive:
            archive.writestr('release/' + updater.MANIFEST, json.dumps(self.manifest))
            for name, data in self.files.items():
                archive.writestr('release/' + name, b'corrupt' if name == 'server.py' else data)
        self.raw = buf.getvalue()
        self.assertIn('校验失败', self.run_auto())
        self.assertEqual((self.root / 'server.py').read_bytes(), b'print("old")\n')
        self.assertFalse((self.root / updater.MANIFEST).exists())
        self.validate.assert_not_called()

    def test_installed_validation_failure_restores_code_and_manifest(self):
        self.validate.return_value = '导入失败\n诊断'
        self.assertIn('跳过', self.run_auto())
        self.assertEqual((self.root / 'server.py').read_bytes(), b'print("old")\n')
        self.assertFalse((self.root / 'web/app.js').exists())
        self.assertFalse((self.root / updater.MANIFEST).exists())
        self.assertFalse((self.root / 'runtime/updates/pending.json').exists())

    def test_git_directory_and_worktree_file_skip_before_lock_or_network(self):
        for kind in ('directory', 'file'):
            git = self.root / '.git'
            if kind == 'directory': git.mkdir()
            else: git.write_text('gitdir: ignored')
            with patch.object(updater, 'acquire_lock', side_effect=AssertionError('No lock needed')):
                self.assertIn('git pull', self.run_auto())
            if kind == 'directory': git.rmdir()
            else: git.unlink()
        self.fetch.assert_not_called()

    def test_environment_opt_out_skips_before_lock_or_network(self):
        with patch.dict(os.environ, {'VRC_NO_AUTO_UPDATE': '1'}), \
                patch.object(updater, 'acquire_lock', side_effect=AssertionError('No lock needed')):
            self.assertIn('VRC_NO_AUTO_UPDATE', self.run_auto())
        self.fetch.assert_not_called()

    def test_running_legacy_process_skips_without_network(self):
        (self.root / 'runtime/server.pid').write_text(str(os.getpid()))
        self.assertIn('运行', self.run_auto())
        self.fetch.assert_not_called()

    def test_occupied_lock_skips_without_network(self):
        with updater.acquire_lock(self.root):
            self.assertIn('运行', self.run_auto())
        self.fetch.assert_not_called()

    def test_failure_before_lock_is_still_success_exit(self):
        with patch.object(updater, 'legacy_server_running', side_effect=RuntimeError('unexpected')):
            self.assertIn('跳过', self.run_auto())
        self.fetch.assert_not_called()

    def test_malformed_release_and_download_failure_release_lock(self):
        for replies in ([b'invalid json'], [b'{"sha":"not-a-sha"}'],
                        [json.dumps({'sha': self.sha}).encode(), b'{}'],
                        [json.dumps({'sha': self.sha}).encode(), json.dumps(self.manifest).encode(),
                         updater.UpdateError('下载失败')]):
            with self.subTest(replies=len(replies)):
                self.fetch.side_effect = replies
                self.assertIn('跳过', self.run_auto())
                with updater.acquire_lock(self.root):
                    pass
                self.assertEqual((self.root / 'server.py').read_bytes(), b'print("old")\n')

    def test_interrupted_install_is_recovered_before_check(self):
        backup = updater.install(self.root, self.raw, self.manifest)
        (self.root / 'runtime/updates/pending.json').write_text(json.dumps({'state': 'installing', 'backup': backup}))
        self.fetch.side_effect = updater.UpdateError('断网')
        self.assertIn('跳过', self.run_auto())
        self.assertEqual((self.root / 'server.py').read_bytes(), b'print("old")\n')
        self.assertFalse((self.root / 'runtime/updates/pending.json').exists())

    def test_metadata_requests_share_one_time_budget(self):
        with patch.object(updater.time, 'monotonic', side_effect=[100, 101, 110]):
            self.service.check(timeout=15)
        self.assertEqual(self.timeouts, [14, 5])


class DownloadDeadlineTests(unittest.TestCase):
    def test_total_timeout_bounds_blocked_connection_and_slow_read(self):
        for stage in ('connect', 'read'):
            with self.subTest(stage=stage):
                unblock = threading.Event()
                finished = threading.Event()
                def stall(*args, **kwargs):
                    unblock.wait(2)
                    finished.set()
                    return b'' if stage == 'read' else io.BytesIO(b'')
                response = io.BytesIO(b'')
                response.read = stall
                started = time.monotonic()
                with patch('ssl.create_default_context'), patch('urllib.request.urlopen',
                        side_effect=stall if stage == 'connect' else lambda *a, **kw: response):
                    try:
                        with self.assertRaisesRegex(updater.UpdateError, '超时'):
                            updater.download('https://offline.invalid/', timeout=.05)
                    finally:
                        unblock.set()
                        finished.wait(1)
                self.assertLess(time.monotonic() - started, 1)

    def test_download_size_cap_and_http_errors_are_chinese(self):
        for reply, message in ((io.BytesIO(b'1234'), '大小'),
                               (urllib.error.HTTPError('offline', 429, '', {}, None), '限流')):
            with patch('ssl.create_default_context'), patch('urllib.request.urlopen') as fetch:
                if isinstance(reply, Exception): fetch.side_effect = reply
                else: fetch.return_value = reply
                with self.assertRaisesRegex(updater.UpdateError, message):
                    updater.download('https://offline.invalid/', limit=3)


class LauncherTests(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'macOS launcher requires bash')
    def test_launcher_continues_when_auto_updater_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'start.command').write_bytes((Path(__file__).resolve().parents[1] / 'start.command').read_bytes())
            (root / 'runtime/venv/bin').mkdir(parents=True)
            (root / 'runtime/INSTALLED.txt').touch()
            executable = root / 'runtime/venv/bin/python'
            executable.write_text('#!/bin/bash\necho "$*" >> calls.log\n'
                                  'if [ "$2" = "--auto" ]; then exit 17; fi\nexit 0\n')
            executable.chmod(0o755)
            (root / 'bin').mkdir()
            for name in ('lsof', 'ps', 'open', 'sleep'):
                stub = root / 'bin' / name
                stub.write_text('#!/bin/bash\nexit 1\n')
                stub.chmod(0o755)
            result = subprocess.run(['bash', str(root / 'start.command')], cwd=root,
                env={**os.environ, 'PATH': str(root / 'bin') + os.pathsep + os.environ['PATH']},
                capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('正在检查更新', result.stdout)
            self.assertEqual((root / 'calls.log').read_text().splitlines(), ['updater.py --auto', 'server.py'])


if __name__ == '__main__': unittest.main(verbosity=2)
