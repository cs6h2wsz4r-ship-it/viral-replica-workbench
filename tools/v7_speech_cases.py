"""V7 sentence reconstruction, entirely offline and on temporary projects."""
import copy
import importlib
import importlib.util
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from console import config, evidence, gateway, store

ROOT = Path(__file__).resolve().parents[1]


class V7SpeechTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('console.speech_lines'), 'V7 speech_lines module is missing')
        self.speech = importlib.import_module('console.speech_lines')
        temp = tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp')
        self.addCleanup(temp.cleanup)
        self.enterContext(patch.object(config, 'PROJECTS_DIR', Path(temp.name)))
        self.enterContext(patch.object(config, 'load', return_value={**config.DEFAULTS, 'OPENAI_API_KEY': 'offline'}))
        self.remote = self.enterContext(patch.object(gateway, 'reason', side_effect=AssertionError('unmocked model request')))
        self.pid = store.create('v7-speech')['id']
        self.d = store.project_dir(self.pid)
        self.transcript = {'segments': [
            {'start': 0, 'end': 1.5, 'text': 'people are', 'words': [
                {'word': 'people', 'start': 0, 'end': .5}, {'word': 'are', 'start': .7, 'end': 1.5}]},
            {'start': 1.6, 'end': 4, 'text': 'going crazy today', 'words': [
                {'word': 'going', 'start': 1.6, 'end': 2}, {'word': 'crazy', 'start': 2.1, 'end': 3},
                {'word': 'today', 'start': 3.7, 'end': 4}]}]}
        self.dialogue = [{k: s[k] for k in ('start', 'end', 'text')} | {'speaker': 'C1'} for s in self.transcript['segments']]
        self.good = {'lines': [{'first': 0, 'last': 3, 'text': 'People are going crazy!'},
                               {'first': 4, 'last': 4, 'text': 'Today.'}]}
        self.save()

    def save(self):
        store.atomic_write_json(self.d / 'analysis/transcript.raw.json', self.transcript)
        store.update(self.pid, lambda data: data.update(source={'file': 'source/source.mp4', 'sha256': 'source'},
            breakdown={'dialogue': copy.deepcopy(self.dialogue)}, analysis={'status': 'done', 'steps': {}}))

    def wire(self, value):
        return {'content': json.dumps(value), 'model': 'offline', 'provider': 'openai', 'usage': {}}

    def answer(self, value=None):
        self.remote.side_effect = None
        self.remote.return_value = self.wire(copy.deepcopy(self.good if value is None else value))

    def test_word_indices_timing_and_pause_are_original(self):
        words = self.speech.words(store.load(self.pid), self.d)
        self.assertEqual([w['index'] for w in words], list(range(5)))
        self.assertEqual(words[2], {'index': 2, 'word': 'going', 'start': 1.6, 'end': 2.0, 'gap': .1})
        lines = self.speech.validate(self.good, words, self.dialogue)
        self.assertEqual(lines[0], {'start': 0.0, 'end': 3.0, 'text': 'People are going crazy!',
            'words': [0, 3], 'speaker': 'C1', 'pause_after': .7})
        self.assertEqual(lines[1]['pause_after'], 0)

    def test_validation_rejects_word_changes_omissions_overlap_and_order(self):
        words = self.speech.words(store.load(self.pid), self.d)
        bad = [
            {'lines': [{'first': 0, 'last': 4, 'text': 'People are going wild today.'}]},
            {'lines': [{'first': 0, 'last': 3, 'text': 'People are going crazy.'}]},
            {'lines': [{'first': 0, 'last': 3, 'text': 'People are going crazy.'}, {'first': 3, 'last': 4, 'text': 'Crazy today.'}]},
            {'lines': [{'first': 1, 'last': 4, 'text': 'Are going crazy today.'}]},
            {'lines': [{'first': 0, 'last': 4, 'text': 'People are crazy going today.'}]},
            {'lines': [{'first': True, 'last': 4, 'text': 'People are going crazy today.'}]},
        ]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.speech.validate(value, words, self.dialogue)

    def test_normalization_tolerates_typography_without_swallowing_words(self):
        normalize = self.speech.normalize_text
        self.assertEqual(normalize("Don't use high-end!"), normalize('DONT use highend.'))
        self.assertEqual(normalize("I’m here."), normalize("I'm here"))
        self.assertNotEqual(normalize('people are'), normalize('peopleare'))
        self.assertEqual(normalize('high-end'), normalize('highend'))
        self.assertEqual(normalize('high‐end'), normalize('highend'))

    def test_hyphenated_original_word_allows_joined_and_spaced_typography(self):
        words = [{'index': 0, 'word': 'high-end', 'start': 0, 'end': 1, 'gap': 0},
                 {'index': 1, 'word': "don't", 'start': 1, 'end': 2, 'gap': 0}]
        for text in ("Highend, don’t!", "High end, dont!", "High–end, don't."):
            with self.subTest(text=text):
                self.assertEqual(self.speech.validate({'lines': [{'first': 0, 'last': 1, 'text': text}]}, words, [])[0]['text'], text)
        ordinary = [{**word, 'word': text} for word, text in zip(words, ('people', 'are'))]
        with self.assertRaises(ValueError):
            self.speech.validate({'lines': [{'first': 0, 'last': 1, 'text': 'Peopleare.'}]}, ordinary, [])

    def test_malformed_transcript_falls_back_and_does_not_repeat(self):
        (self.d / 'analysis/transcript.raw.json').write_text('{broken')
        result = self.speech.ensure(self.pid)
        self.assertEqual(result['breakdown']['speech_lines_state']['status'], 'fallback')
        self.assertEqual(self.speech.lines(result), self.dialogue)
        self.speech.ensure(self.pid)
        self.remote.assert_not_called()

    def test_speaker_uses_accumulated_time_overlap(self):
        labels = [{'start': 0, 'end': 1, 'speaker': 'C1'}, {'start': 1, 'end': 2, 'speaker': 'C2'},
                  {'start': 2, 'end': 3, 'speaker': 'C1'}]
        lines = self.speech.validate(self.good, self.speech.words(store.load(self.pid), self.d), labels)
        self.assertEqual(lines[0]['speaker'], 'C1')

    def test_ensure_retry_plain_text_and_srt_preserves_original_dialogue(self):
        bad = {'lines': [{'first': 0, 'last': 4, 'text': 'changed words'}]}
        self.remote.side_effect = [self.wire(bad), self.wire(self.good)]
        result = self.speech.ensure(self.pid)
        self.assertEqual(self.remote.call_count, 2)
        self.assertEqual(result['breakdown']['dialogue'], self.dialogue)
        self.assertEqual(result['breakdown']['speech_lines_state']['status'], 'done')
        first, second = self.remote.call_args_list
        self.assertEqual(first.args[0], 'speech_lines')
        self.assertTrue(all(isinstance(m['content'], str) for m in first.args[1]))
        self.assertTrue(any('不一致' in m['content'] for m in second.args[1]))
        self.assertIn('People are going crazy!', (self.d / 'analysis/台词表.srt').read_text())
        self.assertEqual((self.d / 'analysis/台词表.转写原句.srt').read_text(), evidence.to_srt({'segments': self.dialogue}))
        self.speech.ensure(self.pid)
        self.assertEqual(self.remote.call_count, 2)

    def test_failed_retry_falls_back_once_with_safe_reason(self):
        self.remote.side_effect = RuntimeError('Authorization: Bearer sk-hidden request={PRIVATE}')
        result = self.speech.ensure(self.pid)
        state = result['breakdown']['speech_lines_state']
        self.assertEqual(self.remote.call_count, 2)
        self.assertEqual(state['status'], 'fallback')
        self.assertEqual(state['warning'], '台词自动断句没成功，暂用转写的原句。')
        for forbidden in ('sk-hidden', 'Authorization', 'PRIVATE', 'Bearer'):
            self.assertNotIn(forbidden, json.dumps(result, ensure_ascii=False))
        self.assertEqual([r['text'] for r in result['breakdown']['speech_lines']], [r['text'] for r in self.dialogue])
        self.speech.ensure(self.pid)
        self.assertEqual(self.remote.call_count, 2)

    def test_no_words_and_silent_project_never_request_model(self):
        for dialogue in (self.dialogue, []):
            self.transcript = {'segments': []}
            self.dialogue = dialogue
            self.save()
            result = self.speech.ensure(self.pid)
            self.assertEqual(self.speech.lines(result), dialogue)
            self.assertIn(result['breakdown']['speech_lines_state']['status'], ('fallback', 'skipped'))
        self.remote.assert_not_called()

    def test_stale_source_or_transcript_never_commits_results(self):
        for changed in ('source', 'transcript', 'dialogue'):
            self.save()
            def mutate(*args, **kwargs):
                if changed == 'source':
                    store.update(self.pid, lambda data: data['source'].update(sha256='changed'))
                elif changed == 'dialogue':
                    store.update(self.pid, lambda data: data['breakdown']['dialogue'][0].update(text='upstream edit'))
                else:
                    transcript = copy.deepcopy(self.transcript)
                    transcript['segments'][0]['words'][0]['word'] = 'upstream'
                    store.atomic_write_json(self.d / 'analysis/transcript.raw.json', transcript)
                return self.wire(self.good)
            self.remote.side_effect = mutate
            result = self.speech.ensure(self.pid)
            self.assertNotIn('speech_lines', result['breakdown'])
            self.assertFalse((self.d / 'analysis/台词表.srt').exists())

    def test_concurrent_ensure_only_runs_once_without_blocking_store_log(self):
        entered, release = threading.Event(), threading.Event()
        results, failures = [], []
        def model(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError('test model did not release')
            return self.wire(self.good)
        self.remote.side_effect = model
        def run():
            try:
                results.append(self.speech.ensure(self.pid))
            except BaseException as exc:
                failures.append(exc)
        first = threading.Thread(target=run)
        second = threading.Thread(target=run)
        first.start()
        self.assertTrue(entered.wait(3))
        second.start()
        logger = threading.Thread(target=lambda: store.log(self.pid, 'concurrent progress'))
        logger.start(); logger.join(1)
        nonblocking = not logger.is_alive()
        release.set()
        first.join(4); second.join(4); logger.join(4)
        self.assertTrue(nonblocking)
        self.assertFalse(failures)
        self.assertEqual(len(results), 2)
        self.assertEqual(self.remote.call_count, 1)

    def test_real_transcript_can_rejoin_sentence_across_asr_boundary(self):
        fixture = ROOT / 'projects/p1105180906-f9f6/analysis/transcript.raw.json'
        shutil.copyfile(fixture, self.d / 'analysis/transcript.raw.json')
        transcript = json.loads((self.d / 'analysis/transcript.raw.json').read_text())
        words = self.speech.words(store.load(self.pid), self.d)
        # Fixture text belongs in tests only; production prompt must stay generic.
        a = next(i for i,w in enumerate(words) if w['word'] == 'people')
        b = next(i for i,w in enumerate(words[a:], a) if w['word'] == 'these')
        bounds = [(0, a-1), (a, b), (b+1, len(words)-1)]
        payload = {'lines': [{'first': start, 'last': end, 'text': ' '.join(w['word'] for w in words[start:end+1]) + '.'}
                             for start, end in bounds]}
        lines = self.speech.validate(payload, words, transcript['segments'])
        self.assertIn('people are going crazy', lines[1]['text'])
        self.assertEqual(lines[1]['start'], 7.698)
        self.assertEqual(lines[1]['end'], 9.447)
        self.assertTrue(all(term not in self.speech.SPEECH_SYSTEM.lower() for term in ('elf', 'lip balm', 'people are going crazy')))

    def test_invalid_json_has_only_two_raw_model_attempts(self):
        from console import llm
        with patch.object(gateway, 'reason_checked', side_effect=lambda step, request, **kw:
                gateway._reason(step, request, checked=True, **kw)), \
             patch.object(gateway, 'reason', side_effect=lambda step, request, **kw:
                gateway._reason(step, request, **kw)), \
             patch.object(llm, 'chat', return_value={'content': 'not json', 'usage': {}, 'model': 'offline', 'provider': 'openai'}) as raw:
            result = self.speech.ensure(self.pid)
        self.assertEqual(raw.call_count, 2)
        self.assertEqual(result['breakdown']['speech_lines_state']['status'], 'fallback')

    def test_route_follows_analysis(self):
        from console.gateway import routes
        self.assertEqual(routes.DEFAULT_ROUTES['speech_lines'], 'follow:analysis')
        self.assertEqual(routes.STEP_LABELS['speech_lines'], '台词断句')
        values = {**config.DEFAULTS, 'OPENAI_API_KEY': 'offline', 'ROUTE_ANALYSIS': 'openai:custom-analysis'}
        self.assertEqual(routes.resolve('speech_lines', values=values)['model'], 'custom-analysis')
        routes.validate(values)

    def test_read_helpers_and_import_never_migrate_projects(self):
        before = (self.d / 'project.json').read_bytes()
        with patch.object(store, 'update', side_effect=AssertionError('read-only helper wrote project')):
            importlib.reload(self.speech)
            data = store.load(self.pid)
            self.speech.lines(data)
            self.speech.words(data, self.d)
        self.assertEqual((self.d / 'project.json').read_bytes(), before)
        self.remote.assert_not_called()

    def test_reanalysis_skip_asr_ignores_existing_transcript_file(self):
        from console import media, pipeline
        meta = {'model': 'offline', 'usage': {}, 'seconds': 0}
        for obj, name, value in [(config, 'provider_settings', {'model': 'offline'}),
            (media, 'probe', {'duration': 5, 'audio_stream_count': 1}), (media, 'hypit_boundaries', []),
            (media, 'merge_boundaries', []), (pipeline, '_grids', [])]:
            self.enterContext(patch.object(obj, name, return_value=value))
        with patch.object(gateway, 'reason', return_value=({}, meta)), patch.object(pipeline, '_build_analysis_storyboard'):
            pipeline._run_analysis(self.pid, 'openai', skip_asr=True)
        result = store.load(self.pid)
        self.assertEqual(result['breakdown']['speech_lines'], [])
        self.assertEqual(result['breakdown']['speech_lines_state']['status'], 'skipped')
        self.remote.assert_not_called()

    def test_late_analysis_result_does_not_mark_newer_analysis_failed(self):
        from console import asr, media, pipeline
        meta = {'model': 'offline', 'usage': {}, 'seconds': 0}
        for obj, name, value in [(config, 'provider_settings', {'model': 'offline'}),
            (media, 'probe', {'duration': 5, 'audio_stream_count': 1}), (media, 'hypit_boundaries', []),
            (media, 'merge_boundaries', []), (pipeline, '_grids', []), (asr, 'parse_srt', self.transcript)]:
            self.enterContext(patch.object(obj, name, return_value=value))
        def superseded(pid, pack, transcript, directory):
            store.update(pid, lambda data: data.update(analysis={'status': 'done', 'started': 'newer'},
                breakdown={'dialogue': [], 'overview': {'title': 'newer'}}))
            return {**pack, 'speech_lines': [], 'speech_lines_state': {'status': 'done'}}
        with patch.object(gateway, 'reason', return_value=({}, meta)), \
             patch.object(self.speech, 'refine', side_effect=superseded), \
             patch.object(pipeline, '_build_analysis_storyboard'):
            with self.assertRaisesRegex(ValueError, '变化'):
                pipeline.run_analysis(self.pid, 'openai', srt_text='offline')
        self.assertEqual(store.load(self.pid)['analysis'], {'status': 'done', 'started': 'newer'})
        self.assertEqual(store.load(self.pid)['breakdown']['overview']['title'], 'newer')

    def test_new_analysis_refines_after_speaker_annotation(self):
        from console import asr, media, pipeline
        overview = {'overview': {'language': 'en'}, 'characters': [{'id': 'C1'}],
                    'passages': [{'start': 0, 'end': 5}]}
        meta = {'model': 'offline', 'usage': {}, 'seconds': 0}
        self.answer()
        for obj, name, value in [(config, 'provider_settings', {'model': 'offline'}),
            (media, 'probe', {'duration': 5, 'audio_stream_count': 1}), (media, 'hypit_boundaries', []),
            (media, 'merge_boundaries', []), (pipeline, '_grids', []), (asr, 'parse_srt', self.transcript)]:
            self.enterContext(patch.object(obj, name, return_value=value))
        analysis_answers = iter([(overview, meta), ({'lineSpeakers': self.dialogue}, meta)])
        def analysis_reason(step, request, **kwargs):
            return self.remote(step, request, **kwargs) if step == 'speech_lines' else next(analysis_answers)
        with patch.object(gateway, 'reason', side_effect=analysis_reason), \
             patch.object(pipeline, '_build_analysis_storyboard'):
            pipeline._run_analysis(self.pid, 'openai', srt_text='offline')
        result = store.load(self.pid)
        self.assertEqual(result['breakdown']['speech_lines'][0]['speaker'], 'C1')
        self.assertEqual(result['breakdown']['speech_lines_state']['status'], 'done')
        self.assertIn('台词表.转写原句.srt', result['analysis']['docs'])
        self.speech.ensure(self.pid)
        self.assertEqual(self.remote.call_count, 1)
