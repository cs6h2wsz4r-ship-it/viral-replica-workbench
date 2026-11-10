"""Official image channel regressions; every configuration/transport is mocked."""
import base64
import hashlib
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import config, jimmy_bridge as bridge, jobs, llm, store


class Round20ImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.tmp))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('禁止配置读取')))
        self.net = self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('必须 mock 网络')))
        self.cli = self.enterContext(patch.object(bridge, '_call', side_effect=AssertionError('禁止三方调用')))
        self.enterContext(patch.object(jobs, 'start', side_effect=lambda pid, key, fn, label: fn()))
        self.pid = store.create('official image offline')['id']
        self.directory = store.project_dir(self.pid)
        assets = []
        for name in ['person', 'product', 'anchor', 'frame']:
            path = self.directory / 'assets' / (name + '.png')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('offline-' + name).encode())
            assets.append({'id':name, 'type':'image', 'file':'assets/' + path.name, 'label':name,
                           'sha256':store.sha256_file(path)})
        store.update(self.pid, lambda d: d.update(assets=assets, replacements={
            'characters':[{'source_id':'C1','action':'replace','asset_id':'person'}],
            'product':{'action':'replace','asset_id':'product'}}))
        self.values = {**config.DEFAULTS, 'OPENAI_API_KEY':'fake-offline-key',
                       'OPENAI_BASE_URL':'https://offline.invalid/v1', 'OPENAI_IMAGE_MODEL':'gpt-image-2'}

    def preview(self, **kwargs):
        return bridge.image_preview(self.pid, 'Draw a camera frame', ['person','product','frame'],
                                    '9:16', 'gpt-image-2', '2k', 'camera:C1:S1', **kwargs)

    def response(self):
        return io.BytesIO(json.dumps({'data':[{'b64_json':base64.b64encode(b'rendered-offline').decode()}],
                                     'usage':{'total_tokens':123}}).encode())

    def test_official_preview_is_local_and_hash_bound(self):
        rec = self.preview(channel='official')
        self.assertEqual(rec['channel'], 'official')
        self.assertEqual(rec['preview']['size'], '1024x1536')
        self.assertEqual(rec['preview']['quality'], 'auto')
        self.assertEqual([r['sha256'] for r in rec['preview']['images']],
                         [store.sha256_file(self.directory / f'assets/{name}.png') for name in ['person','product','frame']])
        self.assertEqual(rec['preview_sha256'], store.sha256_file(self.directory / rec['run_dir'] / 'image-job.preview.json'))
        self.net.assert_not_called(); self.cli.assert_not_called()

    def test_confirm_multipart_order_result_usage_and_selection(self):
        rec = self.preview(channel='official')
        with patch.object(config, 'load', return_value=self.values), patch('urllib.request.urlopen', return_value=self.response()) as post:
            bridge.image_confirm(self.pid, rec['id'])
        request = post.call_args.args[0]
        self.assertEqual(request.full_url, 'https://offline.invalid/v1/images/edits')
        self.assertEqual(request.method, 'POST')
        self.assertEqual(post.call_args.kwargs['timeout'], 300)
        body = request.data
        self.assertEqual(body.count(b'name="image[]"'), 3)
        self.assertLess(body.index(b'offline-person'), body.index(b'offline-product'))
        self.assertLess(body.index(b'offline-product'), body.index(b'offline-frame'))
        for value in [b'name="size"\r\n\r\n1024x1536', b'name="quality"\r\n\r\nauto', b'name="n"\r\n\r\n1']:
            self.assertIn(value, body)
        data = store.load(self.pid); done = data['images'][rec['id']]
        self.assertEqual(done['state'], 'completed')
        self.assertEqual(done['output_sha256'], hashlib.sha256(b'rendered-offline').hexdigest())
        self.assertEqual(done['usage']['total_tokens'], 123)
        self.assertEqual((self.directory / rec['run_dir'] / 'result.png').read_bytes(), b'rendered-offline')
        self.assertEqual(data['replacements']['characters'][0]['camera_assets']['S1'], rec['id'])
        self.cli.assert_not_called()

    def test_failed_official_cannot_resume_remote_or_reconfirm(self):
        rec = self.preview(channel='official')
        error = urllib.error.HTTPError('https://offline.invalid', 401, 'unauthorized', {}, io.BytesIO(b'{"error":{}}'))
        with patch.object(config, 'load', return_value=self.values), patch('urllib.request.urlopen', side_effect=error) as post:
            with self.assertRaisesRegex(Exception, '官方 key 无效或没有权限'):
                bridge.image_confirm(self.pid, rec['id'])
            self.assertEqual(post.call_count, 1)
        failed = store.load(self.pid)['images'][rec['id']]
        self.assertEqual(failed['state'], 'failed')
        self.assertIn('改用三方渠道重试', failed['error'])
        with self.assertRaisesRegex(bridge.BridgeError, '官方生图没有可恢复的远端任务'):
            bridge.image_resume(self.pid, rec['id'])
        with self.assertRaises(bridge.BridgeError): bridge.image_confirm(self.pid, rec['id'])
        self.net.assert_not_called(); self.cli.assert_not_called()

    def test_resume_local_after_failed_worker_never_posts(self):
        rec = self.preview(channel='official')
        output = self.directory / rec['run_dir'] / 'result.png'; output.write_bytes(b'local-output')
        store.update(self.pid, lambda d:d['images'][rec['id']].update(state='failed', output_sha256=store.sha256_file(output)))
        bridge.image_resume(self.pid, rec['id'])
        self.assertEqual(store.load(self.pid)['images'][rec['id']]['state'], 'completed')
        self.net.assert_not_called(); self.cli.assert_not_called()

    def test_changed_reference_or_preview_blocks_paid_submit(self):
        for changed in ['reference', 'preview', 'binding']:
            with self.subTest(changed=changed):
                rec = self.preview(channel='official')
                if changed == 'reference':
                    (self.directory / 'assets/frame.png').write_bytes(b'changed-frame')
                elif changed == 'preview':
                    (self.directory / rec['run_dir'] / 'image-job.preview.json').write_text('{}')
                else:
                    store.update(self.pid, lambda d:d['replacements']['product'].update(note='changed'))
                with self.assertRaisesRegex(bridge.BridgeError, '过期|校验|哈希'):
                    bridge.image_confirm(self.pid, rec['id'])
        self.net.assert_not_called()

    def test_legacy_default_prepare_payload_unchanged(self):
        payloads = []
        def prepare(args, cwd):
            self.assertEqual(args[1], 'prepare')
            payload = json.loads(Path(args[3]).read_text()); payloads.append(payload)
            run = Path(args[5]); run.mkdir(parents=True)
            store.atomic_write_json(run / 'image-job.preview.json', payload)
            return 'prepared'
        with patch.object(bridge, '_call', side_effect=prepare):
            rec = self.preview()
        self.assertEqual(rec['channel'], 'third_party')
        self.assertNotIn('channel', payloads[0]); self.assertNotIn('size', payloads[0])
        self.assertTrue(all(set(ref) == {'source','path'} for ref in payloads[0]['images']))

    def test_legacy_no_channel_confirm_still_uses_third_party(self):
        run = self.directory / 'image-runs/legacy'; run.mkdir(parents=True)
        store.update(self.pid, lambda d:d['images'].update(legacy={'id':'legacy','state':'pending_confirmation','run_dir':'image-runs/legacy'}))
        with patch.object(config, 'jimmy_configured', return_value=True), patch.object(config, 'jimmy_env_file', return_value=self.tmp/'mock.env'), \
             patch.object(bridge, '_image_submit') as submit, patch.object(bridge, '_image_poll') as poll:
            bridge.image_confirm(self.pid, 'legacy')
        submit.assert_called_once(); poll.assert_called_once()

    def test_official_http_retry_quota_and_url_result(self):
        from console import openai_image
        for status, payload, count in [(429, {'error':{'code':'insufficient_quota'}}, 1),
                                        (500, {'error':{'message':'internal error'}}, 4)]:
            with self.subTest(status=status):
                errors = [urllib.error.HTTPError('https://offline.invalid', status, 'error', {}, io.BytesIO(json.dumps(payload).encode())) for _ in range(count)]
                rec = self.preview(channel='official')
                with patch.object(config, 'load', return_value=self.values), patch('urllib.request.urlopen', side_effect=errors) as post, patch.object(openai_image.time, 'sleep') as sleep:
                    with self.assertRaisesRegex(Exception, '官方额度不足' if status == 429 else '改用三方渠道重试'):
                        bridge.image_confirm(self.pid, rec['id'])
                self.assertEqual(post.call_count, count)
                self.assertEqual([c.args[0] for c in sleep.call_args_list], [] if count == 1 else [5,15,30])
        rec = self.preview(channel='official')
        responses = [io.BytesIO(b'{"data":[{"url":"https://offline.invalid/result.png"}]}'), io.BytesIO(b'downloaded-image')]
        with patch.object(config, 'load', return_value=self.values), patch('urllib.request.urlopen', side_effect=responses) as post:
            bridge.image_confirm(self.pid, rec['id'])
        self.assertEqual(post.call_args_list[1].args[0].method, 'GET')
        self.assertNotIn('Authorization', dict(post.call_args_list[1].args[0].headers))

    def test_third_party_failure_is_readable_and_retains_sanitized_detail(self):
        run = self.directory / 'image-runs/legacy'; run.mkdir(parents=True)
        store.update(self.pid, lambda d:d['images'].update(legacy={'id':'legacy','state':'pending_confirmation','run_dir':'image-runs/legacy'}))
        with patch.object(config, 'jimmy_configured', return_value=True), patch.object(config, 'jimmy_env_file', return_value=self.tmp/'mock.env'), \
             patch.object(bridge, '_image_submit', side_effect=bridge.BridgeError('Jimmy image task failed: unavailable')):
            with self.assertRaisesRegex(bridge.BridgeError, '三方渠道'):
                bridge.image_confirm(self.pid, 'legacy')
        rec = store.load(self.pid)['images']['legacy']
        self.assertEqual(rec['error'], '三方渠道任务失败，请检查配置或稍后重试')
        self.assertIn('third-party image task failed: unavailable', rec['previous_errors'])
        self.assertNotIn('Jimmy', json.dumps(rec))

    def test_official_anchor_reference_order(self):
        def prepare(d):
            d['breakdown'] = {'scene':[{'id':'S1','timeRanges':[[0,5]]},{'id':'S2','timeRanges':[[5,10]]}]}
            d['replacements']['characters'][0]['camera_assets'] = {'S2':'anchor'}
        store.update(self.pid, prepare)
        rec = bridge.image_preview(self.pid, 'frame', ['person','product','anchor','frame'],
                                   '9:16', 'gpt-image-2', '2k', 'camera:C1:S1', channel='official')
        with patch.object(config, 'load', return_value=self.values), patch('urllib.request.urlopen', return_value=self.response()) as post:
            bridge.image_confirm(self.pid, rec['id'])
        body = post.call_args.args[0].data
        indexes = [body.index(('offline-' + name).encode()) for name in ['person','product','anchor','frame']]
        self.assertEqual(indexes, sorted(indexes))
        self.assertEqual(body.count(b'name="image[]"'), 4)

    def test_confirm_twice_does_not_resubmit(self):
        rec = self.preview(channel='official')
        with patch.object(config, 'load', return_value=self.values), patch('urllib.request.urlopen', return_value=self.response()) as post:
            bridge.image_confirm(self.pid, rec['id'])
            with self.assertRaises(bridge.BridgeError): bridge.image_confirm(self.pid, rec['id'])
        self.assertEqual(post.call_count, 1)

    def test_network_timeout_does_not_retry_unknown_charge(self):
        rec = self.preview(channel='official')
        with patch.object(config, 'load', return_value=self.values), patch('urllib.request.urlopen', side_effect=TimeoutError()) as post:
            with self.assertRaisesRegex(Exception, '可能已计费'):
                bridge.image_confirm(self.pid, rec['id'])
        self.assertEqual(post.call_count, 1)
        self.assertEqual(store.load(self.pid)['images'][rec['id']]['state'], 'failed')

    def test_official_ratio_nearest(self):
        from console import openai_image
        self.assertEqual(openai_image.image_size('16:9'), '1536x1024')
        self.assertEqual(openai_image.image_size('1:1'), '1024x1024')
        self.assertEqual(openai_image.image_size('4:5'), '1024x1536')
        self.assertEqual(openai_image.image_size('4:3'), '1536x1024')


if __name__ == '__main__': unittest.main()
