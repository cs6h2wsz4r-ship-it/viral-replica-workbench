"""24.14: temporary configuration/projects only; every transport is offline."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from console import config, gateway, runtime_env, store


class Round24_14Tests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory(dir=config.ROOT / 'runtime/temp')))
        self.enterContext(patch.object(config, 'ENV_FILE', self.folder / 'mock-settings.conf'))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.folder / 'mock-projects'))
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('禁止真实模型请求')))
        with patch.object(runtime_env, 'apply'):
            import server
        self.server = server
        self.enterContext(patch.object(server, 'build_status', return_value={
            'build': 'offline', 'server_build': 'offline', 'web_build': 'offline', 'restart_pending': False}))
        self.handler = object.__new__(server.Handler)
        self.handler.headers = {'X-Client-Build': 'offline'}

    def test_three_cards_configuration_channels_models_and_shared_keys(self):
        config.save({'OPENAI_API_KEY': 'offline-official', 'JIMMY_API_KEY': 'offline-third',
                     'ROUTE_ANALYSIS': 'deepseek', 'ROUTE_SCRIPT': 'deepseek', 'DEFAULT_LLM': 'deepseek',
                     'DEEPSEEK_MODEL': 'configured-reasoner', 'ROUTE_CHECK': 'follow:script',
                     'ROUTE_DIALOGUE_PROPOSAL': 'follow:script', 'ROUTE_CAMERA_PROMPT': 'follow:analysis',
                     'ROUTE_CAMERA_IMAGE': 'third_party', 'ROUTE_VIDEO': 'third_party'})
        for step in ('analysis', 'script', 'check', 'dialogue_proposal', 'camera_prompt'):
            self.assertEqual(gateway.routes.resolve(step), {'channel': 'deepseek', 'model': 'configured-reasoner'})
        self.assertEqual(gateway.routes.resolve('camera_image'), {'channel': 'third_party', 'model': 'gpt-image-2'})
        self.assertEqual(gateway.routes.resolve('video')['model'], 'seedance2.0-mini-video')
        config.save({'OPENAI_API_KEY': '', 'JIMMY_API_KEY': '', 'ROUTE_ANALYSIS': 'jimmy',
                     'ROUTE_SCRIPT': 'jimmy', 'JIMMY_LLM_MODEL': 'configured-third-model'})
        self.assertEqual(config.load()['OPENAI_API_KEY'], 'offline-official')
        self.assertEqual(config.load()['JIMMY_API_KEY'], 'offline-third')
        self.assertEqual(gateway.routes.resolve('analysis')['model'], 'configured-third-model')
        view = config.public_view()
        self.assertNotIn('offline-official', json.dumps(view))
        self.assertNotIn('offline-third', json.dumps(view))

    def test_temporary_reason_and_image_overrides_never_remembered(self):
        config.save({'OPENAI_API_KEY': 'offline', 'JIMMY_API_KEY': 'offline'})
        pid = store.create('temporary selection')['id']
        before = (store.project_dir(pid) / 'project.json').read_bytes()
        route = self.server._route_selection(pid, 'script', {'provider': 'deepseek', 'route_selected': True})
        self.assertEqual(route['channel'], 'deepseek')
        self.handler._body_json = lambda: {'channel': 'third_party', 'route_selected': True, 'for_task': 'camera:C1:S1'}
        with patch.object(gateway, 'image_preview', return_value={}) as preview:
            self.handler._api('POST', ['projects', pid, 'images', 'preview'], {})
        self.assertEqual(preview.call_args.kwargs['override']['channel'], 'third_party')
        self.assertEqual((store.project_dir(pid) / 'project.json').read_bytes(), before)

    def test_clear_only_requested_legacy_step_and_preserve_compatibility(self):
        config.save({'OPENAI_API_KEY': 'offline'})
        pid = store.create('legacy route')['id']
        store.update(pid, lambda d: d.update(routes={'script': 'deepseek:old-model', 'camera_image': 'third_party:gpt-image-2'}, marker='keep'))
        before = copy.deepcopy(store.load(pid))
        self.assertEqual(gateway.routes.resolve('dialogue_proposal', before)['model'], 'old-model')
        result = self.handler._api('DELETE', ['projects', pid, 'routes', 'script'], {})
        expected = {**before, 'routes': {'camera_image': 'third_party:gpt-image-2'}}
        self.assertEqual(store.load(pid), expected)
        self.assertNotIn('script', result['routes'])
        self.assertEqual(gateway.routes.resolve('dialogue_proposal', result)['channel'], 'openai')
        with self.assertRaises(ValueError):
            self.handler._api('DELETE', ['projects', pid, 'routes', 'unknown'], {})
        self.assertEqual(store.load(pid), expected)
        self.handler._api('DELETE', ['projects', pid, 'routes', 'script'], {})
        self.assertEqual(store.load(pid), expected)

    def test_local_probe_installed_missing_and_incomplete_without_downloads(self):
        from console import local_models
        hub = self.folder / 'hub'
        repo = hub / 'models--Systran--faster-whisper-medium'
        snapshot = repo / 'snapshots' / 'offline-revision'
        snapshot.mkdir(parents=True)
        (repo / 'refs').mkdir()
        (repo / 'refs/main').write_text('offline-revision')
        for name in ('config.json', 'tokenizer.json'):
            (snapshot / name).write_text('{}')
        (snapshot / 'model.bin').write_bytes(b'offline weights')
        align = [{'language': language, 'status': 'ok', 'model': 'offline', 'path': 'local'} for language in ('zh', 'en')]
        with patch.object(local_models, 'HF_HUB', hub), patch.object(local_models.asr, 'engines', return_value=['faster-whisper']), \
                patch.object(local_models.align_models, 'report', return_value=align), \
                patch.object(local_models.shutil, 'which', side_effect=lambda name: '/offline/' + name):
            rows = local_models.report('medium')
            self.assertEqual([r['status'] for r in rows], ['ok'] * 4)
            (snapshot / 'model.bin').unlink()
            self.assertEqual(local_models.report('medium')[0]['status'], 'broken')
            self.assertEqual(local_models.report('small')[0]['status'], 'missing')
        with patch.object(local_models.asr, 'engines', return_value=[]), patch.object(local_models.align_models, 'report', return_value=[
                {'language': 'zh', 'status': 'missing'}, {'language': 'en', 'status': 'broken', 'detail': '文件损坏'}]), \
                patch.object(local_models.shutil, 'which', return_value=None):
            rows = local_models.report('medium')
            self.assertEqual([r['status'] for r in rows], ['broken', 'missing', 'broken', 'missing'])

    def test_local_probe_endpoint_is_read_only_and_connection_uses_mock(self):
        from console import local_models
        config.save({'ASR_MODEL': 'medium'})
        before = config.ENV_FILE.read_bytes()
        with patch.object(local_models, 'report', return_value=[{'name': 'FFmpeg', 'status': 'missing'}]) as probe:
            self.assertEqual(self.handler._api('GET', ['settings', 'local-models'], {}), {'models': [{'name': 'FFmpeg', 'status': 'missing'}]})
            probe.assert_called_once_with('medium')
        self.handler._body_json = lambda: {'channel': 'openai'}
        with patch.object(gateway, 'test_connection', return_value={'status': '成功', 'elapsed_ms': 0}) as mock:
            self.assertEqual(self.handler._api('POST', ['settings', 'test-connection'], {})['status'], '成功')
            mock.assert_called_once_with('openai')
        self.assertEqual(config.ENV_FILE.read_bytes(), before)

    def test_whisper_aliases_use_the_installed_library_repository_map(self):
        from console import local_models
        aliases = {'large': 'Systran/faster-whisper-large-v3',
                   'turbo': 'mobiuslabsgmbh/faster-whisper-large-v3-turbo',
                   'distil-large-v3': 'Systran/faster-distil-whisper-large-v3'}
        with patch.object(local_models, 'HF_HUB', self.folder / 'hub'), \
                patch.object(local_models.asr, 'engines', return_value=['faster-whisper']):
            for alias, repo in aliases.items():
                row = local_models.report(alias)[0]
                self.assertEqual(row['path'], str(self.folder / 'hub' / ('models--' + repo.replace('/', '--'))))
                self.assertEqual(row['status'], 'missing')


if __name__ == '__main__':
    unittest.main()
