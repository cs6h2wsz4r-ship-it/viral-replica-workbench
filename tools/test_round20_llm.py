"""Official inference regressions: config and every transport are isolated mocks."""
import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import call, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import config, llm


def reply(content='{}', reasoning=0):
    return {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}],
            'usage': {'completion_tokens_details': {'reasoning_tokens': reasoning}}}


def http_error(status, code='', param='', message='offline error'):
    body = json.dumps({'error': {'code': code, 'param': param, 'message': message}}).encode()
    return urllib.error.HTTPError('https://offline.invalid', status, 'offline', {}, io.BytesIO(body))


class OfficialLLMTests(unittest.TestCase):
    def setUp(self):
        self.values = {**config.DEFAULTS, 'OPENAI_API_KEY': 'mock-official',
                       'OPENAI_BASE_URL': 'https://api.openai.com/v1', 'OPENAI_MODEL': 'gpt-5.6-sol',
                       'OPENAI_REASONING_EFFORT': '', 'DEEPSEEK_API_KEY': 'mock-deepseek',
                       'DOUBAO_API_KEY': 'mock-doubao',
                       'CUSTOM_API_KEY': 'mock-custom', 'CUSTOM_BASE_URL': 'https://custom.invalid/v1',
                       'CUSTOM_MODEL': 'custom-model'}
        self.enterContext(patch.object(config, '_parse', return_value={'JIMMY_API_KEY': 'mock-third-party'}))
        self.enterContext(patch.object(config, 'load', side_effect=lambda: dict(self.values)))
        self.enterContext(patch.object(llm, '_ssl_context', return_value=None))
        self.enterContext(patch.object(llm, '_jimmy_dns_shim'))
        self.sleep = self.enterContext(patch.object(llm.time, 'sleep'))
        self.transport = self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('unmocked network')))
        self.requests = []
        self.messages = [{'role': 'user', 'content': '只输出 JSON'}]

    def replies(self, *responses):
        items = iter(responses)

        def send(req, **kwargs):
            self.requests.append(req)
            item = next(items)
            if isinstance(item, Exception):
                raise item
            return io.BytesIO(json.dumps(item).encode())

        self.transport.side_effect = send

    def test_official_defaults_ready_mask_and_dynamic_default(self):
        self.assertIn('openai', config.PROVIDERS)
        self.assertEqual(config.PROVIDERS['openai']['label'], '官方 · OpenAI')
        self.assertEqual(config.DEFAULTS['OPENAI_MODEL'], 'gpt-5.6-sol')
        self.assertEqual(config.DEFAULTS['OPENAI_IMAGE_MODEL'], 'gpt-image-2')
        self.assertEqual(config.DEFAULTS['OPENAI_BASE_URL'], 'https://api.openai.com/v1')
        self.assertEqual(config.DEFAULTS['OPENAI_REASONING_EFFORT'], '')
        self.assertIn('OPENAI_API_KEY', config.SECRET_KEYS)
        self.assertEqual(config.default_llm(), 'openai')
        public = config.public_view()
        self.assertNotEqual(public['OPENAI_API_KEY'], self.values['OPENAI_API_KEY'])
        self.assertTrue(public['_providers']['openai']['ready'])
        self.values.update(OPENAI_API_KEY='', DEFAULT_LLM='deepseek')
        self.assertEqual(config.default_llm(), 'deepseek')

    def test_official_request_format_and_optional_reasoning(self):
        for effort in ['', 'high']:
            with self.subTest(effort=effort):
                self.values['OPENAI_REASONING_EFFORT'] = effort
                self.replies(reply())
                llm.chat('openai', self.messages, max_tokens=300)
                request = self.requests[-1]
                expected = {'model': 'gpt-5.6-sol', 'messages': self.messages, 'max_completion_tokens': 300}
                if effort:
                    expected['reasoning_effort'] = effort
                self.assertEqual(json.loads(request.data), expected)
                self.assertEqual(request.full_url, 'https://api.openai.com/v1/chat/completions')

    def test_compatible_channels_preserve_exact_request_bytes(self):
        for provider in ['deepseek', 'doubao', 'custom', 'jimmy']:
            with self.subTest(provider=provider):
                self.replies(reply())
                settings = config.provider_settings(provider)
                llm.chat(provider, self.messages, temperature=.3, max_tokens=123)
                expected = {'model': settings['model'], 'messages': self.messages, 'temperature': .3, 'max_tokens': 123}
                self.assertEqual(self.requests[-1].data, json.dumps(expected, ensure_ascii=False).encode())

    def test_official_permanent_http_errors_never_retry(self):
        for status, code, expected in [(401, '', '官方 key 无效或没有权限'),
                                       (403, '', '官方 key 无效或没有权限'),
                                       (429, 'insufficient_quota', '官方额度不足')]:
            with self.subTest(status=status, code=code):
                self.requests.clear()
                self.replies(http_error(status, code))
                with self.assertRaisesRegex(llm.LLMError, expected) as caught:
                    llm.chat_json_checked('openai', self.messages, analysis={'provider': 'jimmy'})
                self.assertIn('改用三方渠道重试', str(caught.exception))
                self.assertEqual(len(self.requests), 1)
                self.sleep.assert_not_called()

    def test_official_transient_http_errors_use_existing_backoff(self):
        for status in [429, 500, 503]:
            with self.subTest(status=status):
                self.sleep.reset_mock()
                self.replies(*(http_error(status) for _ in range(3)), reply())
                self.assertEqual(llm.chat('openai', self.messages)['content'], '{}')
                self.assertEqual(self.sleep.call_args_list, [call(5), call(15), call(30)])

    def test_exhausted_http_retries_do_not_restart_checked_or_change_channel(self):
        self.replies(*(http_error(500) for _ in range(4)))
        with self.assertRaisesRegex(llm.LLMError, '改用三方渠道重试'):
            llm.chat_json_checked('openai', self.messages, analysis={'provider': 'jimmy'})
        self.assertEqual(len(self.requests), 4)
        self.assertTrue(all(r.full_url.startswith('https://api.openai.com/') for r in self.requests))

    def test_future_temperature_downgrade_does_not_spend_http_retry_budget(self):
        for code in ['unsupported_parameter', 'unsupported_value']:
            with self.subTest(code=code):
                self.requests.clear()
                self.sleep.reset_mock()
                self.replies(http_error(400, code, 'temperature'), *(http_error(500) for _ in range(3)), reply())
                original = {'model': 'gpt-5.6-sol', 'messages': self.messages,
                            'temperature': .4, 'max_completion_tokens': 300}
                llm._post('https://api.openai.com/v1/chat/completions', original, 'mock', 1, 'openai')
                self.assertIn('temperature', json.loads(self.requests[0].data))
                self.assertTrue(all('temperature' not in json.loads(r.data) for r in self.requests[1:]))
                self.assertEqual(len(self.requests), 5)
                self.assertEqual(self.sleep.call_args_list, [call(5), call(15), call(30)])
                self.assertIn('temperature', original)

    def test_reasoning_exhausted_retries_larger_budget_without_cross_channel(self):
        for analysis in [{}, {'provider': 'jimmy', 'model': 'third-model'}, {'provider': 'openai', 'model': 'gpt-5.6-sol'}]:
            with self.subTest(analysis=analysis):
                self.requests.clear()
                self.replies(reply('', 4000), reply('', 16000))
                with self.assertRaisesRegex(llm.CheckedLLMError, '改用三方渠道重试') as caught:
                    llm.chat_json_checked('openai', self.messages, analysis=analysis, max_tokens=4000)
                self.assertEqual([json.loads(r.data)['max_completion_tokens'] for r in self.requests], [4000, 16000])
                self.assertEqual([a['provider'] for a in caught.exception.attempts], ['openai', 'openai'])

    def test_official_analysis_model_can_retry_within_official_channel(self):
        self.replies(reply('', 4000), reply('', 16000), reply())
        _, meta = llm.chat_json_checked('openai', self.messages, max_tokens=4000,
                                      analysis={'provider': 'openai', 'model': 'other-official-model'})
        self.assertEqual([json.loads(r.data)['max_completion_tokens'] for r in self.requests], [4000, 16000, 8000])
        self.assertEqual(meta['provider'], 'openai')
        self.assertEqual(meta['model'], 'other-official-model')

    def test_reasoning_retry_can_complete_and_retains_official_usage(self):
        self.replies(reply('', 4000), reply('{"ok":true}', 123))
        value, meta = llm.chat_json_checked('openai', self.messages, max_tokens=4000,
                                          analysis={'provider': 'jimmy'})
        self.assertEqual(value, {'ok': True})
        self.assertEqual(meta['provider'], 'openai')
        self.assertEqual([a['max_tokens'] for a in meta['attempts']], [4000, 16000])
        self.assertEqual(meta['usage']['completion_tokens_details']['reasoning_tokens'], 123)

    def test_unrepairable_official_json_includes_manual_retry_hint(self):
        self.replies(reply('invalid'), reply('still invalid'))
        with self.assertRaisesRegex(llm.JSONResponseError, '改用三方渠道重试'):
            llm.chat_json('openai', self.messages)
        self.assertEqual(len(self.requests), 2)

    def test_official_large_request_is_blocked_before_transport(self):
        with self.assertRaisesRegex(llm.LLMError, '请求上限'):
            llm.chat('openai', [{'role': 'user', 'content': 'x' * llm._REQUEST_LIMIT}])
        self.transport.assert_not_called()

    def test_official_inline_images_share_image_request_limit(self):
        self.replies(reply())
        part = {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + 'a' * llm._REQUEST_LIMIT}}
        with patch.object(llm, '_smaller_inline_image', return_value='bW9jaw==') as shrink:
            llm.chat('openai', [{'role': 'user', 'content': [part]}])
        shrink.assert_called_once()
        self.assertLess(len(self.requests[0].data), llm._REQUEST_LIMIT)
        self.assertIn('data:image/jpeg;base64,bW9jaw==', self.requests[0].data.decode())

    def test_shared_official_http_error_policy(self):
        detail = json.dumps({'error': {'code': 'insufficient_quota'}})
        self.assertFalse(llm.official_retryable(429, detail))
        self.assertFalse(llm.official_retryable(401, '{}'))
        self.assertTrue(llm.official_retryable(429, '{}'))
        self.assertTrue(llm.official_retryable(500, '{}'))
        self.assertIn('官方额度不足', llm.official_error(429, detail))
        self.assertIn('OPENAI_API_KEY', llm.official_error(401, '{}'))


if __name__ == '__main__':
    unittest.main()
