"""SB3d local media cases; import this mixin into the director contract suite."""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from console import media


ROOT = Path(__file__).resolve().parents[1]


class DirectorSB3dMediaCases:
    def media_fixture(self, *, seconds=16, fps='30', audio=True, caption=False):
        (ROOT/'runtime/temp').mkdir(parents=True, exist_ok=True)
        directory = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT/'runtime/temp')))
        self.enterContext(patch.dict(os.environ, {'PATH':str(ROOT/'runtime/ffmpeg')+os.pathsep+os.environ.get('PATH','')}))
        source = directory/'source.mp4'
        args = ['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',
                f'color=c=blue:s=160x120:r={fps}:d={seconds}']
        if audio:
            args += ['-f','lavfi','-i',f'sine=frequency=440:sample_rate=16000:duration={seconds}']
        if caption:
            args += ['-vf','drawbox=x=60:y=70:w=40:h=12:color=white:t=fill']
        args += ['-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p']
        if audio:
            args += ['-c:a','aac','-shortest']
        media._run([*args,str(source)])
        return directory, source

    def raw_video_probe(self, path):
        raw = media._run(['ffprobe','-v','error','-count_frames','-show_streams','-show_format','-of','json',str(path)])
        return json.loads(raw.stdout)

    def test_sb3d_real_stretch_preserves_fps_silence_duration_and_frame_integrity(self):
        self.assertTrue(callable(getattr(media,'stretch_clip',None)), 'SB3d needs local reference stretching')
        directory, source = self.media_fixture()
        for index, (seconds, target) in enumerate(((.7,4),(5.333,6),(14.7,15))):
            with self.subTest(seconds=seconds):
                cut = directory/f'cut-{index}.mp4'; output = directory/f'output-{index}.mp4'
                media.cut_clip(source, .2, seconds, cut, mute=True)
                info = media.stretch_clip(cut, seconds, target, output)
                raw = self.raw_video_probe(output)
                video = next(s for s in raw['streams'] if s['codec_type']=='video')
                self.assertLessEqual(abs(float(raw['format']['duration'])-target),.05)
                self.assertEqual(video['avg_frame_rate'],'30/1')
                self.assertFalse(any(s['codec_type']=='audio' for s in raw['streams']))
                self.assertEqual(int(video['nb_read_frames']),int(video['nb_frames']))
                self.assertEqual(int(video['nb_frames']),target*30)
                self.assertEqual(info['decoded_frames'],target*30)
                self.assertEqual(info['indexed_frames'],target*30)

    def test_sb3d_real_stretch_preserves_fractional_original_frame_rate(self):
        self.assertTrue(callable(getattr(media,'stretch_clip',None)), 'SB3d needs local reference stretching')
        directory, source = self.media_fixture(seconds=2,fps='30000/1001',audio=False)
        output = directory/'output.mp4'
        media.stretch_clip(source,2,4,output)
        raw = self.raw_video_probe(output)
        self.assertEqual(raw['streams'][0]['avg_frame_rate'],'30000/1001')
        self.assertLessEqual(abs(float(raw['format']['duration'])-4),.05)

    def test_sb3d_caption_mask_uses_source_clock_before_stretch(self):
        self.assertTrue(callable(getattr(media,'stretch_clip',None)), 'SB3d needs local reference stretching')
        directory, source = self.media_fixture(seconds=5,caption=True)
        cut, output = directory/'cut.mp4', directory/'output.mp4'
        # A 2.2s source span becomes 4s. The mask's .5–1.0s source window
        # must become .91–1.82s, with the same visible box outside that window.
        media.cut_clip(source,1.25,2.2,cut,mute=True,
                       masks=[{'start':.5,'end':1.0,'bbox':[.3,.5,.7,.8]}])
        media.stretch_clip(cut,2.2,4,output)
        def center_rgb(at):
            pixels = media.small_frame(output,at,160,120)
            offset = (76*160+80)*3
            return tuple(pixels[offset:offset+3])
        self.assertGreater(min(center_rgb(.25)),200)
        self.assertLess(max(center_rgb(1.3)[:2]),40)
        self.assertGreater(center_rgb(1.3)[2],180)
        self.assertGreater(min(center_rgb(2.4)),200)

    def test_sb3d_reference_validation_rejects_wrong_duration_and_audio(self):
        self.assertTrue(callable(getattr(media,'validate_reference_clip',None)), 'SB3d needs decoded-file validation')
        directory, source = self.media_fixture(seconds=4,audio=True)
        with self.assertRaisesRegex(media.MediaError,'时长'):
            media.validate_reference_clip(source,6)
        with self.assertRaisesRegex(media.MediaError,'无声|音频'):
            media.validate_reference_clip(source,4)

    def test_sb3d_reference_validation_rejects_index_decode_mismatch(self):
        self.assertTrue(callable(getattr(media,'validate_reference_clip',None)), 'SB3d needs decoded-file validation')
        directory, source = self.media_fixture(seconds=4,audio=False)
        run = media._run
        def inconsistent_probe(args,*pos,**kw):
            result = run(args,*pos,**kw)
            if '-count_frames' in args:
                raw = json.loads(result.stdout)
                video = next(s for s in raw['streams'] if s['codec_type']=='video')
                video['nb_read_frames'] = str(int(video['nb_frames'])-1)
                result.stdout = json.dumps(raw)
            return result
        with patch.object(media,'_run',side_effect=inconsistent_probe):
            with self.assertRaisesRegex(media.MediaError,'帧'):
                media.validate_reference_clip(source,4)

    def test_sb3d_video_preview_stretches_real_reference_and_bills_target(self):
        from PIL import Image
        from console import config, director, jimmy_bridge as bridge, store
        directory, source = self.media_fixture(seconds=8,caption=True)
        self.enterContext(patch.object(config,'PROJECTS_DIR',directory/'project-fixtures'))
        self.enterContext(patch.object(config,'load',return_value=dict(config.DEFAULTS)))
        self.enterContext(patch.object(config,'_parse',side_effect=AssertionError('no secret reads')))
        self.enterContext(patch('urllib.request.urlopen',side_effect=AssertionError('no live network')))
        pid = store.create('SB3d real local media')['id']; project = store.project_dir(pid)
        (project/'source/source.mp4').write_bytes(source.read_bytes())
        Image.new('RGB',(30,50),'blue').save(project/'assets/board.png')
        media.extract_frame(source, 1.25, project/'assets/original.png')
        segment = dict(id='d1',cells=['c01'],mode='video',images=['c01'],gen_images=['c01'],prompt='图1 提供目标画面；视频1 提供动作。',check=['保持人物身份'],
                       timeline=[{'cells':['c01'],'text':'Stand still.'}],source_start=1.25,source_end=6.583,
                       source_seconds=5.333,target_seconds=6,duration=6,stretch=6/5.333)
        store.update(pid,lambda data:data.update(
            source={'file':'source/source.mp4'},director={'format_version':3,'segments':[dict(segment,inputs_sig='media-only')]},
            storyboard={'cells':[dict(id='c01',start=1.25,end=6.583,file='assets/original.png')]},
            storyboard_edits={'sheets':{'s01':{'plan':{'remove_text':True}}}},
            segment_boards={'segments':{'d1':{'plan':{'remove_text':True}}}},
            breakdown={'overlays':[{'start':2,'end':3,'kind':'caption','bbox':[.3,.5,.7,.8]}]}))
        self.enterContext(patch.object(director,'build_storyboard'))
        self.enterContext(patch.object(director,'require_ready'))
        self.enterContext(patch.object(director,'segment_inputs_sig',return_value='media-only'))
        self.enterContext(patch.object(director,'references',return_value=[
            {'file':'assets/board.png','key':'c01','cell':'c01','label':'图1'}]))
        def package(plan, pkg):
            return ({**plan,'tasks':[{**plan['tasks'][0],'model':bridge.MINI_VIDEO}]},{'job_sha256':'offline'})
        self.enterContext(patch.object(bridge,'_prepare_package',side_effect=package))
        run = bridge.preview(pid,'d1')
        self.assertEqual(run['review_inputs']['segment']['prompt'], segment['prompt'])
        frozen = run['review_inputs']['source_cells'][0]
        self.assertEqual((project/frozen['file']).read_bytes(), (project/'assets/original.png').read_bytes())
        raw = self.raw_video_probe(project/run['clip'])
        video = raw['streams'][0]
        self.assertLessEqual(abs(float(raw['format']['duration'])-6),.05)
        self.assertEqual((int(video['nb_frames']),int(video['nb_read_frames'])),(180,180))
        self.assertEqual(run['billing_seconds'],12)
        self.assertEqual(run['target_seconds'],6)
        self.assertEqual(run['stretch'],6/5.333)
        self.assertEqual(run['clip_processing']['masks_clock'],'source')
        self.assertEqual(run['preview']['metadata']['console']['stretch'],6/5.333)
        pixels = media.small_frame(project/run['clip'],1.4,160,120)
        offset = (76*160+80)*3
        self.assertLess(max(pixels[offset:offset+2]),40)
