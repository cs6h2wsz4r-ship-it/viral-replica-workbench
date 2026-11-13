"""Update routes preserve build/origin checks and exclude project mutations."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tools.test_code_update import package
from console import runtime_env
import updater
with patch.object(runtime_env, 'apply'):
    import server


class UpdateAPITests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('No real network')))
        self.service = updater.Updater(root)
        self.enterContext(patch.object(server, 'UPDATES', self.service))
        self.enterContext(patch.object(server, 'build_status', return_value={
            'restart_pending':False, 'build':'test-build', 'server_build':'s', 'web_build':'w'}))
        self.handler = object.__new__(server.Handler)
        self.handler.headers = {'X-Client-Build':'test-build', 'Host':'127.0.0.1:8765', 'Origin':'http://127.0.0.1:8765'}
        self.handler._body_json = lambda: {'sha':'a'*40}

    def test_status_does_not_check_network(self):
        with patch.object(updater, 'download', side_effect=AssertionError('No implicit check')):
            result = self.handler._api('GET', ['updates'], {})
        self.assertEqual(result['repository'], 'alyunzhangu/viral-replica-workbench')

    def test_manual_check_after_auto_update_reports_current_without_installing(self):
        manifest = package({'server.py': b'# code'}, version='20261114-0100')[1]
        (self.service.root / updater.MANIFEST).write_text(json.dumps(manifest))
        (self.service.root / 'VERSION').write_text(manifest['version'])
        replies = [json.dumps({'sha': 'a' * 40}).encode(), json.dumps(manifest).encode()]
        with patch.object(updater, 'download', side_effect=replies) as fetch, \
                patch.object(updater, 'install', side_effect=AssertionError('Check only')):
            result = self.handler._api('POST', ['updates', 'check'], {})
        self.assertEqual(result['current_version'], '20261114-0100')
        self.assertIsNone(result['available'])
        self.assertIn('最新', result['message'])
        self.assertEqual(fetch.call_count, 2)

    def test_mutation_requires_current_page_build(self):
        self.handler.headers = {}
        with patch.object(self.service, 'start') as start:
            with self.assertRaises(server.ApiError): self.handler._api('POST', ['updates','apply'], {})
        start.assert_not_called()

    def test_cross_origin_update_rejected(self):
        self.handler.headers['Origin'] = 'https://unrelated.example'
        with patch.object(self.service, 'start') as start:
            with self.assertRaises(server.ApiError) as error: self.handler._api('POST', ['updates','apply'], {})
        self.assertEqual(error.exception.status, 403)
        start.assert_not_called()

    def test_apply_pins_confirmation_and_runs_shared_import_check(self):
        with patch.object(self.service, 'start', return_value={'phase':'downloading'}) as start, \
                patch.object(updater, 'validate_installed', return_value='') as validate:
            self.handler._api('POST', ['updates','apply'], {})
            self.assertEqual(start.call_args.args, ('a'*40,))
            self.assertIs(start.call_args.kwargs['busy'], server.jobs.any_running)
            start.call_args.kwargs['validate']()
            validate.assert_called_once_with(server.WEB.parent)

    def test_project_requests_are_blocked_during_update_but_status_remains_available(self):
        self.service.phase = 'installing'
        with patch.object(server.store, 'create') as create:
            with self.assertRaises(updater.UpdateError): self.handler._api('POST', ['projects'], {})
        create.assert_not_called()
        self.assertEqual(self.handler._api('GET', ['updates'], {})['phase'], 'installing')


if __name__ == '__main__': unittest.main(verbosity=2)
