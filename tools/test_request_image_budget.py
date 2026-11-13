"""Self-contained image-budget regressions; synthetic media and no network/config."""
import base64
import copy
import io
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from console import llm, segment_plan, storyboard
from console.gateway.adapters import common
from console.gateway.errors import CheckedLLMError
from console.gateway import reasoning

ROOT = Path(__file__).resolve().parents[1]


class RequestImageBudgetTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'runtime/temp').mkdir(parents=True, exist_ok=True)
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp')))
        self.enterContext(patch.object(llm, '_IMAGE_CACHE', self.folder / 'cache'))
        self.network = self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('No network')))
        self.enterContext(patch('console.config._parse', side_effect=AssertionError('No credentials')))

    def picture(self, name, seed=42, size=(1024, 1024)):
        path = self.folder / name
        Image.frombytes('RGB', size, random.Random(seed).randbytes(size[0] * size[1] * 3)).save(path, quality=95)
        return path

    def part(self, path):
        mime = 'image/png' if path.suffix == '.png' else 'image/jpeg'
        return {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,' + base64.b64encode(path.read_bytes()).decode()}}

    def body(self, parts):
        return {'model': 'offline', 'max_tokens': 8000,
                'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': '完整保留中文标签'}] + parts}]}

    def budget(self, body):
        try:
            return llm._request_data(body)
        except llm.LLMError as exc:
            self.fail(f'Adaptive compression should fit this request: {exc}')

    def test_many_detailed_images_fit_after_old_1024_pass_would_fail(self):
        paths = [self.picture(f'{i}.jpg', seed=i) for i in range(4)]
        original_files = [p.read_bytes() for p in paths]
        body = self.body([self.part(p) for p in paths])
        before = copy.deepcopy(body)
        old = copy.deepcopy(body)
        for field, key in llm._request_images(old):
            field[key] = 'data:image/jpeg;base64,' + llm._smaller_inline_image(field[key].split(',', 1)[1])
        self.assertGreater(len(json.dumps(old, ensure_ascii=False).encode()), llm._REQUEST_LIMIT)
        payload, count = self.budget(body)
        self.assertLessEqual(len(payload), llm._REQUEST_LIMIT)
        self.assertEqual(count, 4)
        images = llm._request_images(json.loads(payload))
        self.assertEqual(len(images), 4)
        for field, key in images:
            image = Image.open(io.BytesIO(base64.b64decode(field[key].split(',', 1)[1])))
            self.assertGreaterEqual(max(image.size), 640)
            self.assertLessEqual(max(image.size), 1024)
        self.assertIn('完整保留中文标签', payload.decode())
        self.assertEqual(body, before)
        self.assertEqual([p.read_bytes() for p in paths], original_files)

    def test_slight_overflow_keeps_resolution_and_stops_before_small_image(self):
        large = self.part(self.picture('large.jpg', size=(1536, 1024)))
        small_path = self.folder / 'small.png'
        Image.new('RGB', (64, 48), 'blue').save(small_path)
        small = self.part(small_path)
        body = self.body([small, large])
        limit = len(json.dumps(body, ensure_ascii=False).encode()) - 1000
        with patch.object(llm, '_REQUEST_LIMIT', limit):
            payload, _ = self.budget(body)
        parts = json.loads(payload)['messages'][0]['content']
        self.assertEqual(parts[1], small)
        image = Image.open(io.BytesIO(base64.b64decode(parts[2]['image_url']['url'].split(',', 1)[1])))
        self.assertEqual(image.size, (1536, 1024))

    def test_under_budget_is_byte_identical_and_does_not_reencode(self):
        body = self.body([{'type': 'image_url', 'image_url': {'url': 'https://example.invalid/image.jpg'}}])
        with patch.object(llm, '_smaller_inline_image', side_effect=AssertionError('No reencoding')):
            payload, count = llm._request_data(body)
        self.assertEqual(payload, json.dumps(body, ensure_ascii=False).encode())
        self.assertEqual(count, 1)

    def test_gemini_uses_same_budget_and_updates_png_mime(self):
        path = self.picture('source.png')
        encoded = base64.b64encode(path.read_bytes()).decode()
        body = {'contents': [{'parts': [{'inline_data': {'mime_type': 'image/png', 'data': encoded}} for _ in range(4)]}]}
        original = copy.deepcopy(body)
        payload, count = self.budget(body)
        self.assertEqual(count, 4)
        self.assertLessEqual(len(payload), llm._REQUEST_LIMIT)
        for field, _ in llm._request_images(json.loads(payload)):
            self.assertEqual(field['mime_type'], 'image/jpeg')
            self.assertTrue(base64.b64decode(field['data']).startswith(b'\xff\xd8'))
        self.assertEqual(body, original)

    def test_impossible_request_reports_local_limit_before_network(self):
        path = self.picture('small.jpg', size=(64, 48))
        body = self.body([self.part(path)])
        with patch.object(llm, '_REQUEST_LIMIT', 200):
            with self.assertRaises(llm.LLMError) as error:
                common.post('https://offline.invalid', body, 'unused', 1, 'jimmy')
        detail = error.exception.error_detail
        self.assertEqual(detail['error_type'], 'local_request_limit')
        self.assertEqual(detail['image_count'], 1)
        self.assertIsNone(detail['http_status'])
        self.assertGreater(detail['request_bytes'], 200)
        self.assertIn('本地', str(error.exception))
        self.assertIn('未发送', str(error.exception))
        self.assertNotIn('模型渠道的请求上限', str(error.exception))
        self.network.assert_not_called()

    def test_checked_local_limit_is_not_mislabeled_as_model_validation(self):
        error = CheckedLLMError([dict(provider='openai', model='offline', max_tokens=8000,
            reason='本地请求超过安全上限，未发送到模型', kind='too_large',
            error_detail={'error_type': 'local_request_limit', 'http_status': None})])
        self.assertIn('未发送', str(error))
        self.assertNotIn('模型校验', str(error))
        self.assertNotIn('8000', str(error))
        self.assertNotIn('三方渠道重试', str(error))
        self.assertFalse(error.retryable_elsewhere)

    def test_remote_error_keeps_http_diagnostics(self):
        error = CheckedLLMError([dict(provider='jimmy', model='offline', max_tokens=8000,
            reason='渠道返回 HTTP 413', kind='too_large',
            error_detail={'error_type': 'payload_too_large', 'http_status': 413})])
        self.assertIn('HTTP 413', str(error))
        self.assertNotIn('未发送', str(error))
        self.assertEqual(error.error_detail['http_status'], 413)

    def test_official_reasoning_does_not_add_switch_channel_hint_to_local_error(self):
        failure = llm.LLMError('本地请求超限，未发送到模型', kind='too_large',
                               error_detail={'error_type': 'local_request_limit', 'http_status': None})
        def ask(*args, **kwargs):
            return reasoning.chat('openai', [], model='offline')
        with patch.object(llm, '_chat', side_effect=failure), patch.object(llm, 'chat_json', side_effect=ask):
            with self.assertRaises(CheckedLLMError) as error:
                reasoning.chat_json_checked('openai', [], model='offline')
        self.assertNotIn('三方渠道重试', str(error.exception))
        self.assertFalse(error.exception.retryable_elsewhere)
        self.assertEqual(len(error.exception.attempts), 1)

    def test_quality_cache_is_separate_and_second_request_reuses_encodings(self):
        path = self.picture('detail.jpg')
        original = path.read_bytes()
        high = llm._prepare_image(path, 1024, force=True, quality=3)
        lower = llm._prepare_image(path, 1024, force=True, quality=7)
        self.assertNotEqual(high, lower)
        self.assertLess(lower.stat().st_size, high.stat().st_size)
        body = self.body([self.part(path)] * 4)
        first, _ = self.budget(body)
        with patch.object(llm.media, '_run', side_effect=AssertionError('Should reuse cached encodings')):
            second, _ = self.budget(body)
        self.assertEqual(first, second)
        self.assertEqual(path.read_bytes(), original)

    def test_detail_floor_rejects_without_dropping_images_or_cascading_loss(self):
        path = self.picture('fine-detail.jpg')
        part = self.part(path)
        original = part['image_url']['url'].split(',', 1)[1]
        body = self.body([part] * 4)
        with patch.object(llm, '_REQUEST_LIMIT', 100_000), \
                patch.object(llm, '_smaller_inline_image', wraps=llm._smaller_inline_image) as compress:
            with self.assertRaises(llm.LLMError) as error:
                llm._request_data(body)
        self.assertEqual(error.exception.error_detail['image_count'], 4)
        self.assertTrue(all(call.args[0] == original for call in compress.call_args_list))
        sizes = []
        for cached in (self.folder / 'cache').glob('*.jpg'):
            with Image.open(cached) as image:
                sizes.append(max(image.size))
        self.assertEqual(min(sizes), 640)
        self.assertEqual(max(sizes), 1024)
        self.network.assert_not_called()

    def test_transport_sends_only_final_budgeted_bytes(self):
        path = self.picture('transport.jpg')
        body = self.body([self.part(path)] * 4)
        before = copy.deepcopy(body)
        self.network.side_effect = lambda *a, **kw: io.BytesIO(b'{"choices": [{"message": {"content": "{}"}}]}')
        with patch.object(llm, '_ssl_context', return_value=None):
            common.post('https://offline.invalid', body, 'unused', 1, 'jimmy')
        self.network.assert_called_once()
        payload = self.network.call_args.args[0].data
        self.assertLessEqual(len(payload), llm._REQUEST_LIMIT)
        self.assertEqual(len(llm._request_images(json.loads(payload))), 4)
        self.assertEqual(body, before)

    def test_mixed_video_keeps_video_bytes_and_reports_local_overflow(self):
        path = self.picture('frame.jpg', size=(64, 48))
        body = {'contents': [{'parts': [
            {'inline_data': {'mime_type': 'video/mp4', 'data': 'A' * 1000}},
            {'inline_data': {'mime_type': 'image/jpeg', 'data': base64.b64encode(path.read_bytes()).decode()}}]}]}
        before = copy.deepcopy(body)
        with patch.object(llm, '_REQUEST_LIMIT', 200):
            with self.assertRaises(llm.LLMError) as error:
                llm._request_data(body)
        self.assertEqual(error.exception.error_detail['error_type'], 'local_request_limit')
        self.assertEqual(body, before)

    def test_pure_video_still_bypasses_image_limit(self):
        body = {'contents': [{'parts': [{'inline_data': {'mime_type': 'video/mp4', 'data': 'A' * 1000}}]}]}
        with patch.object(llm, '_REQUEST_LIMIT', 200):
            payload, count = llm._request_data(body)
        self.assertEqual(payload, json.dumps(body).encode())
        self.assertEqual(count, 0)

    def test_text_only_overflow_does_not_blame_images(self):
        with patch.object(llm, '_REQUEST_LIMIT', 200):
            with self.assertRaises(llm.LLMError) as error:
                llm._request_data(self.body([{'type': 'text', 'text': '文字' * 1000}]))
        self.assertIn('本地', str(error.exception))
        self.assertNotIn('图片太多', str(error.exception))


if __name__ == '__main__':
    from tools.run_tests_offline import audit
    import sys
    sys.addaudithook(audit)
    unittest.main(verbosity=2)
