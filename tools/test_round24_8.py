"""24.8: preserve optimistic concurrency checks; model/storage I/O stays offline."""
import copy
import unittest
from unittest.mock import patch

from console import pipeline, store
from tools import test_round24_6 as fixtures


class Round24_8Tests(unittest.TestCase):
    setUp = fixtures.Round24_6Tests.setUp

    def test_concurrent_script_or_replacement_save_still_rejected_without_overwrite(self):
        for changed in ('script', 'replacements'):
            with self.subTest(changed=changed):
                store.update(self.pid, lambda d: d.update(copy.deepcopy(self.data)))
                store.update(self.pid, lambda d: d['script']['tasks'][0].update(duration=15, ratio='9:16'))
                submitted = copy.deepcopy(store.load(self.pid)['script'])
                submitted['tasks'][0]['title'] = '本次提交'

                def concurrent_save(*args):
                    def commit(data):
                        data[changed]['concurrent_marker'] = '另一次保存'
                    store.update(self.pid, commit)

                with patch.object(pipeline, 'apply_replacement_safety', side_effect=concurrent_save) as safety:
                    with self.assertRaisesRegex(ValueError, '刚才有另一次保存先完成了，请刷新到最新内容，检查后再操作'):
                        pipeline.save_script(self.pid, submitted)
                self.assertEqual(safety.call_count, 1)
                latest = store.load(self.pid)
                self.assertEqual(latest[changed]['concurrent_marker'], '另一次保存')
                self.assertNotIn('title', latest['script']['tasks'][0])


if __name__ == '__main__':
    unittest.main()
