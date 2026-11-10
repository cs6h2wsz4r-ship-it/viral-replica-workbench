"""Frozen project inputs and fail-closed, checksum-verified local media copies."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PID = 'p1029100000-r18v'
REL = 'assets/frame-C1-0.50.jpg'


class Round24FixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp')))

    def test_fixture_is_clean_and_records_exact_removals(self):
        manifest = json.loads((ROOT / 'tools/fixtures/round24/manifest.json').read_text())
        from tools.project_fixture import load_project
        data = load_project(PID)
        self.assertFalse(any('derived_from' in asset for asset in data['assets']))
        self.assertNotIn('img1029120047-7469', data['images'])
        entry = manifest['projects'][PID]
        self.assertEqual(entry['removed_asset_ids'], ['a1029120031-ac7d'])
        self.assertEqual(entry['removed_image_ids'], ['img1029120047-7469'])
        self.assertEqual(len(manifest['projects']), 5)
        for pid, entry in manifest['projects'].items():
            raw = (ROOT / 'tools/fixtures/round24' / pid / 'project.json').read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), entry['fixture_sha256'])
            if pid != PID:
                self.assertEqual(entry['source_project_sha256'], entry['fixture_sha256'])

    def test_polluted_source_json_does_not_change_frozen_input(self):
        from tools.project_fixture import copy_media, load_project
        baseline = load_project(PID)
        source = self.temp / 'source-copy'
        copy_media(PID, source, [REL], source_dir=ROOT / 'projects' / PID)
        polluted = load_project(PID)
        polluted['assets'].append({'id': 'round24-fake', 'derived_from': 'fake', 'file': 'assets/fake.png'})
        (source / 'project.json').write_text(json.dumps(polluted))
        with patch.dict(os.environ, {'ROUND24_MEDIA_SOURCE_DIR': str(source)}):
            self.assertEqual(load_project(PID), baseline)
            copy_media(PID, self.temp / 'destination', [REL])
        self.assertEqual((self.temp / 'destination' / REL).read_bytes(), (source / REL).read_bytes())

    def test_default_and_override_media_are_identical(self):
        from tools.project_fixture import copy_media, media_source_dir
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(media_source_dir(PID), ROOT / 'projects' / PID)
            copy_media(PID, self.temp / 'default', [REL])
        with patch.dict(os.environ, {'ROUND24_MEDIA_SOURCE_DIR': str(self.temp / 'default')}):
            copy_media(PID, self.temp / 'override', [REL])
            self.assertEqual(media_source_dir('p1027195039-ccc0'), ROOT / 'projects/p1027195039-ccc0')
        self.assertEqual((self.temp / 'default' / REL).read_bytes(), (self.temp / 'override' / REL).read_bytes())

    def test_hash_mismatch_fails_before_destination_is_written(self):
        from tools.project_fixture import copy_media
        source = self.temp / 'bad-source'
        (source / REL).parent.mkdir(parents=True)
        (source / REL).write_bytes(b'corrupted media')
        with self.assertRaisesRegex(ValueError, 'SHA256 mismatch') as error:
            copy_media(PID, self.temp / 'destination', [REL], source_dir=source)
        message = str(error.exception)
        expected = json.loads((ROOT / 'tools/fixtures/round24/manifest.json').read_text())['projects'][PID]['media_sha256'][REL]
        for value in [PID, REL, expected, hashlib.sha256(b'corrupted media').hexdigest()]:
            self.assertIn(value, message)
        self.assertFalse((self.temp / 'destination' / REL).exists())

    def test_missing_media_fails_with_expected_digest_and_path(self):
        from tools.project_fixture import copy_media
        with self.assertRaisesRegex(FileNotFoundError, 'actual=missing') as error:
            copy_media(PID, self.temp / 'destination', [REL], source_dir=self.temp / 'missing-source')
        for value in [PID, REL, 'expected=']:
            self.assertIn(value, str(error.exception))
        self.assertFalse((self.temp / 'destination' / REL).exists())

    def test_copy_rejects_unrecorded_media_and_non_temporary_destination(self):
        from tools.project_fixture import copy_media
        with self.assertRaisesRegex(ValueError, 'not recorded'):
            copy_media(PID, self.temp / 'destination', ['project.json'])
        with self.assertRaisesRegex(ValueError, 'runtime/temp'):
            copy_media(PID, ROOT / 'tools/fixtures/round24/rejected', [REL])
        self.assertFalse((ROOT / 'tools/fixtures/round24/rejected').exists())

    def test_each_load_is_independent(self):
        from tools.project_fixture import load_project
        original = load_project(PID)
        changed = load_project(PID)
        changed['assets'].clear()
        self.assertEqual(load_project(PID), original)


if __name__ == '__main__':
    from tools.run_tests_offline import audit
    sys.dont_write_bytecode = True
    sys.addaudithook(audit)
    unittest.main(verbosity=2)
