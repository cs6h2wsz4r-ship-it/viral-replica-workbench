"""Offline transport policies; never read local credentials or invoke a provider."""
import io
import json
import unittest
from pathlib import Path
from unittest.mock import call, patch
import urllib.error

from console import config, llm


class GatewayTransportTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('secret read')))
        self.enterContext(patch.object(config, 'provider_settings', return_value={
            'model': 'offline-model', 'base_url': 'https://offline.invalid', 'api_key': 'fake', 'timeout': 1}))
        self.enterContext(patch.object(llm, '_ssl_context', return_value=None))
        self.enterContext(patch.object(llm, '_jimmy_dns_shim'))
        self.sleep = self.enterContext(patch.object(llm.time, 'sleep'))
        self.network = self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')))

    def test_gateway_modules_exist(self):
        base = Path(__file__).resolve().parents[1] / 'console/gateway'
        self.assertTrue((base / 'reasoning.py').is_file(), 'reasoning implementation must move to gateway')
        for name in ('official', 'third_party', 'deepseek', 'doubao', 'custom'):
            self.assertTrue((base / 'adapters' / (name + '.py')).is_file())

    def test_quota_http_429_does_not_retry_third_party(self):
        detail = json.dumps({'error': {'code': 'insufficient_quota', 'message': 'balance exhausted'}}).encode()
        self.network.side_effect = lambda *a, **k: (_ for _ in ()).throw(
            urllib.error.HTTPError('https://offline.invalid', 429, 'offline', {}, io.BytesIO(detail)))
        with self.assertRaises(llm.LLMError) as caught:
            llm._post('https://offline.invalid', {}, 'fake', 1, 'jimmy')
        self.assertEqual(self.network.call_count, 1)
        self.assertEqual(caught.exception.kind, 'quota')
        self.assertEqual(caught.exception.channel, 'third_party')
        self.sleep.assert_not_called()

    def test_official_http_200_temporary_error_retries(self):
        replies = [io.BytesIO(json.dumps({'error': {'code': 'server_error', 'message': 'temporarily unavailable'}}).encode()),
                   io.BytesIO(json.dumps({'choices': []}).encode())]
        self.network.side_effect = replies
        self.assertEqual(llm._post('https://offline.invalid', {}, 'fake', 1, 'openai'), {'choices': []})
        self.assertEqual(self.sleep.call_args_list, [call(5)])

    def test_compatible_http_200_nested_error_retries(self):
        for provider in ('jimmy', 'deepseek', 'doubao', 'custom'):
            with self.subTest(provider=provider):
                self.sleep.reset_mock()
                self.network.side_effect = [io.BytesIO(json.dumps({'error': {'code': 'server_error'}}).encode()),
                                            io.BytesIO(json.dumps({'choices': []}).encode())]
                self.assertEqual(llm._post('https://offline.invalid', {}, 'fake', 1, provider), {'choices': []})
                self.assertEqual(self.sleep.call_args_list, [call(5)])

    def test_compatible_http_200_quota_is_structured(self):
        self.network.side_effect = None
        self.network.return_value = io.BytesIO(json.dumps({'error': {'code': 'insufficient_quota'}}).encode())
        with self.assertRaises(llm.LLMError) as caught:
            llm._post('https://offline.invalid', {}, 'fake', 1, 'jimmy')
        self.assertEqual(caught.exception.kind, 'quota')
        self.sleep.assert_not_called()

    def test_official_image_http_200_quota_is_not_retried(self):
        from console.gateway.adapters import official
        from console.gateway.errors import ImageError
        self.network.side_effect = None
        self.network.return_value = io.BytesIO(json.dumps({'error': {'code': 'insufficient_quota'}}).encode())
        with patch.object(config, 'load', return_value={'OPENAI_API_KEY': 'fake'}), \
                patch('console.openai_image._multipart', return_value=(b'offline', 'multipart/form-data')):
            with self.assertRaises(ImageError) as caught:
                official.image_generate({'model': 'offline'}, Path('unused-image-output'))
        self.assertEqual(caught.exception.kind, 'quota')
        self.assertEqual(self.network.call_count, 1)
        self.sleep.assert_not_called()

    def test_official_image_http_200_transient_retries_backoff(self):
        from console.gateway.adapters import official
        from console.gateway.errors import ImageError
        self.network.side_effect = [io.BytesIO(json.dumps({'error': {'code': 'server_error'}}).encode()) for _ in range(4)]
        with patch.object(config, 'load', return_value={'OPENAI_API_KEY': 'fake'}), \
                patch('console.openai_image._multipart', return_value=(b'offline', 'multipart/form-data')):
            with self.assertRaises(ImageError) as caught:
                official.image_generate({'model': 'offline'}, Path('unused-image-output'))
        self.assertEqual(caught.exception.kind, 'transient')
        self.assertEqual(self.network.call_count, 4)
        self.assertEqual(self.sleep.call_args_list, [call(5), call(15), call(30)])

    def test_errors_sanitize_brand_and_secrets(self):
        from console.gateway.errors import GatewayError
        exc = GatewayError('Jimmy 请求失败 Bearer sk-private-value', kind='transient', channel='jimmy',
                           model='offline', technical='jimmy api_key=secret-value')
        self.assertEqual(exc.channel, 'third_party')
        self.assertNotIn('Jimmy', str(exc))
        self.assertNotIn('sk-private-value', str(exc))
        self.assertNotIn('secret-value', exc.technical)


if __name__ == '__main__':
    unittest.main()
