"""24.6 per-line rewrite constraints; temporary storage and mocked models only."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import config, gateway, pipeline, prompts, replacement_guard, store


class Round24_6Tests(unittest.TestCase):
    def setUp(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp')))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', folder))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('禁止读取密钥')))
        self.enterContext(patch.object(config, 'load', return_value=dict(config.DEFAULTS)))
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('禁止真实请求')))
        self.pid = store.create('24.6 逐句台词')['id']
        self.texts = ['Excuse me, your dress is beautiful.', 'Oh, thank you so much.',
                      "It's so flowy and comfortable.", 'I love the open back.',
                      'The fabric is perfect for this weather.']
        self.source = [dict(start=i * 3, end=i * 3 + [1.82, 2, 1.36, .82, 2.5][i],
                            original=text, speaker='C2' if i == 0 else 'C1',
                            onScreen=i != 0, evidence='原片逐句证据') for i, text in enumerate(self.texts)]
        self.task = dict(id='seg-01', language='en', dialogue='\n'.join(self.texts), source_start=0, source_end=15)
        self.data = dict(script={'tasks': [self.task]}, replacements={'product': {'action': 'replace', 'description': '新套装'}},
                         breakdown={'overview': {'language': 'en'}, 'productMoments': [{'description': 'flowy dress with open back'}]})
        store.update(self.pid, lambda d: d.update(copy.deepcopy(self.data)))
        self.enterContext(patch.object(pipeline, '_dialogue_source', return_value=copy.deepcopy(self.source)))

    def response(self):
        texts = ['Your look is nice.', self.texts[1], 'It feels nice.', 'Love this.', self.texts[4]]
        return {'language': 'en', 'lines': [dict(row, proposed=text, changed=text != row['original'], reason='按新商品改写')
                                          for row, text in zip(self.source, texts)]}

    def generate(self, response=None):
        with patch.object(gateway, 'reason_checked', return_value=(response or self.response(), {'model': 'mock', 'provider': 'deepseek'})) as model:
            proposal = pipeline.dialogue_proposal(self.pid, 'seg-01')
        return proposal, model

    def test_payload_per_line_permissions_terms_and_prompt(self):
        _, model = self.generate()
        messages = model.call_args.args[1]
        payload = json.loads(messages[1]['content'])
        rows = payload['lines']
        self.assertEqual([r.get('mustKeep') for r in rows], [False, True, False, False, True])
        for i, row in enumerate(rows):
            expected = not bool(replacement_guard.dialogue_mismatches(self.data, {**self.task, 'dialogue': row['original'], 'dialogue_choice': None}))
            self.assertEqual(row['mustKeep'], expected)
            self.assertNotIn('maxUnits', row)
            self.assertNotIn('spokenSeconds', row)
            self.assertNotIn('originalUnits', row)
        self.assertEqual([r.get('conflictTerms', []) for r in rows], [['dress'], [], ['flowy'], ['open back'], []])
        for rule in ('mustKeep=true', 'mustKeep=false', 'proposed', 'original', '逐字相同', 'changed=false', 'conflictTerms', '只替换冲突词所在的部分'):
            self.assertIn(rule, messages[0]['content'])
        self.assertEqual(messages[0]['content'], prompts.DIALOGUE_PROPOSAL_SYSTEM)

    def test_model_changes_fifth_line_restored_and_saved_without_losing_valid_edits(self):
        response = self.response()
        response['lines'][4].update(proposed='This fabric is great for warm days.', changed=True)
        proposal, model = self.generate(response)
        self.assertEqual(model.call_count, 1)
        self.assertEqual([r['proposed'] for r in proposal['lines'][:4]], [r['proposed'] for r in response['lines'][:4]])
        last = proposal['lines'][4]
        self.assertEqual(last['proposed'], self.texts[4])
        self.assertIs(last['changed'], False)
        self.assertIs(last.get('restored'), True)
        self.assertNotIn('too_long', last)
        self.assertEqual(store.load(self.pid)['script']['tasks'][0]['dialogue_proposal'], proposal)
        with patch.object(pipeline, 'save_script', side_effect=lambda pid, script: {'script': script}):
            adopted = pipeline.adopt_dialogue_proposal(self.pid, 'seg-01')
        self.assertTrue(adopted['script']['tasks'][0]['dialogue_proposal']['lines'][4]['restored'])

    def test_both_keep_lines_restored_and_forged_permissions_ignored(self):
        response = self.response()
        for i in (1, 4):
            response['lines'][i].update(proposed='Nice!', mustKeep=False, restored=False)
        proposal, _ = self.generate(response)
        for i in (1, 4):
            self.assertEqual(proposal['lines'][i]['proposed'], self.texts[i])
            self.assertTrue(proposal['lines'][i]['restored'])
            self.assertFalse(proposal['lines'][i]['changed'])
        self.assertFalse(proposal['lines'][0].get('restored', False))

    def test_strict_errors_identify_sentence_original_and_problem_and_do_not_save(self):
        mutations = [
            (lambda r: r['lines'][2].update(start=99), 2, '时间'),
            (lambda r: r['lines'][2].update(original='fake'), 2, '原句'),
            (lambda r: r['lines'].pop(), 4, '数量'),
            (lambda r: r['lines'].append(copy.deepcopy(r['lines'][-1])), 4, '数量'),
            (lambda r: r['lines'][1].update(proposed=''), 1, '不能为空'),
            (lambda r: r['lines'][1].update(proposed='谢谢你'), 1, '原片语言'),
            (lambda r: r.update(language='zh'), 0, '原片语言'),
        ]
        for change, index, problem in mutations:
            with self.subTest(problem=problem, index=index):
                response = self.response(); change(response)
                before = (store.project_dir(self.pid) / 'project.json').read_bytes()
                with self.assertRaises(ValueError) as caught:
                    self.generate(response)
                message = str(caught.exception)
                self.assertIn(f'第 {index + 1} 句', message)
                self.assertIn(self.texts[index], message)
                self.assertIn(problem, message)
                self.assertEqual((store.project_dir(self.pid) / 'project.json').read_bytes(), before)

    def test_model_metadata_changes_are_ignored_and_original_attribution_kept(self):
        response = self.response()
        response['lines'][0].update(speaker='C1', onScreen=True, evidence='捏造证据')
        proposal, _ = self.generate(response)
        self.assertEqual(proposal['lines'][0]['speaker'], self.source[0]['speaker'])
        self.assertEqual(proposal['lines'][0]['onScreen'], self.source[0]['onScreen'])
        self.assertEqual(proposal['lines'][0]['evidence'], self.source[0]['evidence'])

    def test_conflict_term_aliases_use_actual_sentence_match(self):
        source = copy.deepcopy(self.source)
        source[0]['original'] = 'Your gown is beautiful.'
        with patch.object(pipeline, '_dialogue_source', return_value=source):
            response = self.response(); response['lines'][0]['original'] = source[0]['original']
            _, model = self.generate(response)
        row = json.loads(model.call_args.args[1][1]['content'])['lines'][0]
        self.assertFalse(row['mustKeep'])
        self.assertEqual(row['conflictTerms'], ['gown'])


if __name__ == '__main__':
    unittest.main()
