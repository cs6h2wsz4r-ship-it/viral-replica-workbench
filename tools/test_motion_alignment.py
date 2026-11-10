"""Local timing diagnostics: synthetic regressions and opt-in real incident calibration."""
import json
import hashlib
import os
import sys
import tempfile
import unittest
import wave
import numpy as np
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import asr, media


class MotionAlignmentTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        (ROOT / 'runtime/temp').mkdir(parents=True, exist_ok=True)
        self.assertTrue(callable(getattr(media, 'motion_alignment', None)), 'motion_alignment is missing')
        self.temp = self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp'))
        self.out = Path(self.temp)
        self.energy = np.random.default_rng(31).random(150)
        self.transcript = {'language': 'en', 'timing': 'forced_alignment', 'segments': [
            {'start': 11.073, 'end': 13.727, 'text': 'No, because why does this look this good when I walk?'},
            {'start': 15.07, 'end': 17.608, 'text': 'The back, the slit, yeah.'}]}
        self.generated = {'timing': 'forced_alignment', 'segments': [
            {'start': .603, 'end': 3, 'text': self.transcript['segments'][0]['text']},
            {'start': 4.04, 'end': 7, 'text': self.transcript['segments'][1]['text']}]}

    def run_check(self, energies=None, transcript=None):
        with patch.object(media, '_motion_energy', side_effect=energies or [self.energy, self.energy]), \
                patch.object(media, 'extract_audio_wav'), \
                patch.object(asr, 'transcribe_best', return_value=transcript or self.generated) as recognition:
            result = media.motion_alignment(Path('result.mp4'), Path('source.mp4'), 10, 25,
                                            self.transcript, self.out)
            self.assertTrue(recognition.call_args.kwargs['local_only'])
            return result

    def test_early_dialogue_has_signed_lags_and_warns(self):
        report = self.run_check()
        self.assertEqual(report['status'], 'misaligned')
        self.assertEqual([s['lag_seconds'] for s in report['speech_lags']], [-.47, -1.03])
        self.assertEqual(report['motion_lag_seconds'], 0)
        self.assertAlmostEqual(report['motion_correlation'], 1)
        self.assertTrue(report['warnings'])

    def test_identical_source_clocks_are_aligned(self):
        generated = {'segments': [{**s, 'start': s['start'] - 10, 'end': s['end'] - 10}
                                  for s in self.transcript['segments']]}
        report = self.run_check(transcript=generated)
        self.assertEqual(report['status'], 'aligned')
        self.assertEqual([s['lag_seconds'] for s in report['speech_lags']], [0, 0])

    def test_motion_sign_both_directions(self):
        import numpy as np
        for lag in (-8, 8):
            shifted = np.roll(self.energy, lag)
            report = self.run_check([self.energy, shifted])
            self.assertAlmostEqual(report['motion_lag_seconds'], lag / 10)
            self.assertEqual(report['status'], 'misaligned')

    def test_flat_motion_does_not_claim_alignment(self):
        report = self.run_check([self.energy * 0, self.energy * 0])
        self.assertIsNone(report['motion_lag_seconds'])
        self.assertTrue(report['warnings'])
        self.assertNotEqual(report['status'], 'aligned')

    def test_missing_asr_is_inconclusive(self):
        with patch.object(media, '_motion_energy', return_value=self.energy), \
                patch.object(media, 'extract_audio_wav'), \
                patch.object(asr, 'transcribe_best', side_effect=RuntimeError('local model absent')):
            report = media.motion_alignment(Path('result.mp4'), Path('source.mp4'), 10, 25,
                                            self.transcript, self.out)
        self.assertEqual(report['status'], 'inconclusive')
        self.assertTrue(any('local model absent' in w for w in report['warnings']))

    def test_chinese_sentence_matches_real_character_or_word_timestamps(self):
        source = {'segments': [{'start': 1, 'end': 3, 'text': '这条裙子真好看。'}]}
        for pieces in (list('这条裙子真好看'), ['这条', '裙子', '真好看']):
            generated = {'segments': [{'start': 0, 'end': 2, 'text': '这条裙子真好看。',
                'words': [{'word': word, 'start': index * .2, 'end': index * .2 + .2}
                          for index, word in enumerate(pieces)]}]}
            with self.subTest(pieces=pieces):
                rows, warnings = media._speech_lags(source, generated, 0, 5)
                self.assertEqual(warnings, [])
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['generated_start'], 0)
                self.assertEqual(rows[0]['lag_seconds'], -1)

    def test_missing_sentence_cannot_pass(self):
        report = self.run_check(transcript={'segments': []})
        self.assertEqual(report['status'], 'inconclusive')

    def test_low_correlation_warns(self):
        import numpy as np
        report = self.run_check([self.energy, np.random.default_rng(52).random(150)])
        self.assertLess(report['motion_correlation'], report['thresholds']['min_correlation'])
        self.assertEqual(report['status'], 'misaligned')

    def test_real_frame_sampling_and_incident_motion(self):
        project = ROOT / 'projects/p1027195039-ccc0'
        source = project / 'source/source.mp4'
        incident = project / 'jimmy-runs/p1027195039-ccc0-seg-01-r1/results/video-seg-01.mp4'
        with patch.dict(os.environ, {'PATH': str(ROOT / 'runtime/ffmpeg') + os.pathsep + os.environ.get('PATH', '')}):
            source_energy = media._motion_energy(source, 0, 15)
            incident_energy = media._motion_energy(incident, 0, 15)
        self.assertEqual(len(source_energy), 149)
        self.assertEqual(media._motion_correlation(source_energy, source_energy), (0, 1))
        lag, coefficient = media._motion_correlation(source_energy, incident_energy)
        self.assertEqual(lag, -1)
        self.assertAlmostEqual(coefficient, .5024, places=3)

    def test_real_asr_incident_and_self_from_bound_evidence(self):
        reports = real_sample_checks()
        self.assertEqual(reports['incident']['status'], 'misaligned')
        self.assertEqual(reports['self']['status'], 'aligned')

    def test_local_only_propagates_through_whisperx_fallback(self):
        with patch.object(asr, 'engines', return_value=['whisperx', 'faster-whisper']), \
                patch.object(asr, 'transcribe_whisperx', side_effect=RuntimeError('alignment unavailable')) as wx, \
                patch.object(asr, 'transcribe', return_value={'segments': []}) as fw:
            asr.transcribe_best(Path('unused.wav'), 'medium', 'int8', 'en', local_only=True)
        self.assertTrue(wx.call_args.kwargs['local_only'])
        self.assertTrue(fw.call_args.kwargs['local_only'])

    def test_local_asr_never_retries_download(self):
        wav = self.out / 'empty.wav'
        with wave.open(str(wav), 'wb') as output:
            output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            output.writeframes(b'\0' * 3200)
        constructor = Mock(side_effect=RuntimeError('not cached'))
        with patch.dict(sys.modules, {'faster_whisper': SimpleNamespace(WhisperModel=constructor)}), \
                patch.object(asr, '_model', None), patch.object(asr, '_model_key', None):
            with self.assertRaisesRegex(RuntimeError, 'not cached'):
                asr.transcribe(wav, local_only=True)
        self.assertEqual(constructor.call_count, 1)
        self.assertTrue(constructor.call_args.kwargs['local_files_only'])


