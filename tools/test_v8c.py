"""V8c cross-segment mode changes: synthetic projects, no external jobs."""
import copy
import unittest
from unittest.mock import patch

from PIL import Image

from console import config, director, fix_plan, gateway, jimmy_bridge, media
from console import segment_boards, segment_plan, store, storyboard_edits, storyboard_images
from tools import test_director_sb3 as fixtures
from tools import test_v8b as v8b


class V8cModeBindingTests(unittest.TestCase):
    fixture = v8b.V8bVisualBindingTests.fixture
    segment = fixtures.DirectorSB3Tests.segment

    def setUp(self):
        fixtures.DirectorSB3Tests.setUp(self)
        self.fixture()
        self.enterContext(patch.object(gateway, 'reason_checked', side_effect=AssertionError('stub all reasoning')))
        self.enterContext(patch.object(gateway, 'image_confirm', side_effect=AssertionError('no paid images')))
        self.enterContext(patch.object(jimmy_bridge, '_call', side_effect=AssertionError('no engine processes')))
        self.enterContext(patch.object(jimmy_bridge, '_start_generate', side_effect=AssertionError('no video submission')))

    def image_preview(self):
        def preview(step, pid, prompt, ids, ratio, model, resolution, for_task):
            iid = store.new_id('img')
            folder = self.d / 'image-runs' / iid
            folder.mkdir(parents=True)
            record = dict(id=iid, state='pending_confirmation', channel='official',
                          for_task=for_task, run_dir=store.rel(pid, folder))
            store.update(pid, lambda data: data['images'].__setitem__(iid, record))
            return record

        with patch.object(gateway, 'image_preview', side_effect=preview):
            return storyboard_edits.preview(self.pid, 'd2', {})

    def video_preview(self):
        def cut(source, start, length, output, **kwargs):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b'offline source cut')

        def stretch(source, length, duration, output):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b'offline stretched source')
            return dict(indexed_frames=4, decoded_frames=4, fps=1)

        def package(plan, folder):
            review = folder / 'review'
            job = {**plan, 'tasks': [{**task, 'model': 'offline'} for task in plan['tasks']]}
            path = review / 'jimmy-job.preview.json'
            store.atomic_write_json(path, job)
            handoff = dict(state='ready_for_confirmation', job_sha256=store.sha256_file(path))
            store.atomic_write_json(review / 'jimmy-handoff.json', handoff)
            return job, handoff

        with patch.object(media, 'cut_clip', side_effect=cut), \
                patch.object(media, 'stretch_clip', side_effect=stretch), \
                patch.object(media, 'caption_residue', return_value={'status': 'skipped', 'suspected': []}), \
                patch.object(jimmy_bridge, '_prepare_package', side_effect=package):
            return jimmy_bridge.director_preview(self.pid, 'd2')

    def test_other_segment_pending_image_keeps_its_confirmation_binding(self):
        preview = self.image_preview()
        before = store.load(self.pid)
        storyboard_edits.verify_preview(self.pid, preview)

        after = segment_plan.save(self.pid, 'd1', {'mode': 'image'})

        self.assertEqual(after['images'][preview['id']], before['images'][preview['id']])
        self.assertEqual(segment_boards.state_for(after, 'd2'), segment_boards.state_for(before, 'd2'))
        self.assertEqual(director.segment_inputs_sig(after, self.d, 'd2'),
                         director.segment_inputs_sig(before, self.d, 'd2'))
        self.assertNotEqual(director.inputs_sig(after, self.d), director.inputs_sig(before, self.d))
        # The bridge repeats this check at confirmation and before upload retries.
        jimmy_bridge._verify_camera_image_current(self.pid, preview['id'])
        self.assertEqual(store.load(self.pid)['images'][preview['id']]['state'], 'pending_confirmation')

    def test_other_segment_running_image_completes_into_its_own_board(self):
        preview = self.image_preview()
        store.update(self.pid, lambda data: data['images'][preview['id']].update(state='running'))
        after_mode = segment_plan.save(self.pid, 'd1', {'mode': 'image'})
        jimmy_bridge._verify_camera_image_current(self.pid, preview['id'])
        output = self.d / preview['run_dir'] / 'result.png'
        Image.new('RGB', (30, 50), 'blue').save(output)

        def render(directory, folder, prefix, context, image):
            files = {}
            for cell in context['cells']:
                path = folder / f'{prefix}-{cell["id"]}.png'
                Image.new('RGB', (30, 50), 'blue').save(path)
                files[cell['id']] = path
            clean, labeled = folder / f'{prefix}.png', folder / f'{prefix}.jpg'
            Image.new('RGB', (60, 50), 'blue').save(clean)
            Image.new('RGB', (60, 50), 'blue').save(labeled)
            return files, clean, labeled, {}

        with patch.object(storyboard_images, '_render', side_effect=render):
            jimmy_bridge._image_done(self.pid, preview['id'], output)

        after = store.load(self.pid)
        version = segment_boards.current(after, 'd2')
        self.assertEqual(after['images'][preview['id']]['state'], 'completed')
        self.assertEqual(version['image_id'], preview['id'])
        self.assertEqual(set(version['cells']), {'c03', 'c04'})
        self.assertEqual(version['segment_sig'], preview['storyboard_context']['segment_sig'])
        self.assertEqual(len(segment_boards.state_for(after, 'd2')['versions']), 2)
        self.assertEqual(segment_boards.state_for(after, 'd1'), segment_boards.state_for(after_mode, 'd1'))
        self.assertEqual(director.get_segment(after, 'd1'), director.get_segment(after_mode, 'd1'))
        self.assertEqual(segment_plan.get_segment(after, 'd1')['mode'], 'image')

    def test_other_segment_pending_video_remains_confirmable(self):
        preview = self.video_preview()
        store.update(self.pid, lambda data: data['runs'].__setitem__(
            'd1', [dict(rev='r1', kind='director', state='pending_confirmation')]))
        before = store.load(self.pid)
        package = self.d / preview['package'] / 'review' / 'jimmy-job.preview.json'
        frozen_bytes = package.read_bytes()

        after_mode = segment_plan.save(self.pid, 'd1', {'mode': 'image'})

        self.assertEqual(after_mode['runs']['d2'], before['runs']['d2'])
        self.assertEqual(after_mode['runs']['d1'][0]['state'], 'superseded')
        self.assertNotEqual(director.inputs_sig(before, self.d), director.inputs_sig(after_mode, self.d))
        with patch.object(config, 'jimmy_configured', return_value=True), \
                patch.object(jimmy_bridge, '_call', return_value=''), \
                patch.object(jimmy_bridge, '_start_generate') as submit:
            jimmy_bridge.confirm(self.pid, 'd2', preview['rev'], preview['job_sha256'])
        submit.assert_called_once_with(self.pid, 'd2', preview['rev'])
        after = store.load(self.pid)
        self.assertEqual(after['runs']['d2'][0]['state'], 'submitting')
        self.assertEqual(after['runs']['d1'], after_mode['runs']['d1'])
        self.assertEqual(package.with_name('jimmy-job.json').read_bytes(), frozen_bytes)
        self.assertEqual(director.get_segment(after, 'd1'), director.get_segment(after_mode, 'd1'))

    def test_other_segment_running_video_completes_without_rebinding(self):
        preview = self.video_preview()
        store.update(self.pid, lambda data: data['runs']['d2'][0].update(
            state='running', run_dir='jimmy-runs/offline-d2'))
        after_mode = segment_plan.save(self.pid, 'd1', {'mode': 'image'})
        self.assertEqual(after_mode['runs']['d2'][0]['state'], 'running')
        result = self.d / 'clips' / 'offline-d2-result.mp4'
        result.write_bytes(b'offline generated video')
        child = dict(phase='completed', local_path=str(result), jimmy_task_id='offline-d2')

        with patch.object(jimmy_bridge, '_video_child', return_value=child):
            jimmy_bridge._refresh(self.pid, 'd2', preview['rev'])

        after = store.load(self.pid)
        run = after['runs']['d2'][0]
        self.assertEqual((run['state'], run['result']), ('completed', store.rel(self.pid, result)))
        self.assertEqual(run['review_inputs'], preview['review_inputs'])
        self.assertEqual(run['director_sig'], preview['director_sig'])
        self.assertEqual(director.get_segment(after, 'd2')['selected_run'], preview['rev'])
        self.assertEqual(director.get_segment(after, 'd1'), director.get_segment(after_mode, 'd1'))
        self.assertEqual(segment_plan.get_segment(after, 'd1')['mode'], 'image')

    def test_other_segment_fix_plan_reasoning_and_apply_keep_local_binding(self):
        data = store.load(self.pid)
        row = copy.deepcopy(director.get_segment(data, 'd2'))
        result = self.d / 'clips' / 'offline-d2-reviewed.mp4'
        result.write_bytes(b'offline reviewed result')
        run = dict(rev='r1', kind='director', state='completed', result=store.rel(self.pid, result),
                   review_inputs={'segment': row}, review=dict(status='completed', verdict='fix', issues=[
                       dict(start=0, end=1, level='high', what='结尾不稳', fix='prompt', how='结尾保持稳定')]))
        store.update(self.pid, lambda current: current['runs'].__setitem__('d2', [run]))
        answer = dict(redraw=[], prompt=row['prompt'] + '动作结束后保持稳定。', why='稳定结尾')

        def reason(*args, **kwargs):
            segment_plan.save(self.pid, 'd1', {'mode': 'image'})
            return answer, {}

        with patch.object(gateway, 'reason_checked', side_effect=reason) as reasoning, \
                patch.object(gateway.routes, 'resolve', return_value={'model': 'offline'}):
            generated = fix_plan.generate(self.pid, 'd2', 'r1', {'issues': [0], 'note': ''})
        reasoning.assert_called_once()
        plan = generated['runs']['d2'][0]['fix_plan']
        self.assertEqual(plan['status'], 'ready')
        # A second unrelated mode change between reasoning and apply also stays local.
        before_apply = segment_plan.save(self.pid, 'd1', {'mode': 'video'})
        applied = fix_plan.apply(self.pid, 'd2', 'r1', {'plan_id': plan['id']})
        self.assertEqual(applied['runs']['d2'][0]['fix_plan']['status'], 'ready_video')
        self.assertEqual(director.get_segment(applied, 'd2')['prompt'], answer['prompt'])
        self.assertEqual(director.get_segment(applied, 'd1'), director.get_segment(before_apply, 'd1'))
        self.assertEqual(fix_plan.video_binding(applied, 'd2', self.d),
                         {'id': plan['id'], 'rev': 'r1', 'sid': 'd2'})

    def test_director_rewrite_rejects_other_segment_mode_change_during_reasoning(self):
        before = store.load(self.pid)
        previous = copy.deepcopy(director.get_segment(before, 'd2'))
        answer = {key: previous[key] for key in ('id', 'what', 'check', 'prompt')}
        answer['prompt'] += '动作结束后保持稳定。'

        def reason(*args, **kwargs):
            segment_plan.save(self.pid, 'd1', {'mode': 'image'})
            return answer, {}

        with patch.object(gateway, 'reason_checked', side_effect=reason) as reasoning:
            with self.assertRaisesRegex(ValueError, '内容刚刚改过，请重新重写本段'):
                director.save(self.pid, {'action': 'rewrite', 'segment_id': 'd2'})
        reasoning.assert_called_once()
        after = store.load(self.pid)
        self.assertEqual(director.get_segment(after, 'd2'), previous)
        self.assertEqual(segment_plan.get_segment(after, 'd1')['mode'], 'image')
        self.assertIsNone(director.get_segment(after, 'd1')['inputs_sig'])
        self.assertEqual(director.segment_inputs_sig(after, self.d, 'd2'),
                         director.segment_inputs_sig(before, self.d, 'd2'))
        self.assertNotEqual(director.inputs_sig(after, self.d), director.inputs_sig(before, self.d))


if __name__ == '__main__':
    unittest.main()
