"""SB3b regressions: synthetic projects, local files, mocked model replies only."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import test_director_sb3 as fixtures
from console import config, director, gateway, jimmy_bridge, llm, store
from console.gateway import reasoning
from console.gateway.errors import CheckedLLMError


class DirectorSB3bTests(unittest.TestCase):
    # Reuse fixture helpers without collecting the old test class a second time.
    setUp = fixtures.DirectorSB3Tests.setUp
    fixture = fixtures.DirectorSB3Tests.fixture
    segment = fixtures.DirectorSB3Tests.segment
    plan = fixtures.DirectorSB3Tests.plan
    generated = fixtures.DirectorSB3Tests.generated

    def responses(self, kind='full'):
        return sorted((self.d/'analysis/director/responses').glob(f'*-{kind}.json'))

    def invoke(self, action, value):
        with patch.object(gateway, 'reason_checked', return_value=(value, {})):
            return (director.generate(self.pid) if action == 'generate' else
                    director.save(self.pid, {'action':'rewrite', 'segment_id':'d1'}))

    def test_reply_saved_before_validation_for_generation_and_rewrite(self):
        data = self.generated(2)
        for action, kind in (('generate', 'full'), ('rewrite', 'd1')):
            value = self.plan(data) if action == 'generate' else copy.deepcopy(data['director']['segments'][0])
            before = set(self.responses(kind))
            original = director.validate_plan
            def validate(*args, **kw):
                files = set(self.responses(kind)) - before
                self.assertEqual(len(files), 1, 'reply must already exist before validation')
                self.assertEqual(json.loads(files.pop().read_text()), value)
                return original(*args, **kw)
            with patch.object(director, 'validate_plan', side_effect=validate):
                self.invoke(action, value)

    def test_structural_failure_preserves_plan_and_logs_reason_and_archive(self):
        data = self.generated(4, groups=[['c01','c02'], ['c03','c04']])
        for action, kind in (('generate', 'full'), ('rewrite', 'd1')):
            value = self.plan(data) if action == 'generate' else copy.deepcopy(data['director']['segments'][0])
            (value['segments'][0] if action == 'generate' else value)['id'] = 'unknown'
            before = set(self.responses(kind))
            with self.assertRaisesRegex(ValueError, '导演方案不完整：') as raised:
                self.invoke(action, value)
            new = set(self.responses(kind)) - before
            self.assertEqual(len(new), 1)
            path = new.pop()
            self.assertEqual(json.loads(path.read_text()), value)
            after = store.load(self.pid)
            self.assertEqual(after['director'], data['director'])
            self.assertIn(str(path.relative_to(self.d)), after['log'][-1]['msg'])
            self.assertIn(str(raised.exception), after['log'][-1]['msg'])

    def test_parse_failures_and_repair_replies_are_all_archived(self):
        data = self.generated(2)
        for action, kind in (('generate', 'full'), ('rewrite', 'd1')):
            for repaired in (False, True):
                with self.subTest(action=action, repaired=repaired):
                    value = self.plan(data) if action == 'generate' else copy.deepcopy(data['director']['segments'][0])
                    raw = 'unparsed original reply: { broken JSON'
                    second = json.dumps(value) if repaired else 'second broken reply'
                    before = set(self.responses(kind))
                    def checked(step, messages, **kw):
                        kw.pop('project')
                        return reasoning.chat_json('custom', messages, **kw)
                    with patch.object(gateway, 'reason_checked', side_effect=checked), patch.object(
                            llm, 'chat', side_effect=[{'content':raw}, {'content':second}]) as chat:
                        if repaired:
                            if action == 'generate': director.generate(self.pid)
                            else: director.save(self.pid, {'action':'rewrite', 'segment_id':'d1'})
                        else:
                            with self.assertRaisesRegex(ValueError, '导演方案不完整：.*JSON'):
                                if action == 'generate': director.generate(self.pid)
                                else: director.save(self.pid, {'action':'rewrite', 'segment_id':'d1'})
                    self.assertEqual(chat.call_count, 2)
                    files = sorted(set(self.responses(kind)) - before)
                    self.assertEqual(len(files), 2)
                    self.assertEqual(files[0].read_text(), raw)
                    if repaired: self.assertEqual(json.loads(files[1].read_text()), value)
                    else: self.assertEqual(files[1].read_text(), second)
                    log = '\n'.join(row['msg'] for row in store.load(self.pid)['log'])
                    self.assertIn(str(files[0].relative_to(self.d)), log)
                    self.assertIn('JSON', log)
                    self.assertNotIn('on_response', chat.call_args.kwargs, 'observer must not reach transport')

    def test_model_free_brief_is_ignored_in_favor_of_structured_fields(self):
        data=self.fixture(2)
        for legacy in ({'z':'First','a':['Second']},['First',{'section':'Second'}]):
            value=self.plan(data);value['segments'][0]['brief']=legacy
            saved=self.invoke('generate',value);row=saved['director']['segments'][0]
            self.assertEqual(row['prompt'],value['segments'][0]['prompt'])
            self.assertNotIn('brief',row);self.assertNotIn('replace_keep',row)
            self.assertFalse(row['needs_rewrite']);self.assertEqual(row['warnings'],[])

    def test_response_observer_traverses_real_checked_gateway_without_transport_kwargs(self):
        data = self.fixture(2); value = self.plan(data)
        raw = 'malformed { first reply'
        with patch.object(gateway.routes, 'resolve', return_value={'channel':'custom', 'model':'synthetic'}), \
                patch.object(llm, '_chat', side_effect=[{'content':raw}, {'content':json.dumps(value)}]) as transport:
            saved = director.generate(self.pid)
        self.assertIn(value['segments'][0]['prompt'], saved['director']['segments'][0]['prompt'])
        files = self.responses()
        self.assertEqual(len(files), 2)
        self.assertEqual(files[0].read_text(), raw)
        self.assertEqual(json.loads(files[1].read_text()), value)
        self.assertEqual(transport.call_count, 2)
        for call in transport.call_args_list:
            self.assertNotIn('on_response', call.kwargs)

    def test_empty_transport_replies_are_archived_before_checked_retries(self):
        self.fixture(2)
        settings = {'base_url':'https://offline.invalid', 'api_key':'offline-placeholder',
                    'model':'synthetic', 'timeout':1}
        for channel, model, content, exhausted in (
                ('custom', 'synthetic', '', False), ('custom', 'synthetic', ' \n ', False),
                ('custom', 'synthetic', None, True), ('jimmy', 'gemini-synthetic', ' \n ', False)):
            with self.subTest(channel=channel, content=content, exhausted=exhausted):
                before = set(self.responses())
                if channel == 'jimmy':
                    payload = {'code':0, 'data':{'candidates':[{'content':{'parts':[{'text':content}]}}]}}
                else:
                    payload = {'choices':[{'message':{'content':content}, 'finish_reason':'length' if exhausted else 'stop'}]}
                with patch.object(gateway.routes, 'resolve', return_value={'channel':channel, 'model':model}), \
                        patch.object(config, 'provider_settings', return_value={**settings, 'model':model}), \
                        patch.object(llm, '_jimmy_dns_shim'), patch.object(llm, '_post', return_value=payload) as post:
                    with self.assertRaises(CheckedLLMError):
                        director.generate(self.pid)
                paths = sorted(set(self.responses()) - before)
                self.assertEqual(len(paths), post.call_count)
                self.assertGreater(len(paths), 0)
                log = '\n'.join(row['msg'] for row in store.load(self.pid)['log'])
                for path in paths:
                    self.assertEqual(path.read_text(), content or '')
                    self.assertIn(str(path.relative_to(self.d)), log)
                self.assertNotIn('director', store.load(self.pid))

    def test_empty_fields_and_long_assembly_save_only_problem_segment(self):
        data=self.fixture(4,2)
        for prompt in ('','   ',None,42,'x'*6213):
            value=self.plan(data,[['c01','c02'],['c03','c04']]);value['segments'][0]['prompt']=prompt
            with patch.object(gateway,'reason_checked',side_effect=[(value,{}),(value['segments'][0],{})]):
                saved=director.generate(self.pid)
            bad,good=saved['director']['segments']
            self.assertTrue(bad['needs_rewrite']);self.assertFalse(good['needs_rewrite'])
            self.assertTrue(bad['rewrite_reason'])
            with self.assertRaisesRegex(jimmy_bridge.BridgeError,'这一段的说明没写好'):
                jimmy_bridge.director_preview(self.pid,bad['id'])

    def test_all_structural_errors_still_fail_atomically(self):
        from console import segment_plan
        data=self.generated(4,groups=[['c01','c02'],['c03','c04']])
        for bad_id in ('d2','../x','missing',None):
            value=copy.deepcopy(data['director']);value['segments'][0]['id']=bad_id
            with self.subTest(bad_id=bad_id),self.assertRaisesRegex(ValueError,'导演方案不完整：'):
                self.invoke('generate',value)
            self.assertEqual(store.load(self.pid)['director'],data['director'])
        for change in ({'cells':['c01','c03']},{'mode':'bad'},{'images':['storyboard','missing']},{'refs':['C1','C1']}):
            value=self.plan(data);value['segments'][0].update(change)
            saved=self.invoke('generate',value)['director']['segments'][0]
            for key in ('cells','mode','images'):self.assertEqual(saved[key],data['director']['segments'][0][key])
        store.update(self.pid,lambda d:d['storyboard']['cells'][-1].update(end=20))
        with self.assertRaisesRegex(ValueError,'分段方案不完整：.*15'):
            director.validate_plan(data['director'],store.load(self.pid),self.d)

    def test_manual_fix_clears_brief_flag_at_exact_limit_in_both_save_paths(self):
        for action in ('prompt', 'save'):
            for original in ('', 'x'*6213):
                data = self.fixture(2); value = self.plan(data); value['segments'][0]['prompt'] = original
                before = self.invoke('generate', value)
                plan = copy.deepcopy(before['director']); valid=self.segment(['c01','c02'])['prompt']; exact=valid+'a'*(5000-len(valid))
                plan['segments'][0]['prompt'] = exact
                body = {'action':action, 'segment_id':'d1', 'prompt':exact, 'plan':plan}
                row = director.save(self.pid, body)['director']['segments'][0]
                self.assertFalse(row['needs_rewrite']); self.assertFalse(row.get('rewrite_reason'))
                self.assertTrue(row['edited']); self.assertEqual(len(row['prompt']), 5000)

    def test_manual_edit_retains_structural_flag_even_with_initial_long_brief(self):
        from console import segment_plan
        data=self.fixture(2);value=self.plan(data);value['segments'][0]['prompt']='x'*6213
        self.invoke('generate',value)
        segment_plan.save(self.pid,'d1',{'mode':'image'})
        saved=director.save(self.pid,{'action':'prompt','segment_id':'d1','prompt':self.segment(['c01','c02'])['prompt']+' 视频1'})
        row=saved['director']['segments'][0]
        self.assertTrue(director.view(saved,self.d)['segments'][0]['stale'])
        self.assertFalse(row.get('rewrite_reason'),'obsolete length reason must disappear')

    def test_rewrite_budget_previous_length_and_still_long_flag(self):
        data=self.fixture(4,2);value=self.plan(data,[['c01','c02'],['c03','c04']]);value['segments'][0]['prompt']='x'*6213
        with patch.object(gateway,'reason_checked',side_effect=[(value,{}),(value['segments'][0],{})]):
            before=director.generate(self.pid)
        target=copy.deepcopy(before['director']['segments'][0]);target['prompt']='y'*5501
        with patch.object(gateway,'reason_checked',return_value=(target,{})) as model:
            after=director.save(self.pid,{'action':'rewrite','segment_id':'d1'})
        text=json.dumps(model.call_args.args[1],ensure_ascii=False)
        self.assertIn('硬上限 5000 字符',text);self.assertIn('6213 字符，超过 5000 上限',text)
        row=after['director']['segments'][0]
        self.assertTrue(row['needs_rewrite']);self.assertEqual(row['prompt'],'y'*5501)
        self.assertEqual(row['rewrite_reason'],'这一段的提示词有 5501 字符，超过 5000 上限')
        self.assertEqual(after['director']['segments'][1],before['director']['segments'][1])
        target['prompt']=self.segment(['c01','c02'])['prompt']
        row=self.invoke('rewrite',target)['director']['segments'][0]
        self.assertFalse(row['needs_rewrite']);self.assertFalse(row.get('rewrite_reason'))

    def test_short_rewrite_has_budget_without_false_previous_length_claim(self):
        data=self.generated(2)
        text=json.dumps(director.messages(data,self.d,target=data['director']['segments'][0]),ensure_ascii=False)
        self.assertIn('硬上限 5000 字符',text);self.assertIn('4500 字',text)
        self.assertNotIn('上一版需要修改',text)

    def test_optional_explanations_do_not_discard_structurally_valid_plan(self):
        data = self.fixture(2); value = self.plan(data)
        value.pop('summary'); value['questions'] = None
        for key in ('what',):
            value['segments'][0].pop(key)
        saved = self.invoke('generate', value)
        self.assertEqual(len(saved['director']['segments']), 1)
        self.assertFalse(saved['director']['segments'][0]['needs_rewrite'])

    def test_rewrite_missing_empty_or_invalid_structured_content(self):
        before=self.generated(4,2,groups=[['c01','c02'],['c03','c04']])
        for content in (None,'',{'intro':'First'}):
            value=copy.deepcopy(before['director']['segments'][0])
            if content is None:value.pop('prompt')
            else:value['prompt']=content
            row=self.invoke('rewrite',value)['director']['segments'][0]
            self.assertTrue(row['needs_rewrite'])
            self.assertEqual(row['rewrite_reason'],'模型没有写这一段的完整提示词')
            self.assertEqual(store.load(self.pid)['director']['segments'][1],before['director']['segments'][1])

    def test_system_assembly_budget_and_short_manual_brief_has_no_warning(self):
        self.assertIn('每条提示词不超过 4500 字',director.SYSTEM)
        self.assertFalse(hasattr(director,'brief_budget'))
        self.generated(2)
        row=director.save(self.pid,{'action':'prompt','segment_id':'d1','prompt':self.segment(['c01','c02'])['prompt']})['director']['segments'][0]
        self.assertEqual(row['warnings'],[]);self.assertFalse(row['needs_rewrite'])


if __name__ == '__main__':
    unittest.main()
