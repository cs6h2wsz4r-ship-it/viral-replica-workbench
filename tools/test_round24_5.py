"""24.5 proposal regressions, updated for 24.7's advisory-only length policy."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import config, evidence, gateway, pipeline, prompts, replacement_guard, store


class Round24_5Tests(unittest.TestCase):
    def setUp(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', folder))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('禁止访问密钥')))
        self.enterContext(patch.object(config, 'load', return_value=dict(config.DEFAULTS)))
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('禁止真实请求')))
        self.pid = store.create('24.5 台词预算')['id']
        self.source = [dict(start=a, end=b, original=t, speaker='C1', onScreen=True, evidence='原片证据')
                       for a, b, t in [(0, 1.82, 'Excuse me, your dress is beautiful.'),
                                       (2, 3.36, "It's so flowy and comfortable."),
                                       (4, 4.82, 'I love the open back.')]]
        self.task = {'id': 'seg-01', 'language': 'en', 'dialogue': '\n'.join(r['original'] for r in self.source)}
        store.update(self.pid, lambda d: d.update(script={'tasks': [self.task]}, breakdown={'overview': {'language': 'en'}}))
        self.enterContext(patch.object(pipeline, '_dialogue_source', return_value=copy.deepcopy(self.source)))
        self.enterContext(patch.object(replacement_guard, 'dialogue_mismatches', return_value=['商品冲突']))
        self.enterContext(patch.object(replacement_guard, 'conflicts', return_value=[]))

    def response(self, texts=None):
        texts = texts or ['Your look is nice.', 'It feels nice.', 'Love this.']
        return {'language': 'en', 'lines': [dict(r, proposed=t, units=999, reason='替换商品')
                                           for r, t in zip(self.source, texts)]}

    def generate(self, result=None, **kwargs):
        with patch.object(gateway, 'reason_checked', return_value=(result or self.response(), {'model': 'mock', 'provider': 'openai'})) as model:
            proposal = pipeline.dialogue_proposal(self.pid, 'seg-01', **kwargs)
        return proposal, model

    def test_payload_and_prompt_no_longer_send_per_line_budgets(self):
        _, model = self.generate()
        messages = model.call_args.args[1]
        payload = json.loads(messages[1]['content'])
        for row in payload['lines']:
            for field in ('spokenSeconds', 'originalUnits', 'maxUnits'):
                self.assertNotIn(field, row)
        for rule in ('maxUnits', 'spokenSeconds', '[aeiouy]+', '逐句自查', 'units'):
            self.assertNotIn(rule, messages[0]['content'])
        self.assertIn('改写句的长度和原句差不多，不要比原句更长', messages[0]['content'])
        self.assertEqual(messages[0]['content'], prompts.DIALOGUE_PROPOSAL_SYSTEM)

    def test_overlong_line_is_saved_without_length_flags_or_model_counts(self):
        response = self.response(['Your look is nice.', 'It is so wonderfully beautiful and comfortable.', 'Love this.'])
        proposal, _ = self.generate(response)
        row = proposal['lines'][1]
        self.assertEqual(row['proposed'], response['lines'][1]['proposed'])
        for field in ('too_long', 'units', 'model_units', 'max_units', 'seconds', 'max_seconds'):
            self.assertNotIn(field, row)
        self.assertEqual(store.load(self.pid)['script']['tasks'][0]['dialogue_proposal'], proposal)
        self.assertEqual(proposal['lines'][0]['speaker'], 'C1')

    def test_adopt_accepts_long_edits_and_discards_legacy_length_fields(self):
        proposal, _ = self.generate()
        lines = copy.deepcopy(proposal['lines'])
        lines[1].update(proposed='It is so wonderfully beautiful and comfortable.', units=9999, max_units=0, too_long=True)
        with patch.object(pipeline, 'save_script', side_effect=lambda pid, script: {'script': script}) as save:
            adopted = pipeline.adopt_dialogue_proposal(self.pid, 'seg-01', lines)
        save.assert_called_once()
        self.assertEqual(adopted['script']['tasks'][0]['dialogue_choice'], 'adopt')
        self.assertIn(lines[1]['proposed'], adopted['script']['tasks'][0]['dialogue'])
        self.assertTrue(all('too_long' not in r for r in adopted['script']['tasks'][0]['dialogue_proposal']['lines']))

    def test_retry_receives_current_edited_text_without_budgets(self):
        proposal, _ = self.generate()
        previous = copy.deepcopy(proposal)
        previous['lines'][1].update(proposed='It is so wonderfully beautiful and comfortable.', max_units=9999)
        _, model = self.generate(instruction='只改短超长的第 2 句', previous_proposal=previous)
        payload = json.loads(model.call_args.args[1][1]['content'])
        self.assertEqual(payload['instruction'], '只改短超长的第 2 句')
        row = payload['previous_proposal']['lines'][1]
        self.assertNotIn('too_long', row)
        self.assertNotIn('max_units', row)
        self.assertEqual(row['proposed'], previous['lines'][1]['proposed'])

    def test_previous_draft_is_diagnostic_and_never_blocks_regeneration(self):
        proposal, _ = self.generate()
        empty = copy.deepcopy(proposal)
        empty['lines'][1]['proposed'] = ''
        _, model = self.generate(previous_proposal=empty)
        previous = json.loads(model.call_args.args[1][1]['content'])['previous_proposal']
        self.assertEqual(previous['lines'][1]['proposed'], '')
        self.assertNotIn('max_units', previous['lines'][1])
        stale = copy.deepcopy(proposal)
        stale['lines'][0]['end'] = 99
        for supplied in (True, False):
            with self.subTest(supplied=supplied):
                store.update(self.pid, lambda d: d['script']['tasks'][0].update(dialogue_proposal=stale))
                _, model = self.generate(**({'previous_proposal': stale} if supplied else {}))
                self.assertIsNone(json.loads(model.call_args.args[1][1]['content'])['previous_proposal'])
        # A replacement change can remove the conflict; past edited text is still just feedback.
        with patch.object(replacement_guard, 'dialogue_mismatches', return_value=[]):
            response = self.response([r['original'] for r in self.source])
            proposal, _ = self.generate(response, previous_proposal=empty)
        self.assertFalse(any(row['changed'] for row in proposal['lines']))

    def test_other_validation_and_unchanged_sentence_exemption_are_preserved(self):
        for change, error in [(lambda r: r['lines'].pop(), '数量'),
                              (lambda r: r['lines'][0].update(original='fake'), '原句或时间'),
                              (lambda r: r['lines'][0].update(end=99), '原句或时间'),
                              (lambda r: r['lines'][0].update(proposed=''), '不能为空'),
                              (lambda r: r.update(language='zh'), '原片语言'),
                              (lambda r: r['lines'][0].update(proposed='这套好看'), '原片语言')]:
            with self.subTest(error=error):
                response = self.response(); change(response)
                with self.assertRaisesRegex(ValueError, error):
                    self.generate(response)
        with patch.object(replacement_guard, 'dialogue_mismatches', return_value=[]):
            restored, _ = self.generate()
            self.assertEqual([r['proposed'] for r in restored['lines']], [r['original'] for r in self.source])
            self.assertTrue(all(r['restored'] and not r['changed'] for r in restored['lines']))
            proposal, _ = self.generate(self.response([r['original'] for r in self.source]))
        self.assertFalse(any(r['changed'] for r in proposal['lines']), '不改写句保持原样')

    def test_short_spoken_window_does_not_block_chinese_rewrites(self):
        self.task['language'] = 'zh'
        source = [dict(start=.001, end=.5731, original='这条裙子真的很好看')]
        result = {'lines': [dict(source[0], proposed='这套衣服好看')]}
        checked = pipeline._validate_dialogue_proposal({}, self.task, source, result)
        self.assertEqual(checked[0]['proposed'], '这套衣服好看')
        self.assertTrue(checked[0]['changed'])
        self.assertNotIn('too_long', checked[0])

    def test_server_exposes_gateway_kind_and_forwards_previous_proposal(self):
        from console import runtime_env
        with patch.object(runtime_env, 'apply'):
            import server
        handler = object.__new__(server.Handler)
        handler.path = f'/api/projects/{self.pid}/script/dialogue-proposal'
        handler.headers = {'X-Client-Build': server.build_status()['build']}
        previous = self.response()
        with patch.object(handler, '_body_json', return_value={'task_id': 'seg-01', 'previous_proposal': previous}), \
                patch.object(server, '_route_selection', return_value={'channel': 'openai', 'model': 'mock'}), \
                patch.object(pipeline, 'dialogue_proposal', side_effect=gateway.LLMError('官方额度不足', kind='quota')) as generate, \
                patch.object(handler, '_json') as output:
            handler._dispatch('POST')
        self.assertEqual(output.call_args.args[0].get('error_kind'), 'quota')
        self.assertEqual(generate.call_args.kwargs['previous_proposal'], previous)

    def test_status_resolves_explicit_dialogue_provider_choices_with_missing_key_fallback(self):
        from console import asr, runtime_env
        with patch.object(runtime_env, 'apply'):
            import server
        handler = object.__new__(server.Handler)
        settings = {**config.DEFAULTS, 'OPENAI_API_KEY': '', 'DEFAULT_LLM': 'jimmy', 'JIMMY_LLM_MODEL': 'fallback-model'}
        with patch.object(config, 'load', return_value=settings), \
                patch.object(config, 'get', side_effect=lambda key: settings.get(key)), \
                patch.object(config, 'jimmy_configured', return_value=True), \
                patch.object(asr, 'available', return_value=False), patch.object(asr, 'engines', return_value=[]), \
                patch.object(runtime_env, 'report', return_value={}):
            status = handler._api('GET', ['status'], {})
        self.assertEqual(status.get('dialogue_provider_routes', {}).get('openai'),
                         {'channel': 'jimmy', 'model': 'fallback-model'})


if __name__ == '__main__':
    unittest.main()
