"""V6a display-only warnings: synthetic data, no files or model calls."""
import copy
import unittest
from console import final_preview


class V6aWarningsTests(unittest.TestCase):
    def fixture(self):
        issues = [
            dict(start=4, end=5, level='high', what='THE MAN末段错位。'),
            dict(start=1, end=3, level='high', what='THE GIRL较长问题！'),
            dict(start=1, end=2, level='high', what='PERSON A较短问题；'),
            dict(start=0, end=1, level='low', what='轻微问题？'),
        ]
        return dict(segments_plan=dict(segments=[dict(id='d01')]), runs=dict(d01=[
            dict(rev='r1', review=dict(verdict='fix', issues=issues)),
            dict(rev='r2', review=dict(verdict='fix', issues=[dict(start=0, end=1, level='high', what='另一版。')]))])), [dict(id='d01', rev='r1')]

    def test_warning_sorts_start_then_end_without_mutating_model_data(self):
        data, chosen = self.fixture()
        before = copy.deepcopy(data)
        warning, = final_preview.review_warnings(data, chosen)
        self.assertLess(warning.index('较短'), warning.index('较长'))
        self.assertLess(warning.index('较长'), warning.index('末段'))
        self.assertNotIn('轻微问题', warning)
        self.assertNotIn('另一版', warning)
        self.assertEqual(data, before)
        # Replacement belongs to HTML rendering; the response stays plain text.
        self.assertIn('THE MAN', warning)
        self.assertNotIn('<img', warning)

    def test_warning_trims_all_terminal_punctuation_before_joining(self):
        data, chosen = self.fixture()
        for punctuation in ('。', '；', '！', '？', '.', ';', '!', '?', '。；！？ ;!? \n', '，,'):
            with self.subTest(punctuation=punctuation):
                for issue in data['runs']['d01'][0]['review']['issues']:
                    issue['what'] = issue['what'].rstrip('。；！？') + punctuation
                warning, = final_preview.review_warnings(data, chosen)
                self.assertIn('PERSON A较短问题；THE GIRL较长问题；THE MAN末段错位，', warning)
                self.assertEqual(warning.count('。'), 1)
                self.assertTrue(warning.endswith('。'))
                self.assertNotRegex(warning, r'[。；！？.!?;，,][；，]')

    def test_acceptance_unchecked_and_selected_version_rules_unchanged(self):
        data, chosen = self.fixture()
        review = data['runs']['d01'][0]['review']
        review['accepted'] = dict(at='now')
        self.assertEqual(final_preview.review_warnings(data, chosen), [])
        review.clear()
        self.assertEqual(final_preview.review_warnings(data, chosen), [])
        chosen[0]['rev'] = 'r2'
        self.assertIn('另一版', final_preview.review_warnings(data, chosen)[0])

    def test_numeric_strings_ties_and_decimal_times(self):
        data, chosen = self.fixture()
        data['runs']['d01'][0]['review']['issues'] = [
            dict(start=a, end=b, level='high', what=text)
            for a, b, text in [('10', '11', '最后。'), ('2', '3', '同起点长。'),
                               ('2', '2.5', '同起点短。'), ('2', '2.5', '同时间第二条。')]]
        warning, = final_preview.review_warnings(data, chosen)
        self.assertIn('同起点短；同时间第二条；同起点长；最后，', warning)


if __name__ == '__main__':
    unittest.main()