def real_sample_checks():
    """Decode the real inputs and verify frozen, SHA-bound local ASR evidence.

    The fixture contains only measured numbers, timing provenance and hashes;
    source media remain read-only. Regenerate explicitly with --real.
    """
    fixture = ROOT / 'tools/fixtures/round11/motion-calibration.json'
    evidence = json.loads(fixture.read_text())
    for name, sha in evidence['input_sha256'].items():
        if name == 'console/media.py':
            # Keep the original measured evidence and unchanged-symbol bindings.
            # 24.11 intentionally changes authored speech anchors and boundary
            # interpretation; bind those exact replacements in a separate manifest.
            import ast
            binding = json.loads((ROOT / 'tools/fixtures/round22/media-calibration-binding.json').read_text())
            assert binding['previous_media_sha256'] == sha
            revision = json.loads((ROOT / 'tools/fixtures/round24_11/media-calibration-binding.json').read_text())
            assert revision['previous_media_sha256'] == binding['current_media_sha256']
            assert set(revision['changed_symbols']) == {'_speech_lags', 'motion_alignment'}
            source_text = (ROOT / name).read_text()
            nodes = {node.name: ast.get_source_segment(source_text, node)
                     for node in ast.parse(source_text).body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
            for node in ast.parse(source_text).body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name): nodes[target.id] = ast.get_source_segment(source_text, node)
            for symbol, digest in binding['calibrated_source_sha256'].items():
                if symbol in revision['changed_symbols']:
                    change = revision['changed_symbols'][symbol]
                    assert change['previous_sha256'] == digest
                    digest = change['current_sha256']
                assert hashlib.sha256(nodes[symbol].encode()).hexdigest() == digest, 'Calibration code changed: ' + symbol
            sha = revision['current_media_sha256']
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == sha, 'Calibration input changed: ' + name
    source = ROOT / evidence['source_file']
    incident = ROOT / evidence['result_file']
    with patch.dict(os.environ, {'PATH': str(ROOT / 'runtime/ffmpeg') + os.pathsep + os.environ.get('PATH', '')}):
        source_energy = media._motion_energy(source, 0, 15)
        generated_energy = media._motion_energy(incident, 0, 15)
    assert len(source_energy) == 149
    assert media._motion_correlation(source_energy, source_energy) == (0, 1)
    lag, coefficient = media._motion_correlation(source_energy, generated_energy)
    reports = evidence['reports']
    assert lag == reports['incident']['motion_lag_seconds']
    assert abs(coefficient - reports['incident']['motion_correlation']) < .001
    assert reports['incident']['status'] == 'misaligned'
    assert abs(reports['incident']['speech_lags'][0]['lag_seconds'] + .952) < .15
    assert abs(reports['incident']['speech_lags'][1]['lag_seconds'] + 1.03) < .15
    assert reports['self']['status'] == 'aligned'
    assert reports['self']['motion_lag_seconds'] == 0
    assert all(abs(row['lag_seconds']) < .1 for row in reports['self']['speech_lags'])
    return reports


