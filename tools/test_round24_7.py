"""24.7: contractions, simple proposals and unblocked adoption; all I/O offline."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import evidence, gateway, pipeline, prompts, store
from tools import test_round24_6 as fixtures
from tools import test_script_rules as script_fixtures


COUNTS = {"It's knit and comfortable.": 7, "It's so flowy and comfortable.": 9,
          "don't": 1, "I'm": 1, "we're": 1, "you'll": 1, "isn't": 1,
          "Excuse me, your dress is beautiful.": 9, "I love the open back.": 6,
          "make love table coffee rhythm": 7, "中文 １２٣4": 6}


class Round24_7Tests(unittest.TestCase):
    setUp = fixtures.Round24_6Tests.setUp
    response = fixtures.Round24_6Tests.response
    generate = fixtures.Round24_6Tests.generate

    def test_contractions_and_duration_consumers_keep_same_rates(self):
        for text, count in COUNTS.items():
            for sample in {text, text.replace("'", '’')}:
                with self.subTest(text=sample):
                    self.assertEqual(evidence.speech_units(sample, 'en'), count)
                    for lang, rates in evidence.PACE_RATE.items():
                        self.assertAlmostEqual(evidence.estimate_seconds(sample, lang, 'normal', .2),
                                               count / rates['normal'] + .2)

    def test_screenshot_and_very_long_proposals_can_be_saved_and_adopted(self):
        for text in ("It's knit and comfortable.", 'It is so wonderfully beautiful and comfortable for every single day.'):
            with self.subTest(text=text):
                response = self.response(); response['lines'][2]['proposed'] = text
                proposal, _ = self.generate(response)
                self.assertNotIn('too_long', proposal['lines'][2])
                self.assertEqual(proposal['lines'][2]['proposed'], text)
                # Real adoption + save path, with only the separate model safety pass mocked.
                store.update(self.pid, lambda d: d['script']['tasks'][0].update(ratio='9:16', duration=15))
                lines = copy.deepcopy(proposal['lines'])
                lines[2].update(too_long=True, units=999, max_units=0, seconds=999, max_seconds=0)
                with patch.object(pipeline, 'apply_replacement_safety'):
                    adopted = pipeline.adopt_dialogue_proposal(self.pid, 'seg-01', lines)
                task = adopted['script']['tasks'][0]
                self.assertEqual(task['dialogue_choice'], 'adopt')
                self.assertIn(text, task['dialogue'])
                self.assertEqual(task['dialogue_lines'][2]['text'], text)
                self.assertNotIn('too_long', task['dialogue_proposal']['lines'][2])
                self.assertEqual(store.load(self.pid)['script'], adopted['script'])

    def test_prompt_and_payload_have_no_length_fields_even_from_legacy_feedback(self):
        proposal, _ = self.generate()
        legacy = copy.deepcopy(proposal)
        for row in legacy['lines']:
            row.update(spokenSeconds=1, originalUnits=10, maxUnits=7, units=99, too_long=True,
                       max_units=7, max_seconds=1, seconds=99, model_units=99)
        with patch.object(gateway, 'reason_checked', return_value=(self.response(), {'model': 'mock'})) as model:
            pipeline.dialogue_proposal(self.pid, 'seg-01', previous_proposal=legacy)
        messages = model.call_args.args[1]
        payload = json.loads(messages[1]['content'])
        self.assertEqual([r['mustKeep'] for r in payload['lines']], [False, True, False, False, True])
        self.assertEqual(payload['lines'][2]['conflictTerms'], ['flowy'])
        self.assertEqual(payload['previous_proposal']['lines'][2]['proposed'], legacy['lines'][2]['proposed'])
        for field in ('spokenSeconds', 'originalUnits', 'maxUnits', 'max_units', 'max_seconds',
                      'too_long', 'model_units', 'units'):
            self.assertNotIn('"' + field + '"', messages[1]['content'])
        prompt = messages[0]['content']
        self.assertEqual(prompt, prompts.DIALOGUE_PROPOSAL_SYSTEM)
        for token in ('spokenSeconds', 'originalUnits', 'maxUnits', 'units', '[aeiouy]', '逐句自查', '1.15'):
            self.assertNotIn(token, prompt)
        for rule in ('改写句的长度和原句差不多，不要比原句更长', '把什么换成了什么、依据是什么',
                     'mustKeep=true', 'conflictTerms', '只替换冲突词所在的部分'):
            self.assertIn(rule, prompt)


class Round24_7ScriptDurationTests(unittest.TestCase):
    setUp = script_fixtures.ScriptRulesTests.setUp

    def test_script_estimate_uses_corrected_contractions_without_moving_source_window(self):
        for text in ("It's knit and comfortable.", 'It’s knit and comfortable.'):
            with self.subTest(text=text):
                transcript = {'language': 'en', 'segments': [dict(start=0, end=5, text=text,
                              words=[dict(start=0, end=5, word=text)])]}
                script = script_fixtures.ScriptRulesTests.run_script(
                    self, [script_fixtures.segment(0, 5)], transcript=transcript)
                task = script['tasks'][0]
                self.assertEqual(task['dialogue'], text)
                self.assertEqual(task['estimate'], 1.52)  # 7 / 4.6, rounded to two places.
                self.assertEqual((task['source_start'], task['source_end'], task['duration']), (0, 5, 5))


if __name__ == '__main__':
    unittest.main()
