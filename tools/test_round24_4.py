"""Round 24.4 screenshot timestamp provenance, with local temp projects and mock I/O."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import config, gateway, llm, media, pipeline, store


class Round24_4Tests(unittest.TestCase):
    def setUp(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', folder))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('禁止访问密钥文件')))
        self.enterContext(patch.object(config, 'load', return_value=dict(config.DEFAULTS)))
        self.enterContext(patch.object(config, 'provider_settings', return_value={'model': 'offline'}))
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('禁止真实请求')))
        self.pid = store.create('24.4 截图时间离线回归')['id']
        self.directory = store.project_dir(self.pid)
        store.update(self.pid, lambda d: d.update(source={'file': 'source/source.mp4'}))

    def test_analysis_persists_exact_extraction_time_for_every_shot(self):
        shots = [{'index': 1, 'start': 0.0, 'end': 10.052},
                 {'index': 2, 'start': 10.052, 'end': 12.0}]
        extracted = []

        def frame(src, at, out):
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b'offline screenshot')
            extracted.append((at, store.rel(self.pid, out)))
            return out

        with patch.object(media, 'probe', return_value={'duration': 12, 'audio_stream_count': 0}), \
                patch.object(media, 'hypit_boundaries', return_value=[]), \
                patch.object(media, 'merge_boundaries', return_value=copy.deepcopy(shots)), \
                patch.object(media, 'extract_frame', side_effect=frame), \
                patch.object(pipeline, '_grids', return_value=[]), \
                patch.object(gateway, 'reason', return_value=({}, {})) as reason, \
                patch.object(pipeline, '_clean_passages', side_effect=RuntimeError('offline stop after persisted shots')):
            with self.assertRaisesRegex(RuntimeError, 'offline stop after persisted shots'):
                pipeline._run_analysis(self.pid, 'deepseek', skip_asr=True)

        saved = store.load(self.pid)['analysis']['shots']
        self.assertEqual(len(saved), 2)
        self.assertEqual([s.get('frame_at') for s in saved], [5.026, 11.026])
        self.assertEqual([(s['frame_at'], s['frame']) for s in saved], extracted)
        self.assertEqual([(s['start'], s['end']) for s in saved], [(0.0, 10.052), (10.052, 12.0)])
        reason.assert_called_once()

    def test_draft_extracts_candidate_at_exact_time_and_does_not_reuse_rounded_frame(self):
        for standalone in (False, True):
            with self.subTest(standalone=standalone):
                directory = self.directory
                for file in ('source/source.mp4', 'assets/person.jpg', 'assets/rounded.jpg', 'analysis/frames/shot-001.jpg'):
                    dest = directory / file
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(b'offline media')
                analysis = ({'frames': [{'at': 5.026, 'file': 'analysis/frames/shot-001.jpg'}]} if standalone else
                            {'shots': [{'start': 0, 'end': 10.052, 'frame': 'analysis/frames/shot-001.jpg'}]})
                store.update(self.pid, lambda d: d.update(
                    source={'file': 'source/source.mp4', 'probe': {}}, analysis=analysis,
                    breakdown={'characters': [{'id': 'C1'}], 'scene': [{'id': 'S1', 'timeRanges': [[0, 10.052]]}]},
                    replacements={'characters': [{'source_id': 'C1', 'action': 'replace', 'asset_id': 'person'}],
                                  'product': {'action': 'keep'}},
                    assets=[{'id': 'person', 'type': 'image', 'file': 'assets/person.jpg'},
                            {'id': 'rounded', 'type': 'image', 'file': 'assets/rounded.jpg', 'frame_at': 5.03,
                             'frame_character': 'C1', 'camera_scene': 'S1', 'internal': True}]))
                extracted = []

                def frame(src, at, out, **kwargs):
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(f'offline frame at {at}'.encode())
                    extracted.append(at)
                    return out

                with patch.object(media, 'extract_frame', side_effect=frame), \
                        patch.object(pipeline, '_clean_camera_frame', side_effect=lambda pid, data, asset, at: (asset, '')), \
                        patch.object(llm, 'image_part', return_value=[]), \
                        patch.object(gateway, 'reason', return_value=({'person': 'p', 'shot': 's', 'setting': 'room'}, {})):
                    first = pipeline.draft_camera_image(self.pid, 'deepseek', 'C1', at=5.026, scene_id='S1')
                    again = pipeline.draft_camera_image(self.pid, 'deepseek', 'C1', at=5.026, scene_id='S1')
                    rounded = pipeline.draft_camera_image(self.pid, 'deepseek', 'C1', at=5.03, scene_id='S1')
                self.assertEqual(extracted, [5.026])
                self.assertEqual(first['frame_at'], 5.026)
                self.assertNotEqual(first['frame_asset_id'], 'rounded')
                self.assertEqual(again['frame_asset_id'], first['frame_asset_id'])
                self.assertEqual(rounded['frame_asset_id'], 'rounded')
                asset = next(a for a in store.load(self.pid)['assets'] if a['id'] == first['frame_asset_id'])
                self.assertEqual(asset['frame_at'], 5.026)
                self.assertTrue(asset['file'].endswith('frame-C1-5.026.jpg'))


if __name__ == '__main__':
    unittest.main()
