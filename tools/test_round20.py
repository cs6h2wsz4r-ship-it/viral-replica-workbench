"""Round 20: mocked transports, build freshness and user-facing terminology."""
import tempfile
import json
import ast
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.project_fixture import load_project
from console import config, runtime_env, store, kits
with patch.object(runtime_env, 'apply'):
    import server

class BuildTests(unittest.TestCase):
    def test_status_exposes_current_builds_pending_and_official_defaults(self):
        h = object.__new__(server.Handler)
        values = {**config.DEFAULTS, 'OPENAI_API_KEY':'mock-only'}
        with patch.object(config, 'load', return_value=values), patch.object(config, '_parse', return_value={}), \
                patch.object(server.runtime_env, 'report', return_value={}), patch.object(server.asr, 'available', return_value=False), \
                patch.object(server.asr, 'engines', return_value=[]):
            status = h._api('GET', ['status'], {})
            self.assertEqual(status['default_llm'], 'openai')
            self.assertEqual(status['default_image_channel'], 'official')
            self.assertEqual(status['server_build'], server.SERVER_BUILD_AT_START)
            self.assertFalse(status['restart_pending'])
            with patch.object(server, 'read_server_build', return_value='changed'):
                self.assertTrue(h._api('GET', ['status'], {})['restart_pending'])
            values['OPENAI_API_KEY'] = ''
            self.assertEqual(h._api('GET', ['status'], {})['default_llm'], config.DEFAULTS['DEFAULT_LLM'])

    def test_missing_versions_and_get_confirmation_never_mutate(self):
        h = object.__new__(server.Handler)
        h.headers = {}
        h._body_json = lambda: {}
        with patch.object(store, 'create') as create:
            with self.assertRaises(server.ApiError) as err:
                h._api('POST', ['projects'], {})
            self.assertEqual(err.exception.status, 409)
            create.assert_not_called()
        from console import jimmy_bridge
        with patch.object(jimmy_bridge, 'image_confirm') as confirm, patch.object(store, 'load', return_value={}):
            with self.assertRaises(server.ApiError):
                h._api('GET', ['projects', 'fake', 'images', 'image', 'confirm'], {})
            confirm.assert_not_called()

    def test_new_web_page_writes_old_page_rejected_and_python_pending(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp') as tmp:
            root = Path(tmp); web = root / 'web'; web.mkdir()
            page = web / 'app.js'; page.write_text('old')
            code = root / 'server.py'; code.write_text('old')
            with patch.object(server, 'WEB', web), patch.object(server, 'SERVER_FILES', [code], create=True):
                self.assertTrue(callable(getattr(server, 'read_server_build', None)))
                with patch.object(server, 'SERVER_BUILD_AT_START', server.read_server_build()):
                    old = server.build_status()
                    page.write_text('new')
                    new = server.build_status()
                    self.assertEqual(old['server_build'], new['server_build'])
                    self.assertNotEqual(old['web_build'], new['web_build'])
                    h = object.__new__(server.Handler)
                    h._body_json = lambda: {}
                    h.headers = {'X-Server-Build':new['server_build'], 'X-Web-Build':new['web_build']}
                    with patch.object(store, 'create', return_value={'ok':True}):
                        self.assertEqual(h._api('POST',['projects'],{}), {'ok':True})
                        h.headers['X-Web-Build'] = old['web_build']
                        with self.assertRaises(server.ApiError) as err: h._api('POST',['projects'],{})
                        self.assertEqual(err.exception.status,409)
                    code.write_text('changed')
                    self.assertTrue(server.build_status()['restart_pending'])

    def test_noise_files_do_not_change_web_build(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp') as tmp:
            web = Path(tmp)
            with patch.object(server, 'WEB', web):
                baseline = server.read_web_build()
                for name in ('.DS_Store', '.app.js.swp', 'app.js~', 'file.pyc', '#app.js#', 'app.js.tmp'):
                    (web / name).write_text('noise')
                (web / '__pycache__').mkdir()
                (web / '__pycache__/file.py').write_text('noise')
                self.assertEqual(baseline, server.read_web_build())


class PromptAndTextTests(unittest.TestCase):
    def test_unknown_note_only_for_unknown_lines(self):
        for speaker, expected in [('C2', False), ('unknown', True)]:
            result = '\n'.join(kits.speaker_prompt('hello', '', None, has_audio=False,
                dialogue_windows=[{'speaker': speaker, 'onScreen':False, 'text':'hello','start':0,'end':1}]))
            self.assertEqual('For UNKNOWN lines' in result, expected)

    def test_remote_error_translation(self):
        from console.channel_text import user_message, technical_detail, third_party_message
        self.assertEqual(user_message('Jimmy image task reported an unrecognized status'),
                         '三方渠道返回生成中状态，继续等待')
        self.assertNotRegex(user_message('Jimmy image task failed: backend exploded'), r'Jimmy|吉米|backend exploded')
        self.assertEqual(technical_detail('Jimmy image task failed'), 'third-party image task failed')
        self.assertEqual(third_party_message('upload transport failed'), '三方渠道参考素材上传失败，请检查网络后重试')

    def test_user_facing_source_strings(self):
        # Explicit structural whitelist: module/function docstrings, comments,
        # code identifiers, configuration keys and outbound protocol filenames.
        # channel_text's input patterns are translation rules, never UI labels.
        bad = []
        forbidden = re.compile(r'吉米|Jimmy')
        for path in [ROOT / 'server.py', *sorted((ROOT / 'console').rglob('*.py'))]:
            if path.name == 'channel_text.py':
                continue
            tree = ast.parse(path.read_text())
            docs = {id(node.body[0].value) for node in ast.walk(tree)
                    if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)}
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
                    if re.search(r'[\u4e00-\u9fff]', node.value) and forbidden.search(node.value):
                        bad.append(f'{path.name}:{node.lineno}: {node.value}')
        from html.parser import HTMLParser
        class TextParser(HTMLParser):
            def handle_data(self, text):
                if forbidden.search(text): bad.append('HTML text: ' + text)
            def handle_starttag(self, tag, attrs):
                for key, value in attrs:
                    if key in {'title','placeholder','aria-label'} and forbidden.search(value or ''):
                        bad.append('HTML label: ' + value)
        TextParser().feed((ROOT / 'web/index.html').read_text())
        for path in sorted((ROOT / 'web').glob('*.js')):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if line.lstrip().startswith(('//','/*','*', 'function publicText(')): continue
                if re.search(r'[\u4e00-\u9fff]', line) and forbidden.search(line):
                    bad.append(f'{path.name}:{number}: {line.strip()}')
        self.assertEqual(bad, [], '\n'.join(bad))


class LegacyRequestTests(unittest.TestCase):
    def test_third_party_image_job_matches_prechange_bytes(self):
        import importlib.util
        from console import jimmy_bridge
        spec = importlib.util.spec_from_file_location('console._round20_image_baseline',
            ROOT / 'runtime/temp/round20-baseline/console/jimmy_bridge.py')
        baseline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(baseline)
        with tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp') as tmp, \
                patch.object(config, 'PROJECTS_DIR', Path(tmp)), \
                patch.object(config, '_parse', side_effect=AssertionError('禁止配置读取')):
            pid = store.create('image byte compatibility')['id']; directory = store.project_dir(pid)
            (directory / 'assets/image.png').write_bytes(b'offline-reference')
            store.update(pid, lambda d: d['assets'].append({'id':'ref','type':'image','file':'assets/image.png'}))
            bodies, commands = [], []
            def prepare(args, cwd):
                bodies.append(Path(args[args.index('--job') + 1]).read_bytes())
                commands.append(args)
                run = Path(args[args.index('--run') + 1]); run.mkdir(exist_ok=True)
                store.atomic_write_json(run / 'image-job.preview.json', json.loads(bodies[-1]))
                return 'offline'
            with patch.object(store, 'new_id', return_value='img-compat'):
                for module in (baseline, jimmy_bridge):
                    with patch.object(module, '_call', side_effect=prepare):
                        module.image_preview(pid, 'approved prompt', ['ref'], '9:16', 'gpt-image-2', '2k', None)
            self.assertEqual(bodies[0], bodies[1])
            self.assertEqual(commands[0], commands[1])

    def test_both_old_projects_match_pre_round20_bytes(self):
        from tools.test_scene_camera import SceneCameraTests
        from console import jimmy_bridge, llm
        with tempfile.TemporaryDirectory(dir=ROOT / 'runtime/temp') as tmp, \
                patch.object(config, 'PROJECTS_DIR', Path(tmp)), \
                patch.object(config, '_parse', side_effect=AssertionError('禁止配置读取')), \
                patch.object(llm, 'chat_json', side_effect=AssertionError('禁止模型调用')), \
                patch.object(jimmy_bridge, '_call', side_effect=AssertionError('必须mock三方脚本')):
            self.pid = store.create('round20 compatibility')['id']
            self.directory = store.project_dir(self.pid)
            for pid in ('p1027232428-166a', 'p1027195039-ccc0'):
                original = load_project(pid)
                SceneCameraTests._assert_legacy_compatibility(self, original, 'round20-compatibility-' + pid + '.json', baseline_round='round20')



if __name__ == '__main__':
    from tools.run_tests_offline import audit
    sys.addaudithook(audit)
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in
        ('tools.test_round20', 'tools.test_round20_llm', 'tools.test_round20_image'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
