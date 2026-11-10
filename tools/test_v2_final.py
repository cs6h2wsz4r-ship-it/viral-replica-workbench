"""V2 final preview: synthetic local clips only, no model or network requests."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from console import config, media, store

ROOT = Path(__file__).resolve().parents[1]


class FinalV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.temp))
        self.enterContext(patch.object(config, 'load', return_value=dict(config.DEFAULTS)))
        self.enterContext(patch.dict(os.environ, {'PATH': str(ROOT / 'runtime/ffmpeg') + os.pathsep + os.environ['PATH']}))
        self.pid = store.create('V2 final synthetic')['id']
        self.directory = store.project_dir(self.pid)

    def test_missing_selection_exact_message_and_no_implicit_latest(self):
        from console import final_preview
        data = store.update(self.pid, lambda d: d.update(
            segments_plan={'format_version': 2, 'segments': [{'id': 'd01'}, {'id': 'd02'}]},
            director={'format_version': 3, 'segments': [{'id': 'd01'}, {'id': 'd02'}]},
            runs={'d01': [{'rev': 'r1', 'state': 'completed', 'result': 'some.mp4'}]}))
        self.assertEqual(final_preview.view(data, self.directory)['warnings'], [
            '第 1 段还没有选定的结果，回第 04 步（对照和修改），选中这一段，在“视频”里生成或选定一版。',
            '第 2 段还没有选定的结果，回第 04 步（对照和修改），选中这一段，在“视频”里生成或选定一版。'])
        with self.assertRaisesRegex(ValueError, '第 1 段还没有选定的结果'):
            final_preview.generate(self.pid)

    def test_concatenate_selected_in_plan_order_without_trimming(self):
        from console import final_preview
        inputs = []
        for index, duration in enumerate((.8, 1.2), 1):
            path = self.directory / f'clips/part{index}.mp4'
            media._run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
                        f'color=c={"red" if index == 1 else "blue"}:s=90x160:r=30:d={duration}',
                        '-f', 'lavfi', '-i', f'sine=frequency={index * 440}:duration={duration}',
                        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                        '-c:a', 'aac', '-shortest', str(path)])
            inputs.append(path)
        rows = [{'id': f'd0{i}', 'selected_run': 'r2', 'source_seconds': .1} for i in (1, 2)]
        store.update(self.pid, lambda d: d.update(
            segments_plan={'format_version': 2, 'segments': [{'id': 'd01'}, {'id': 'd02'}]},
            director={'format_version': 3, 'segments': list(reversed(rows))},
            runs={f'd0{i}': [{'rev': 'r2', 'state': 'completed', 'result': f'clips/part{i}.mp4'},
                             {'rev': 'r3', 'state': 'completed', 'result': 'unselected.mp4'}] for i in (1, 2)}))
        result = final_preview.generate(self.pid)
        info = result['final_preview']
        output = self.directory / info['file']
        self.assertRegex(info['file'], r'^final/preview-.*\.mp4$')
        self.assertLessEqual(abs(info['duration'] - sum(media.probe(p)['duration'] for p in inputs)), .1)
        self.assertEqual(media.probe(output)['audio_stream_count'], 1)
        first = media.small_frame(output, .2, 2, 2)
        last = media.small_frame(output, 1.5, 2, 2)
        self.assertGreater(first[0], first[2] + 100)
        self.assertGreater(last[2], last[0] + 100)
        self.assertEqual(info['segments'], [{'id': 'd01', 'rev': 'r2'}, {'id': 'd02', 'rev': 'r2'}])

    def test_replanned_boundaries_and_legacy_prompts_do_not_reuse_old_selection(self):
        from console import final_preview
        (self.directory / 'clips/d01.mp4').write_bytes(b'synthetic selected file')
        source = {'id': 'd01', 'cells': ['c01', 'c02'], 'source_start': 0, 'source_end': 4}
        data = {'segments_plan': {'format_version': 2, 'segments': [source]},
                'director': {'format_version': 3, 'segments': [{**source, 'selected_run': 'r1'}]},
                'runs': {'d01': [{'rev': 'r1', 'state': 'completed', 'result': 'clips/d01.mp4'}]},
                'final_preview': {'file': 'final/old.mp4', 'segments': [{'id': 'd01', 'rev': 'r1'}]}}
        self.assertTrue(final_preview.view(data, self.directory)['ready'])
        data['segments_plan']['segments'][0] = {**source, 'cells': ['c01'], 'source_end': 2}
        state = final_preview.view(data, self.directory)
        self.assertFalse(state['ready']); self.assertTrue(state['stale'])
        self.assertIn('第 1 段还没有选定的结果', state['warnings'][0])
        data['segments_plan']['segments'][0] = source
        del data['director']['format_version']
        self.assertFalse(final_preview.view(data, self.directory)['ready'])


if __name__ == '__main__':
    unittest.main()