def real_calibration():
    """Opt-in real cached ASR, with the same secret/network/protected-write audit."""
    from tools.run_tests_offline import audit
    from console.runtime_env import DIRS
    for key, value in DIRS.items():
        os.environ[key] = str(value)
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      HF_HUB_DISABLE_TELEMETRY='1', PYTHONDONTWRITEBYTECODE='1')
    os.environ['PATH'] = str(ROOT / 'runtime/ffmpeg') + os.pathsep + os.environ.get('PATH', '')
    tempfile.tempdir = str(ROOT / 'runtime/temp')
    sys.dont_write_bytecode = True

    def calibration_audit(event, args):
        # torchaudio calls makedirs(exist_ok=True) for its existing checkpoint
        # directory. This is a no-op; actual creations/writes remain forbidden.
        if event == 'os.mkdir' and Path(args[0]).is_dir():
            return
        audit(event, args)

    sys.addaudithook(calibration_audit)
    project = ROOT / 'projects/p1027195039-ccc0'
    source = project / 'source/source.mp4'
    result = project / 'jimmy-runs/p1027195039-ccc0-seg-01-r1/results/video-seg-01.mp4'
    out = ROOT / 'runtime/temp/motion-fidelity'
    reports = {}
    for label, video in [('incident', result), ('self', source)]:
        original_makedirs = os.makedirs
        def existing_directory_ok(name, mode=0o777, exist_ok=False):
            if exist_ok and Path(name).is_dir():
                return None
            return original_makedirs(name, mode=mode, exist_ok=exist_ok)
        with patch.object(os, 'makedirs', side_effect=existing_directory_ok):
            report = media.motion_alignment(video, source, 0, 15,
                                            project / 'analysis/transcript.raw.json', out / label)
        (out / (label + '-report.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2))
        reports[label] = report
        print(label, json.dumps({k: v for k, v in report.items() if k != 'motion_energy'},
                                ensure_ascii=False, indent=2), flush=True)
    assert reports['incident']['status'] == 'misaligned'
    assert abs(reports['incident']['speech_lags'][1]['lag_seconds'] + 1.03) < .15
    assert reports['self']['status'] == 'aligned'
    assert abs(reports['self']['motion_lag_seconds']) <= .1
    assert all(abs(row['lag_seconds']) <= .5 for row in reports['self']['speech_lags'])
    bound_paths = [source, result, project / 'analysis/transcript.raw.json',
                   ROOT / 'console/media.py', ROOT / 'console/asr.py']
    for label in ('incident', 'self'):
        bound_paths.extend([out / (label + '-report.json'), out / label / 'generated-transcript.json'])
    manifest = {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in bound_paths}
    (out / 'calibration-manifest.json').write_text(json.dumps(manifest, indent=2))
    estimates = {}
    for label in ('self', 'incident'):
        estimates[label] = asr.transcribe_best(out / label / 'generated-audio.wav', 'medium', 'int8', 'en',
                                               engine='faster-whisper', local_only=True)
        (out / (label + '-asr-estimate.json')).write_text(json.dumps(estimates[label], ensure_ascii=False, indent=2))
    source_estimate = estimates['self']['segments'][0]['start']
    generated_estimate = estimates['incident']['segments'][0]['start']
    source_forced = reports['self']['speech_lags'][0]['generated_start']
    generated_forced = reports['incident']['speech_lags'][0]['generated_start']
    evidence = {
        'provenance': 'Measured using tools/test_motion_alignment.py --real, offline local medium int8 + wav2vec2 alignment; no remote transport or downloads',
        'source_file': source.relative_to(ROOT).as_posix(),
        'result_file': result.relative_to(ROOT).as_posix(),
        'source_interval': [0, 15],
        'input_sha256': {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in bound_paths[:5]},
        'transcript_sha256': {label: hashlib.sha256((out / label / 'generated-transcript.json').read_bytes()).hexdigest()
                              for label in ('incident', 'self')},
        'reports': {label: {key: value for key, value in report.items() if key != 'motion_energy'}
                    for label, report in reports.items()},
        'checklist_discrepancy': {
            'source_forced_alignment_start': source_forced,
            'source_independent_asr_estimate_start': source_estimate,
            'generated_forced_alignment_start': generated_forced,
            'generated_independent_asr_estimate_start': generated_estimate,
            'same_method_forced_lag': round(generated_forced - source_forced, 3),
            'same_method_estimate_lag': round(generated_estimate - source_estimate, 3),
            'mixed_method_lag': round(generated_forced - source_estimate, 3),
            'note': 'Checklist approx -.47 combines source ASR estimate with generated forced alignment; preserve authoritative raw source timing for main report.'}}
    fixture = ROOT / 'tools/fixtures/round11/motion-calibration.json'
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    return reports


if __name__ == '__main__':
    if '--real' in sys.argv:
        real_calibration()
    else:
        unittest.main(verbosity=2)
