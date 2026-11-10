"""Round 22: real local source copies, mocked configuration and transports only."""
import base64
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import subprocess
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import config, gateway, jimmy_bridge, jobs, llm, media, pipeline, store
from console import replacement_guard as guard
from tools.project_fixture import copy_media, fixture_path, load_project

NO_TEXT = ('Do not reproduce any captions, subtitles, stickers, watermarks, logos or other on-screen text '
           'from the reference frame; the image contains no readable text except what is printed on the real product itself.')


class Round22Tests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.temp))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('禁止读取配置')))
        self.enterContext(patch.object(config, 'load', return_value={**config.DEFAULTS, 'OPENAI_API_KEY': 'offline'}))
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('必须 mock 网络')))
        self.enterContext(patch.dict(os.environ, {'PATH': str(ROOT / 'runtime/ffmpeg') + os.pathsep + os.environ.get('PATH', '')}))
        self.reason = self.enterContext(patch.object(llm, 'chat_json', return_value=(
            {'person': 'reference image 1', 'shot': 'front view', 'setting': 'parking lot'}, {})))
        self.pid = store.create('round22')['id']
        self.directory = store.project_dir(self.pid)
        fixture_id = 'p1029100000-r18v'
        shutil.copyfile(fixture_path(fixture_id), self.directory / 'fixture.json')
        self.data = load_project(fixture_id)
        self.data.update(id=self.pid, images={}, usage=[])
        # JSON is frozen; only recorded local media are read and checksum-verified.
        copy_media(fixture_id, self.directory,
                   [self.data['source']['file']] + [a['file'] for a in self.data['assets']])
        self.data['assets'] = [a for a in self.data['assets'] if not Path(a['file']).name.startswith('frame-')]
        store.update(self.pid, lambda d: d.update(self.data))

    def draft(self, at=3.36):
        return pipeline.draft_camera_image(self.pid, 'openai', 'C1', at=at, scene_id='S1')

    def preview(self, draft, channel='official'):
        return gateway.image_preview('camera_image', self.pid, draft['prompt'], draft['image_ids'],
            draft['ratio'], 'gpt-image-2', '2k', draft['for_task'],
            draft_binding=draft['camera_binding'], override=channel + ':gpt-image-2')

    def test_real_frame_clean_residue_reuse_and_preview(self):
        draft = self.draft()
        assets = {a['id']: a for a in store.load(self.pid)['assets']}
        self.assertNotEqual(draft['image_ids'][-1], draft['frame_asset_id'])
        clean = assets[draft['image_ids'][-1]]
        original = assets[draft['frame_asset_id']]
        self.assertTrue(clean['internal'])
        self.assertEqual(clean['derived_from'], original['id'])
        box = clean['masks'][0]['bbox']
        self.assertEqual(len(clean['masks']), 1)
        self.assertEqual(box, [0, .53, .26, .69], '3.36 秒只处理 2.78–5.20 秒字幕自己的框')
        self.assertIn(NO_TEXT, draft['prompt'])
        self.assertIn('参考帧里的字幕、贴纸、水印一律不要画出来。', draft['prompt'])
        self.assertEqual(clean['caption_residue']['status'], 'checked')
        self.assertEqual(clean['caption_residue']['suspected'], [])
        dirty = media.caption_residue(self.directory / original['file'], self.directory / self.data['source']['file'],
            clean['masks'], self.data['breakdown']['overlays'], self.temp / 'dirty-residue', image_at=3.36)
        self.assertTrue(dirty['suspected'], dirty)
        with patch.object(media, 'delogo_frame', side_effect=AssertionError('同一帧不得重复处理')):
            again = self.draft()
        self.assertEqual(again['image_ids'], draft['image_ids'])
        self.assertEqual(len([a for a in store.load(self.pid)['assets'] if a.get('derived_from') == original['id']]), 1)
        rec = self.preview(draft)
        self.assertEqual(rec['image_ids'][-1], clean['id'])
        self.assertEqual(rec['frame_asset_id'], original['id'])
        self.assertEqual(rec['clean_frame_asset_id'], clean['id'])
        self.assertTrue(rec['caption_overlap_warning'])
        self.assertTrue(rec['preview']['images'][-1]['path'].endswith(clean['file']))
        self.assertFalse(any(r['kind'] == 'image' for r in store.load(self.pid)['usage']))
        evidence = ROOT / 'runtime/temp/round22-evidence'
        evidence.mkdir(exist_ok=True)
        for asset, name in [(original, 'original-3.36.jpg'), (clean, 'clean-3.36.png')]:
            shutil.copyfile(self.directory / asset['file'], evidence / name)
        store.atomic_write_json(evidence / 'checks.json', {'masks': clean['masks'], 'original': dirty,
            'clean': clean['caption_residue'], 'draft': draft})
        store.atomic_write_json(evidence / 'ui-project.json', store.load(self.pid))

    def test_no_overlays_and_inactive_overlays_use_original(self):
        for overlays in ([], [{'kind': 'caption', 'start': 7, 'end': 8, 'bbox': [.1,.6,.3,.7]}]):
            with self.subTest(overlays=overlays):
                store.update(self.pid, lambda d: d['breakdown'].update(overlays=overlays))
                with patch.object(media, 'delogo_frame', side_effect=AssertionError('不应处理')):
                    draft = self.draft()
                self.assertEqual(draft['image_ids'][-1], draft['frame_asset_id'])
                self.assertFalse(any(a.get('derived_from') for a in store.load(self.pid)['assets']))

    def test_equal_front_candidates_prefer_no_captions_then_smaller_area(self):
        data = copy.deepcopy(self.data)
        data['breakdown']['shots'] = [{'start': a, 'end': a+1, 'characters': ['C1'],
            'action': '正面、商品清楚'} for a in (1, 4, 7)]
        data['breakdown']['productMoments'] = []
        data['breakdown']['overlays'] = [
            {'kind': 'caption', 'system': 'large', 'start': 1, 'end': 2, 'bbox': [.1,.5,.9,.7]},
            {'kind': 'caption', 'system': 'small', 'start': 4, 'end': 5, 'bbox': [.1,.5,.3,.6]}]
        scene = {'id': 'S1', 'timeRanges': [[0, 10]]}
        self.assertEqual(pipeline._camera_frame(data, 'C1', scene)[0], 7.5)
        data['breakdown']['shots'].pop()
        self.assertEqual(pipeline._camera_frame(data, 'C1', scene)[0], 4.5)

    def test_free_previews_and_confirmed_official_network(self):
        store.update(self.pid, lambda d: d['breakdown'].update(overlays=[]))
        draft = self.draft()
        official = self.preview(draft)
        def prepare(args, cwd, **kw):
            self.assertEqual(args[1], 'prepare')
            job = json.loads(Path(args[args.index('--job')+1]).read_text())
            run = Path(args[args.index('--run')+1])
            store.atomic_write_json(run / 'image-job.preview.json', job)
            return 'mock prepare'
        with patch.object(jimmy_bridge, '_call', side_effect=prepare):
            self.preview(draft, 'third_party')
        self.assertFalse(any(r['kind'] == 'image' for r in store.load(self.pid)['usage']))
        payload = {'data': [{'b64_json': base64.b64encode(b'fake generated image').decode()}],
                   'usage': {'input_tokens': 5, 'output_tokens': 8}}
        def start(pid, key, fn, label):
            gateway.usage.wrap_worker(fn)()
            return {'state': 'done'}
        def safety(step, messages, **kwargs):
            rows = json.loads(messages[-1]['content'])['tasks']
            return {'tasks': [{**{k: row[k] for k in ('id', 'action', 'story', 'direction')}, 'changes': []}
                              for row in rows]}, {'provider': 'mock', 'model': 'mock', 'attempts': []}
        with patch.object(jobs, 'start', side_effect=start), patch.object(llm, '_ssl_context', return_value=None), \
                patch.object(gateway, 'reason_checked', side_effect=safety) as check, \
                patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(payload).encode())) as request:
            gateway.image_confirm(self.pid, official['id'])
        self.assertEqual(check.call_count, 1, '新图完成自动跟随时执行安全校对')
        self.assertEqual(request.call_count, 1)
        rows = [r for r in store.load(self.pid)['usage'] if r['kind'] == 'image']
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['network_request'])
        self.assertEqual(rows[0]['billing']['amount'], 13)
        # A later local restore must not reuse the completed request's metering.
        with patch('urllib.request.urlopen', side_effect=AssertionError('本地恢复不能联网')):
            gateway.image_resume(self.pid, official['id'])
        rows = [r for r in store.load(self.pid)['usage'] if r['kind'] == 'image']
        self.assertEqual(len(rows), 1)

    def test_confirm_rejected_before_network_is_not_image_usage(self):
        with patch('console.gateway.adapters.third_party.image_confirm', side_effect=ValueError('已过期')):
            with self.assertRaises(gateway.GatewayError): gateway.image_confirm(self.pid, 'missing')
        self.assertFalse(any(r['kind'] == 'image' for r in store.load(self.pid).get('usage', [])))

    def test_third_party_confirm_counts_unmetered_remote_request(self):
        iid = 'mock-third-party'
        run = self.directory / 'image-runs' / iid
        run.mkdir()
        store.update(self.pid, lambda d: d['images'].update({iid: {'id': iid, 'state': 'pending_confirmation',
            'channel': 'third_party', 'run_dir': store.rel(self.pid, run), 'preview': {'model': 'gpt-image-2'}}}))
        commands = []
        def cli(args, cwd, **kw):
            commands.append(args[1])
            if args[1] == 'submit':
                store.atomic_write_json(run / 'image-state.json', {'phase': 'submitted', 'remote_task_id': 'mock-remote'})
                return '{}'
            self.assertEqual(args[1], 'resume')
            output = run / 'result.png'; output.write_bytes(b'mock image')
            return json.dumps({'phase': 'completed', 'output': str(output)})
        with patch.object(config, 'jimmy_configured', return_value=True), \
                patch.object(config, 'jimmy_env_file', return_value=self.temp / 'mock-settings.conf'), \
                patch.object(jimmy_bridge, '_call', side_effect=cli), \
                patch.object(jobs, 'start', side_effect=lambda pid, key, fn, label: gateway.usage.wrap_worker(fn)()):
            gateway.image_confirm(self.pid, iid)
        self.assertEqual(commands, ['submit', 'resume'])
        rows = store.load(self.pid)['usage']
        self.assertEqual(len(rows), 1); self.assertEqual(rows[0]['kind'], 'image')
        self.assertTrue(rows[0]['network_request']); self.assertIsNone(rows[0]['billing']['amount'])

    def test_caption_time_padding_and_overlap_boxes(self):
        data = {'breakdown': {'overlays': [{'kind': 'caption', 'start': 1, 'end': 2, 'bbox': [.1,.5,.3,.6]}],
            'shots': [{'start': 0, 'end': 3, 'characters': ['C1'], 'bbox': [.6,.1,.9,.9]}]}}
        for at, expected in ((.84, False), (.86, True), (2.14, True), (2.16, False)):
            self.assertEqual(bool(guard.frame_caption_masks(data, at)[0]), expected)
        masks = guard.frame_caption_masks(data, 1.5)[0]
        self.assertFalse(guard.frame_caption_overlap(data, 1.5, masks))
        data['breakdown']['shots'][0]['bbox'] = [.2,.1,.7,.9]
        self.assertTrue(guard.frame_caption_overlap(data, 1.5, masks))

    def test_suspected_residue_is_advisory_and_keeps_clean_input(self):
        with patch.object(media, 'caption_residue', return_value={'status': 'checked', 'suspected': [{'score': .02}]}):
            draft = self.draft()
        self.assertTrue(draft['caption_residue']['suspected'])
        self.assertNotEqual(draft['image_ids'][-1], draft['frame_asset_id'])
        rec = self.preview(draft)
        self.assertEqual(rec['state'], 'pending_confirmation')
        self.assertEqual(rec['caption_residue'], draft['caption_residue'])

    def test_residue_timeout_is_advisory(self):
        with patch.object(media, 'caption_residue', side_effect=subprocess.TimeoutExpired('ffmpeg', 120)):
            draft = self.draft()
        self.assertEqual(draft['caption_residue']['status'], 'unavailable')
        self.assertEqual(self.preview(draft)['state'], 'pending_confirmation')

    def test_concurrent_drafts_share_original_and_clean_asset(self):
        extract = media.extract_frame
        def slow_extract(*args, **kwargs):
            time.sleep(.05)
            return extract(*args, **kwargs)
        with patch.object(media, 'extract_frame', side_effect=slow_extract), \
                patch.object(media, 'delogo_frame', wraps=media.delogo_frame) as clean:
            with ThreadPoolExecutor(max_workers=2) as pool:
                drafts = list(pool.map(lambda _: self.draft(), range(2)))
        self.assertEqual(drafts[0]['frame_asset_id'], drafts[1]['frame_asset_id'])
        self.assertEqual(drafts[0]['clean_frame_asset_id'], drafts[1]['clean_frame_asset_id'])
        self.assertEqual(clean.call_count, 1)


if __name__ == '__main__':
    from tools.run_tests_offline import audit
    sys.addaudithook(audit)
    unittest.main()
