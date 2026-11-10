"""Generic request contracts built from empty content and synthetic local media."""
import ast
import copy
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import (cast, config, director, gateway, jimmy_bridge, llm, motion,
                     prompts, scene_prompt, segment_boards, segment_plan, segment_review, store,
                     storyboard, storyboard_edits as edits, storyboard_images)


BANNED = re.compile(
    r'佩戴|按压|家人|手表|手环|表盘|腕|SOS|婴儿车|推车|父亲|儿子|父子|老人|SENVA|'
    r'\b(?:watch|wrist|dial|bracelet|stroller|father|son)\b|12\.3 秒|19\.4 秒', re.I)


def text_only(value):
    """Exclude image data/paths, keep every actual text message and prompt."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return '\n'.join(text_only(v) for v in value)
    if isinstance(value, dict):
        if value.get('type') == 'image_url':
            return ''
        return text_only(value.get('content', value.get('text', '')))
    return ''


class GenericPromptTests(unittest.TestCase):
    def setUp(self):
        root = ROOT / 'runtime/temp'
        root.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=root)))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.tmp))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('no secret reads')))
        self.enterContext(patch.object(config, 'load', return_value={**config.DEFAULTS, 'OPENAI_API_KEY': 'mock-only'}))
        self.enterContext(patch.object(gateway, 'reason_checked', side_effect=AssertionError('mock required')))
        self.pid = store.create('')['id']
        self.directory = store.project_dir(self.pid)

    def assert_generic(self, text):
        self.assertIsNone(BANNED.search(text), BANNED.findall(text))

    def fixture(self):
        folder = self.directory / 'analysis/storyboard'
        folder.mkdir(parents=True, exist_ok=True)
        cells = []
        for i in range(2):
            file = f'analysis/storyboard/cell-{i+1:02d}.jpg'
            Image.new('RGB', (90, 160), 'gray').save(self.directory / file)
            cells.append(dict(id=f'c{i+1:02d}', index=i+1, start=i*2, end=(i+1)*2,
                              file=file, characters=['C1', 'C2'], product='', product_later=bool(i), action=''))
        assets = []
        for key in ('C1', 'C2', 'product'):
            file = f'assets/{key}.png'
            Image.new('RGB', (50, 70), 'gray').save(self.directory / file)
            assets.append(dict(id=key, type='image', file=file))
        store.update(self.pid, lambda d: d.update(
            assets=assets, breakdown={'characters': [dict(id=k, name='') for k in ('C1', 'C2')]},
            replacements=dict(characters=[dict(source_id=k, action='replace', asset_id=k, description='')
                                           for k in ('C1', 'C2')],
                              product=dict(action='replace', asset_id='product', description='')),
            storyboard=dict(cells=cells, sheets=storyboard.render_sheets(cells, folder))))
        answer = dict(segments=[dict(id='d01', cells=['c01', 'c02'], mode='image', refs=['C1', 'C2', 'product'], gen_images=['c01', 'C1', 'C2', 'product'])])
        with patch.object(gateway, 'reason_checked', return_value=(answer, {})):
            segment_plan.generate(self.pid)
        answer = dict(summary='', cells=[dict(cell=c['id'], changes='', keep='') for c in cells],
                      remove_text=True, questions=[])
        with patch.object(gateway, 'reason_checked', return_value=(answer, {})):
            edits.generate_plan(self.pid, 'd01', {})
        preview = edits.preview(self.pid, 'd01', {})
        storyboard_images.complete(self.pid, preview, Path(preview['storyboard_context']['references'][0]['path']))
        return store.load(self.pid), preview

    def test_empty_content_full_requests_and_image_templates(self):
        data, preview = self.fixture()
        materials = edits.materials_for(data, 'd01', self.directory)
        with patch.object(gateway, 'reason_checked', return_value=({'changes':'按本次要求调整画面','keep':'','why':'已整理这一镜的改法'}, {})):
            single = edits.preview(self.pid, 'd01', dict(cell_id='c01', request='商品'))
        texts = {
            'segment_plan.SYSTEM': segment_plan.SYSTEM,
            'director.SYSTEM': director.SYSTEM,
            'storyboard_edits.SYSTEM': edits.SYSTEM,
            'segment_review.SYSTEM': segment_review.SYSTEM,
            'segment_plan.messages': text_only(segment_plan.messages(data, self.directory)),
            'director.messages': text_only(director.messages(data, self.directory)),
            'director.rewrite': text_only(director.messages(data, self.directory, target=dict(id='d01', prompt='x'*5001,needs_rewrite=True),review_context={'issues':[{'what':'<product> 状态不一致','fix':'redraw_cell','cell':'c01'}],'prompt':'图1 <person> 和 <product>'})),
            'edits.plan_messages': text_only(edits.plan_messages(data, 'd01', '', materials, self.directory)),
            'edits.request_template': edits.request_template(data, 'd01', materials, self.directory),
            'image.full': preview['preview']['prompt'],
            'image.cell': single['preview']['prompt'],
        }
        self.assertEqual(preview['preview']['model'], 'gpt-image-2')
        self.assertEqual(preview['preview']['size'], '1024x1536')
        for mode in ('image', 'video'):
            copied = copy.deepcopy(data)
            copied['segments_plan']['segments'][0]['mode'] = mode
            texts['materials.'+mode] = text_only(director.messages(copied, self.directory))
        # The legacy sheet and text-retaining branches also use generic templates.
        legacy = copy.deepcopy(data)
        legacy.pop('segments_plan'); legacy.pop('segment_boards')
        sid = legacy['storyboard']['sheets'][0]['id']
        legacy['storyboard_edits'] = {'sheets': {sid: copy.deepcopy(edits.state_for(data, 'd01'))}}
        legacy['storyboard_edits']['sheets'][sid]['plan']['remove_text'] = False
        texts['image.legacy'] = edits.image_prompt(legacy, sid, preview['storyboard_context']['references'])
        for name, text in texts.items():
            with self.subTest(request=name):
                self.assert_generic(text)
        self.assertIn('PERSON A', texts['image.full'])
        self.assertIn('PERSON B', texts['materials.video'])
        self.assertIn('视频1', texts['materials.video'])

    def test_other_fixed_prompt_templates_are_generic(self):
        # Scan the remaining prompt-bearing literals as well as the runtime
        # builders; data-dependent name translations are intentionally not templates.
        for module in (prompts,):
            for key, value in vars(module).items():
                if key.isupper() and isinstance(value, str):
                    with self.subTest(module=module.__name__, constant=key):
                        self.assert_generic(value)
        for module in (segment_boards, storyboard_images, segment_review, gateway.routes):
            tree = ast.parse(Path(module.__file__).read_text())
            for literal in ast.walk(tree):
                if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
                    with self.subTest(module=module.__name__, line=literal.lineno):
                        self.assert_generic(literal.value)

        for module, function in ((motion, 'compare_actions'), (scene_prompt, 'compose'),
                                 (jimmy_bridge, 'director_preview'), (jimmy_bridge, 'compose_prompt'),
                                 (jimmy_bridge, 'image_preview')):
            tree = ast.parse(Path(module.__file__).read_text())
            node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function)
            for literal in ast.walk(node):
                if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
                    with self.subTest(module=module.__name__, line=literal.lineno):
                        self.assert_generic(literal.value)

    def test_risk_self_checks_are_generic_and_applied_without_analysis_output(self):
        for system in (segment_plan.SYSTEM, edits.SYSTEM, director.SYSTEM):
            self.assert_generic(system)
            for fragment in ('动笔前', '风险', '功能和状态', '时间点', '台词或反应', '旧包装或文字', '不单独输出分析过程'):
                self.assertIn(fragment, system)
        self.assertIn('提示词和 check', director.SYSTEM)
        for fragment in ('清楚可见', '无论大小', 'mode_reason', '动作的价值和漏回风险'):
            self.assertIn(fragment, segment_plan.SYSTEM)
        self.assertNotIn('仅小面积出现且替换风险低时，可以用 video', segment_plan.SYSTEM)

    def test_old_dialogue_and_age_examples_use_placeholders(self):
        for key, value in vars(prompts).items():
            if key.isupper() and isinstance(value, str):
                with self.subTest(constant=key):
                    for example in ('三十九', '6.15–8.1', 'the slit', "'good'", "'obsessed'", 'The back', '72-year-old'):
                        self.assertNotIn(example, value)
        self.assertIn('<原片台词词语>', prompts.EVIDENCE_RULES)
        self.assertIn('<quoted source words>', prompts.SCRIPT_SYSTEM)
        self.assertIn('<age>-year-old', prompts.CAMERA_PRODUCT_USE)

    def test_project_specific_content_is_preserved_as_input(self):
        data, _ = self.fixture()
        description = '用户商品：手表、手环；watch bracelet'
        data['replacements']['product']['description'] = description
        self.assertIn(description, text_only(segment_plan.messages(data, self.directory)))
        self.assertIn(description, text_only(director.messages(data, self.directory)))
        materials = edits.materials_for(data, 'd01', self.directory)
        self.assertIn(description, text_only(edits.plan_messages(data, 'd01', '', materials, self.directory)))

    def test_cast_requests_have_no_fixed_product_story(self):
        self.fixture()
        with patch.object(gateway, 'reason_checked', return_value=({'checks': []}, {})) as reason:
            cast.check_story(self.pid, {})
        self.assert_generic(text_only(reason.call_args.args[1]))
        with patch.object(gateway, 'reason_checked', return_value=(dict.fromkeys(cast.PRODUCT_KEYS, '外形'), {})) as reason:
            cast.identify(self.pid, dict(kind='product'))
        self.assert_generic(text_only(reason.call_args.args[1]))

    def test_word_boundary_does_not_match_person_reason_or_personal(self):
        self.assert_generic('person reason personal personality')
        for word in ('佩戴', '按压', '家人', '父子', 'son', 'SON', 'father', "father's", 'Watch', '12.3 秒', '19.4 秒'):
            with self.subTest(word=word):
                self.assertIsNotNone(BANNED.search(word))

    def test_cell_product_reference_matches_generic_words_and_step03_name(self):
        self.fixture()
        for request, product_fields, expected in (
                ('商品', {}, True), ('产品', {}, True), ('PRODUCT', {}, True),
                ('调整陶瓷杯的位置', {'name': '陶瓷杯'}, True),
                ('调整收纳盒的位置', {'look': {'category': '收纳盒'}}, True),
                ('摆正a+b', {'name': 'A+B'}, True),
                ('摆正AAB', {'name': 'A+B'}, False),
                ('手表', {}, False), ('手环', {}, False), ('调整背景', {}, False)):
            with self.subTest(request=request, fields=product_fields):
                def update(data):
                    data['replacements']['product'] = dict(action='replace', asset_id='product', description='', **product_fields)
                store.update(self.pid, update)
                with patch.object(gateway,'reason_checked',return_value=({'changes':'按本次要求调整画面','keep':'','why':'已整理这一镜的改法'},{})):
                    rec = edits.preview(self.pid, 'd01', dict(cell_id='c01', request=request))
                keys = [r['key'] for r in rec['storyboard_context']['references']]
                self.assertEqual('product' in keys, expected)
                self.assertEqual(keys[0], 'current')
                self.assertNotIn('original', keys)


if __name__ == '__main__':
    unittest.main()
