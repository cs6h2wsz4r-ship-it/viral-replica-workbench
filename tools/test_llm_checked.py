"""Round 12 regressions: fake urlopen only; incident evidence is read-only."""
import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import config, jimmy_bridge, llm, media, motion, pipeline, store
from tools import test_replacement_guard as replacement_tests
from tools.test_motion_fidelity import incident
from tools.test_replacement_guard import fixture, task


def response(content='', reasoning=0, finish='stop', model=None):
    return {'choices': [{'message': {'content': content}, 'finish_reason': finish}],
            'model': model, 'usage': {'completion_tokens': reasoning,
                                    'completion_tokens_details': {'reasoning_tokens': reasoning}}}


class CheckedLLMTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('禁止读取配置')))
        # Gateway routing reads explicit offline settings; credential parsing stays forbidden.
        self.enterContext(patch.object(config, "load", return_value=dict(config.DEFAULTS)))
        self.enterContext(patch.object(config, 'provider_settings', side_effect=lambda provider: {
            'model': 'gpt-5.6-sol' if provider == 'jimmy' else 'deepseek-flash',
            'base_url': f'https://{provider}.invalid/v1', 'api_key': f'mock-{provider}', 'timeout': 1}))
        self.enterContext(patch.object(llm, '_jimmy_dns_shim'))
        self.enterContext(patch.object(llm, '_ssl_context', return_value=None))
        self.enterContext(patch.object(llm.time, 'sleep'))
        self.transport = self.enterContext(patch('urllib.request.urlopen'))
        self.requests = []
        self.messages = [{'role': 'user', 'content': '只输出 JSON'}]

    def replies(self, *items):
        queue = iter(items)

        def send(req, **kw):
            self.requests.append((req, json.loads(req.data)))
            item = next(queue)
            if isinstance(item, Exception):
                raise item
            return io.BytesIO(json.dumps(item).encode())

        self.transport.side_effect = send

    def checked(self, **kw):
        return llm.chat_json_checked('deepseek', self.messages, model='deepseek-flash',
                                     max_tokens=kw.pop('max_tokens', 4000), **kw)

    def test_empty_reasoning_does_not_repair_json(self):
        self.replies(response('', 4000), response('', 4000))
        with self.assertRaises(llm.LLMError) as caught:
            llm.chat_json('deepseek', self.messages, model='deepseek-flash', max_tokens=4000)
        self.assertEqual(type(caught.exception).__name__, 'ReasoningExhausted')
        self.assertIn('模型（deepseek-flash）把 4000 个 token 全部用于推理，没有输出正文（上限 4000）', str(caught.exception))
        self.assertEqual(len(self.requests), 1)

    def test_retry_with_larger_budget(self):
        self.replies(response('', 4000), response('{"ok": true}'))
        result, meta = self.checked()
        self.assertTrue(result['ok'])
        self.assertEqual([b['max_tokens'] for _, b in self.requests], [4000, 16000])
        self.assertEqual([a['success'] for a in meta['attempts']], [False, True])
        self.assertIn('推理占满', meta['attempts'][0]['reason'])
        self.assertEqual(meta['provider'], 'deepseek')
        self.assertTrue(all(b['messages'] == self.messages for _, b in self.requests))

    def test_fallback_uses_own_provider_settings(self):
        self.replies(response('', 4000), response('', 16000), response('{"ok":true}', model='gpt-5.6-sol'))
        _, meta = self.checked(analysis={'provider': 'jimmy'})
        self.assertEqual((meta['provider'], meta['model']), ('jimmy', 'gpt-5.6-sol'))
        self.assertEqual([b['max_tokens'] for _, b in self.requests], [4000, 16000, 8000])
        req, body = self.requests[-1]
        self.assertTrue(req.full_url.startswith('https://jimmy.invalid/'))
        self.assertEqual(req.get_header('Authorization'), 'Bearer mock-jimmy')
        self.assertEqual(body['model'], 'gpt-5.6-sol')

    def test_three_failures_include_every_reason_and_motion_warning(self):
        bad = [response('', 4000), response('', 16000), response('', 8000)]
        self.replies(*bad)
        with self.assertRaises(llm.LLMError) as caught:
            self.checked()
        self.assertEqual(len(caught.exception.attempts), 3)
        for budget in [4000, 16000, 8000]:
            self.assertIn(f'上限 {budget}', str(caught.exception))
        self.assertEqual(str(caught.exception).count('推理占满'), 3)
        data, t = incident()
        data['script'].update(provider='deepseek', request_model='deepseek-flash')
        data['routes'] = {'script': {'channel': 'deepseek', 'model': 'deepseek-flash'}}
        data['analysis'] = {'provider': 'jimmy'}
        self.replies(*bad)
        report = {}
        errors, warnings = motion.compare_actions(data, t, motion.source_timeline(data, t), report=report)
        self.assertFalse(errors)
        self.assertIn('推理占满', ' '.join(warnings))
        self.assertNotIn('没有返回 JSON', ' '.join(warnings))
        self.assertEqual(report['status'], 'incomplete')
        self.assertEqual(len(report['attempts']), 3)

    def test_nonempty_invalid_json_still_repairs(self):
        self.replies(response('不是 JSON'), response('{"fixed":true}'))
        result, _ = llm.chat_json('deepseek', self.messages, max_tokens=4000)
        self.assertTrue(result['fixed'])
        self.assertEqual(len(self.requests), 2)
        self.assertIn('修正后的完整 JSON', self.requests[-1][1]['messages'][-1]['content'])

    def test_real_incident_addition_is_error_after_fallback(self):
        data, t = incident()
        data['script'].update(provider='deepseek', request_model='deepseek-flash')
        data['routes'] = {'script': {'channel': 'deepseek', 'model': 'deepseek-flash'}}
        data['analysis'] = {'provider': 'jimmy'}
        quote = 'keep walking in until the framing tightens to a mid-shot'
        self.assertIn(quote, t['action'])
        answer = {'additions': [{'field': 'action', 'quote': quote, 'reason': '额外走近到中景'}]}
        self.replies(response('', 4000), response('', 16000), response(json.dumps(answer)))
        report = {}
        rows = motion.source_timeline(data, t)
        self.assertTrue(rows)
        errors, warnings = motion.compare_actions(data, t, rows, report=report)
        self.assertTrue(any(quote in error for error in errors))
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(report['model'], 'gpt-5.6-sol')
        self.assertIn('动作对比由', report['message'])
        self.assertIn('已改用', ' '.join(warnings))
        sent = json.loads(self.requests[-1][1]['messages'][-1]['content'])
        self.assertEqual(sent['SOURCE MOTION TIMELINE'], motion.timeline_block(rows))
        self.assertEqual(sent['action'], t['action'])

    def test_whitespace_null_parts_threshold_and_length(self):
        cases = [(None, 0, 'stop', 'EmptyResponse'), (' \n', 3599, 'stop', 'EmptyResponse'),
                 ([], 3600, 'stop', 'ReasoningExhausted'), ('', 0, 'length', 'ReasoningExhausted')]
        for content, count, finish, name in cases:
            with self.subTest(name=name, count=count):
                self.replies(response(content, count, finish))
                with self.assertRaises(llm.LLMError) as caught:
                    llm.chat_json('deepseek', self.messages, max_tokens=4000)
                self.assertEqual(type(caught.exception).__name__, name)
                self.assertEqual(self.transport.call_count, 1)
                self.transport.reset_mock()

    def test_plain_empty_response_retries_without_repair(self):
        self.replies(response('  '), response('{"ok":true}'))
        _, meta = self.checked()
        self.assertIn('模型返回了空回复', meta['attempts'][0]['reason'])
        self.assertEqual(len(self.requests), 2)

    def test_bad_json_after_repair_retries(self):
        self.replies(response('bad'), response('still bad'), response('{"ok":true}'))
        _, meta = self.checked()
        self.assertEqual([b['max_tokens'] for _, b in self.requests], [4000, 4000, 16000])
        self.assertIn('JSON', meta['attempts'][0]['reason'])

    def test_nonempty_length_reply_is_valid(self):
        self.replies(response('{"ok":true}', 4000, 'length'))
        result, meta = self.checked()
        self.assertTrue(result['ok'])
        self.assertEqual(len(meta['attempts']), 1)

    def test_same_analysis_model_skips_fallback_and_budget_is_capped(self):
        self.replies(response('', 16000), response('', 32000))
        with self.assertRaises(llm.LLMError) as caught:
            self.checked(max_tokens=16000, analysis={'provider': 'deepseek', 'model': 'deepseek-flash'})
        self.assertEqual(len(caught.exception.attempts), 2)
        self.assertEqual([b['max_tokens'] for _, b in self.requests], [16000, 32000])

    def test_same_model_on_other_provider_also_skips_fallback(self):
        self.replies(response('', 4000), response('', 16000), response('{}'))
        with self.assertRaises(llm.LLMError) as caught:
            self.checked(analysis={'provider': 'custom', 'model': 'deepseek-flash'})
        self.assertEqual(len(caught.exception.attempts), 2)
        self.assertEqual(len(self.requests), 2)

    def test_gemini_fallback_receives_double_budget(self):
        gemini = {'code': 0, 'data': {'candidates': [{'content': {'parts': [{'text': '{"ok":true}'}]}}]}}
        self.replies(response('', 4000), response('', 16000), gemini)
        _, meta = self.checked(analysis={'provider': 'jimmy', 'model': 'gemini-test'})
        req, body = self.requests[-1]
        self.assertTrue(req.full_url.endswith('/images/understand'))
        self.assertEqual(body['generationConfig']['maxOutputTokens'], 8000)
        self.assertEqual(meta['model'], 'gemini-test')

    def test_gemini_empty_retry_receives_quadruple_budget(self):
        self.replies({'code': 0, 'data': {'candidates': [{'content': {'parts': [{'text': ''}]}}]}},
                     {'code': 0, 'data': {'candidates': [{'content': {'parts': [{'text': '{}'}]}}]}})
        _, meta = llm.chat_json_checked('jimmy', self.messages, model='gemini-test', max_tokens=4000)
        self.assertEqual([b['generationConfig']['maxOutputTokens'] for _, b in self.requests], [4000, 16000])
        self.assertEqual(len(meta['attempts']), 2)

    def test_analysis_recorded_model_precedes_config_default(self):
        for analysis in [{'provider': 'custom', 'model': 'saved-analysis'},
                         {'provider': 'custom', 'steps': {'overview': {'output': {'model': 'saved-analysis'}}}}]:
            with self.subTest(analysis=analysis):
                self.replies(response('', 4000), response('', 16000), response('{}'))
                _, meta = self.checked(analysis=analysis)
                self.assertEqual(meta['provider'], 'custom')
                self.assertEqual(self.requests[-1][1]['model'], 'saved-analysis')

    def test_safety_rewrite_records_actual_fallback_model(self):
        data = fixture()
        data['script'].update(provider='deepseek', request_model='deepseek-flash')
        data['routes'] = {'script': {'channel': 'deepseek', 'model': 'deepseek-flash'}}
        data['analysis'] = {'provider': 'jimmy'}
        t = task(action='Keep blonde hair. Turn slowly.')
        data['script']['tasks'] = [t]
        rewritten = 'Keep the person from @image1. Turn slowly.'
        answer = {'tasks': [{'id': t['id'], 'action': rewritten, 'story': '', 'direction': '',
                            'changes': [{'field': 'action', 'before': 'blonde hair', 'after': 'the person from @image1'}]}]}
        self.replies(response('', 16000), response('', 32000), response(json.dumps(answer)))
        pipeline.apply_replacement_safety(data, data['script'])
        self.assertEqual(t['action'], rewritten)
        self.assertEqual(t['replacement_safety'][0]['provider'], 'jimmy')
        self.assertEqual(t['replacement_safety'][0]['model'], 'gpt-5.6-sol')
        self.assertEqual(t['replacement_safety_check']['model'], 'gpt-5.6-sol')
        self.assertEqual(len(t['replacement_safety_check']['attempts']), 3)

    def test_network_failure_has_real_reason(self):
        self.replies(*[urllib.error.URLError('mock 网络断开') for _ in range(12)])
        with self.assertRaises(llm.LLMError) as caught:
            self.checked()
        self.assertIn('连接失败', str(caught.exception))
        self.assertIn('mock 网络断开', str(caught.exception))
        self.assertNotIn('没有返回 JSON', str(caught.exception))

    def test_motion_warning_persists_on_allowed_preview_and_invalidates_on_edit(self):
        pid, directory, _ = replacement_tests.ReplacementGuardTests.project(self)
        store.update(pid, lambda d: d['breakdown'].update(shots=[{'start': 0, 'end': 3, 'action': '站立不动'}]))
        store.update(pid, lambda d: d['script']['tasks'][0].update(action='An earnest expression.'))
        self.replies(response(json.dumps({'additions': [{'field': 'action', 'quote': 'An earnest expression.', 'reason': '原片时机不一致'}]})))
        def package(args, cwd):
            store.atomic_write_json(cwd / 'review/jimmy-job.preview.json', {'tasks': [{'id': 'seg-01', 'model': jimmy_bridge.MINI_VIDEO}]})
            store.atomic_write_json(cwd / 'review/jimmy-handoff.json', {'job_sha256': 'mock'})
        with patch.object(media, 'cut_clip', side_effect=lambda src, start, length, out, **kw: out.write_bytes(b'mock clip')), \
                patch.object(media, 'probe', return_value={'duration': 3}), \
                patch.object(media, 'caption_residue', return_value={'status': 'skipped', 'suspected': []}), \
                patch.object(jimmy_bridge, '_call', side_effect=package):
            preview = jimmy_bridge.preview(pid, 'seg-01')
        self.assertEqual(preview['state'], 'pending_confirmation')
        data = store.load(pid)
        t = data['script']['tasks'][0]
        check = jimmy_bridge.replacement_check(data, t, directory)
        self.assertEqual(check['action_comparison']['status'], 'completed')
        self.assertEqual(check['errors'], [])
        self.assertTrue(any('原片时机不一致' in e for e in check['motion_warning']['details']))
        self.assertEqual(len(self.requests), 1, '只读卡片检查不得调用模型')
        self.assertTrue(list((directory / 'packages').iterdir()))
        t['action'] = 'Stay still.'
        check = jimmy_bridge.replacement_check(data, t, directory)
        self.assertEqual(check['action_comparison']['status'], 'incomplete')
        self.assertFalse(any('原片时机不一致' in e for e in check['errors']))

    def test_preview_records_success_and_exhaustion_status(self):
        pid, directory, _ = replacement_tests.ReplacementGuardTests.project(self)
        store.update(pid, lambda d: d['breakdown'].update(shots=[{'start': 0, 'end': 3, 'action': '转身'}]))

        def package(args, cwd):
            store.atomic_write_json(cwd / 'review/jimmy-job.preview.json', {'tasks': [{'id': 'seg-01', 'model': jimmy_bridge.MINI_VIDEO}]})
            store.atomic_write_json(cwd / 'review/jimmy-handoff.json', {'job_sha256': 'mock'})

        for status, replies in [('completed', [response('{"additions":[]}')]),
                                ('incomplete', [response('', 4000), response('', 16000), response('', 8000)])]:
            with self.subTest(status=status):
                self.replies(*replies)
                with patch.object(media, 'cut_clip', side_effect=lambda src, start, length, out, **kw: out.write_bytes(b'mock clip')), \
                        patch.object(media, 'probe', return_value={'duration': 3}), \
                        patch.object(media, 'caption_residue', return_value={'status': 'skipped', 'suspected': []}), \
                        patch.object(jimmy_bridge, '_call', side_effect=package):
                    record = jimmy_bridge.preview(pid, 'seg-01')
                self.assertEqual(record['action_comparison']['status'], status)
                data = store.load(pid)
                check = jimmy_bridge.replacement_check(data, data['script']['tasks'][0], directory)
                self.assertEqual(check['action_comparison'], record['action_comparison'])
                if status == 'incomplete':
                    self.assertIn('推理占满', check['action_comparison']['reason'])

    def test_missing_timeline_never_claims_completed(self):
        report = {}
        _, warnings = motion.compare_actions({'script': {'provider': 'deepseek', 'model': 'deepseek-flash'}}, {}, [], report=report)
        self.assertEqual(report['status'], 'incomplete')
        self.assertIn('原片没有可用的动作时间轴', report['reason'])
        self.transport.assert_not_called()

    def test_same_model_retry_with_response_version_is_not_reported_as_fallback(self):
        data, t = incident()
        data['script'].update(provider='deepseek', request_model='deepseek-flash')
        data['routes'] = {'script': {'channel': 'deepseek', 'model': 'deepseek-flash'}}
        self.replies(response('', 4000), response('{"additions":[]}', model='deepseek-flash-version'))
        _, warnings = motion.compare_actions(data, t, motion.source_timeline(data, t))
        self.assertIn('提高上限重试', ' '.join(warnings))
        self.assertNotIn('已改用', ' '.join(warnings))

    def test_missing_fallback_configuration_preserves_first_two_failures(self):
        self.replies(response('', 4000), response('', 16000))
        settings = config.provider_settings.side_effect

        def missing_jimmy(provider):
            if provider == 'jimmy':
                raise ValueError('还没填 三方渠道 Key')
            return settings(provider)

        with patch.object(config, 'provider_settings', side_effect=missing_jimmy):
            with self.assertRaises(llm.LLMError) as caught:
                self.checked()
        self.assertEqual(len(caught.exception.attempts), 3)
        self.assertIn('还没填 三方渠道 Key', str(caught.exception))
        self.assertEqual(str(caught.exception).count('推理占满'), 2)
        self.assertEqual(len(self.requests), 2)


if __name__ == '__main__':
    unittest.main()
