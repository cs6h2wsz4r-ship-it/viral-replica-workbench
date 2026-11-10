"""V7 speech-led planning. Local fixtures and mocked reasoning only."""
import copy
import hashlib
import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from console import director, gateway, segment_plan, store, storyboard
from tools import test_v3_chain5 as chain5
from tools import test_v2_segment_plan as planning
from tools import test_v6
from tools.v7_speech_cases import V7SpeechTests


def real_speech(directory):
    """Copy only the approved project's transcript into a temporary fixture."""
    origin = Path(__file__).resolve().parents[1] / 'projects/p1105180906-f9f6'
    target = directory / 'analysis/transcript.raw.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(origin / 'analysis/transcript.raw.json', target)
    words = [w for s in json.loads(target.read_text())['segments'] for w in s['words']]
    # Deliberately manual offline stub, never a model result or a project edit.
    ranges = [(0, 3), (4, 15), (16, 20), (21, 25), (26, 28), (29, 32),
              (33, 42), (43, 53), (54, 60)]
    lines = [dict(start=words[a]['start'], end=words[b]['end'],
                  text=' '.join(w['word'] for w in words[a:b+1]).capitalize() + '.',
                  words=[a, b], speaker='C1',
                  pause_after=round(words[b+1]['start']-words[b]['end'], 3) if b+1<len(words) else 0)
             for a, b in ranges]
    return words, lines


class SpeechPlanningTests(planning.SegmentFixture, unittest.TestCase):
    def speech_fixture(self):
        data = self.fixture()
        words, lines = real_speech(self.d)
        data['breakdown']['speech_lines'] = lines
        data['breakdown']['speech_lines_state'] = {'status': 'done'}
        data['breakdown']['dialogue'] = json.loads((self.d/'analysis/transcript.raw.json').read_text())['segments']
        return data, words

    def test_real_cut_labels_and_segment_request(self):
        data, _ = self.speech_fixture()
        state = segment_plan.cut_speech(data, 1.167, self.d)
        self.assertEqual(state['speech'], '句中')
        self.assertEqual(state['line'], 1)
        self.assertEqual(state['between'], ['kidding', 'me'])
        state = segment_plan.cut_speech(data, 9.5, self.d)
        self.assertEqual(state['speech'], '句间')
        self.assertAlmostEqual(state['pause'], .14)
        request = segment_plan.messages(data, self.d)
        text = request[1]['content'][0]['text']
        self.assertIn('speech_lines', text)
        self.assertIn('格子切点的口播状态', text)
        self.assertIn('“句中”的切点不能作为分界', request[0]['content'])

    def test_mid_sentence_retries_once_then_saves_warning(self):
        data, _ = self.speech_fixture()
        store.update(self.pid, lambda current: current.update(data))
        with patch('console.speech_lines.ensure', return_value=data) as ensure, \
                patch.object(gateway, 'reason_checked', return_value=(self.value(), {})) as reason:
            saved = segment_plan.generate(self.pid)
        ensure.assert_called_once_with(self.pid)
        self.assertEqual(reason.call_count, 2)
        self.assertIn('第 1 段和第 2 段', json.dumps(reason.call_args_list[1].args[1], ensure_ascii=False))
        self.assertTrue(saved['segments_plan']['segments'])
        warning = segment_plan.status(saved, self.d)['speech_warnings'][0]
        self.assertEqual(warning, '第 1 段和第 2 段的分界落在一句台词中间，拼接处台词可能不连贯；可以点“重新生成分段方案”再试。')

    def test_retry_can_move_to_sentence_pause_and_clear_warning(self):
        data, _ = self.speech_fixture()
        data['breakdown']['speech_lines'] = [dict(start=0, end=6.7, text='A.', words=[0,0]),
                                           dict(start=6.9, end=12, text='B.', words=[1,1])]
        second = self.value()
        second['segments'][0]['cells'].append(second['segments'][1]['cells'].pop(0))
        second['segments'][0]['gen_images'].append('c04')
        second['segments'][1]['gen_images'][0] = 'c05'
        store.update(self.pid, lambda current: current.update(data))
        with patch('console.speech_lines.ensure', return_value=data), \
                patch.object(gateway, 'reason_checked', side_effect=[(self.value(), {}), (second, {})]) as reason:
            saved = segment_plan.generate(self.pid)
        self.assertEqual(reason.call_count, 2)
        self.assertEqual(segment_plan.status(saved, self.d).get('speech_warnings', []), [])

    def test_legacy_generation_migrates_before_planning_only_once(self):
        data, _ = self.speech_fixture()
        lines = data['breakdown'].pop('speech_lines')
        data['breakdown'].pop('speech_lines_state')
        store.update(self.pid, lambda current: current.update(data))
        answer = {'lines':[dict(first=row['words'][0],last=row['words'][1],text=row['text']) for row in lines]}
        order=[]
        def sentence(*args, **kw):
            order.append('speech'); return {'content':json.dumps(answer)}
        def plan(*args, **kw):
            order.append('plan')
            self.assertIn('speech_lines',json.dumps(args[1]))
            return self.value(), {}
        with patch.object(gateway,'reason',side_effect=sentence) as speech, patch.object(gateway,'reason_checked',side_effect=plan):
            first=segment_plan.generate(self.pid)
            second=segment_plan.generate(self.pid)
        self.assertEqual(order[0],'speech');speech.assert_called_once()
        self.assertEqual(first['breakdown']['speech_lines'],second['breakdown']['speech_lines'])

    def test_boundary_exact_line_edges_and_silent_classification(self):
        data,_=self.speech_fixture();line=data['breakdown']['speech_lines'][0]
        for time in (line['start'],line['end']):
            self.assertEqual(segment_plan.cut_speech(data,time,self.d)['speech'],'句间')
        self.assertEqual(segment_plan.cut_speech({},1)['speech'],'无口播')

    def test_failed_boundary_retry_keeps_first_usable_plan(self):
        from console.gateway.errors import CheckedLLMError
        data,_=self.speech_fixture()
        store.update(self.pid,lambda current:current.update(data))
        for second in (CheckedLLMError([]), ({'segments':[]},{})):
            with self.subTest(second=type(second).__name__), patch('console.speech_lines.ensure',return_value=store.load(self.pid)), \
                    patch.object(gateway,'reason_checked',side_effect=[(self.value(),{}),second]) as reason:
                saved=segment_plan.generate(self.pid)
            self.assertEqual(reason.call_count,2)
            self.assertTrue(segment_plan.status(saved,self.d)['speech_warnings'])

    def test_superseded_speech_migration_cannot_continue_to_plan(self):
        data,_=self.speech_fixture()
        lines=data['breakdown'].pop('speech_lines');data['breakdown'].pop('speech_lines_state')
        store.update(self.pid,lambda current:current.update(data))
        answer={'lines':[dict(first=r['words'][0],last=r['words'][1],text=r['text']) for r in lines]}
        def replace(*args,**kw):
            store.update(self.pid,lambda current:current.update(analysis={'started':'newer-analysis','status':'running'}))
            return {'content':json.dumps(answer)}
        with patch.object(gateway,'reason',side_effect=replace),patch.object(gateway,'reason_checked',return_value=(self.value(),{})) as plan:
            with self.assertRaisesRegex(ValueError,'原片或拆解'):
                segment_plan.generate(self.pid)
        plan.assert_not_called()
        self.assertNotIn('segments_plan',store.load(self.pid))


class SpeechDirectorTests(unittest.TestCase):
    setUp = chain5.Chain5AuthoringTests.setUp
    fixture = chain5.Chain5AuthoringTests.fixture
    fields = chain5.Chain5AuthoringTests.fields

    def speech_fixture(self):
        data = self.fixture()
        words, lines = real_speech(self.d)
        data['breakdown']['speech_lines'] = lines
        # Keep short fixture clocks: the first natural sentence is clipped at 1.5s.
        store.update(self.pid, lambda current: current.update(data))
        return data, words

    def test_clipped_lines_two_decimal_clock_and_cut_word(self):
        data, _ = self.speech_fixture()
        segment = dict(id='d1', cells=['c01','c02'], source_start=.5, source_end=1.2, stretch=2)
        lines, cuts = director.speech_context(data, segment, self.d)
        self.assertEqual(lines[0]['start'], 0)
        self.assertEqual(lines[0]['end'], 1.4)
        self.assertEqual([w['word'] for w in lines[0]['words']], ['you','kidding','me'])
        self.assertEqual(lines[0]['words'][1]['start'], .53)
        self.assertEqual(cuts[0]['time'], .5)
        request = director.messages(data, self.d)
        text = json.dumps(request, ensure_ascii=False)
        for label in ('本段口播线', '切点与口播对应', '连续说完，切换镜头时不停顿'):
            self.assertIn(label, text)
        self.assertNotIn('商品状态、台词的说话人和逐字内容', request[0]['content'])

    def test_split_sentence_rewrites_once_and_remains_nonblocking(self):
        data, _ = self.speech_fixture()
        # Only test speech in the first segment, isolating per-segment rewrite count.
        data['breakdown']['speech_lines'] = data['breakdown']['speech_lines'][:1]
        store.update(self.pid, lambda current: current.update(data))
        value = self.fields(data)
        value['segments'][0]['prompt'] += '逐字说“Are you kidding”，切镜，再说“me”。'
        reply = value['segments'][0]
        with patch.object(director, 'require_ready'), patch.object(gateway, 'reason_checked', side_effect=[(value, {}), (reply, {})]) as reason:
            saved = director.generate(self.pid)
        self.assertEqual(reason.call_count, 2)
        self.assertIn('台词', json.dumps(reason.call_args_list[1].args[1], ensure_ascii=False))
        row = saved['director']['segments'][0]
        self.assertFalse(row['needs_rewrite'], 'speech is advisory, never a generation gate')
        self.assertIn('台词', row['speech_warning'])

    def test_single_rewrite_retries_and_normalizes_punctuation(self):
        data, _ = self.speech_fixture()
        data['breakdown']['speech_lines'] = data['breakdown']['speech_lines'][:1]
        store.update(self.pid, lambda current: current.update(data))
        reply = self.fields(data)['segments'][0]
        fixed = {**reply, 'prompt': reply['prompt']+'。口播：ARE YOU KIDDING ME?'}
        with patch.object(director, 'require_ready'), patch.object(gateway, 'reason_checked', side_effect=[(reply, {}), (fixed, {})]) as reason:
            saved = director._rewrite(self.pid, 'd1')
        self.assertEqual(reason.call_count, 2)
        self.assertFalse(saved['director']['segments'][0].get('speech_warning'))

    def test_silent_requests_match_pre_v7_bytes(self):
        data = self.fixture(); data['breakdown']['dialogue'] = []
        with patch.object(director.llm, 'image_part', return_value=[{'type':'image_url','image_url':{'url':'data:image/jpeg;base64,OFFLINE'}}]):
            for module, expected in ((segment_plan, 'da00111ddd407fcabbf4d55dd4f3c48ce6348d7c651d32bcfe6a237fab9aa7a8'),
                                     (director, '66232b2d74b208912a63a82a0c59bd5f44ae662662240736bd357d4b298dd339')):
                with self.subTest(module=module.__name__):
                    actual = hashlib.sha256(json.dumps(module.messages(data,self.d),ensure_ascii=False).encode()).hexdigest()
                    self.assertEqual(actual, expected)

    def test_new_system_rules_are_generic(self):
        from console import speech_lines, fix_plan
        from tools.test_v2c_generic_prompts import BANNED
        for text in (speech_lines.SPEECH_SYSTEM, segment_plan.SPEECH_RULES, director.SPEECH_RULES, fix_plan.FIX_SYSTEM):
            self.assertIsNone(BANNED.search(text))
            for phrase in ('kidding', 'glitter', 'lip balm', 'C1', 'people are'):
                self.assertNotIn(phrase, text)

    def test_reconstructing_speech_keeps_original_storyboard_current(self):
        data = self.fixture()
        store.atomic_write_json(self.d/'analysis/boundaries.json', {'merged_shots':[{'start':0, 'end':3}]})
        old = storyboard.source_sig(data, self.d)
        data['storyboard']['source_sig'] = old
        data['breakdown']['speech_lines'] = [{'start':0, 'end':1, 'text':'Natural.'}]
        data['breakdown']['speech_lines_state'] = {'status':'done'}
        self.assertEqual(storyboard.source_sig(data, self.d), old)
        self.assertFalse(storyboard.status(data, self.d)['stale'])

    def test_word_timing_changes_invalidate_only_speech_dependent_signatures(self):
        data,_=self.speech_fixture()
        before=[module.inputs_sig(data,self.d) for module in (segment_plan,director)]
        silent=copy.deepcopy(data);silent['breakdown'].pop('speech_lines')
        silent_before=[module.inputs_sig(silent,self.d) for module in (segment_plan,director)]
        path=self.d/'analysis/transcript.raw.json';transcript=json.loads(path.read_text())
        transcript['segments'][0]['words'][0]['end']+=.05
        store.atomic_write_json(path,transcript)
        self.assertNotEqual(segment_plan.inputs_sig(data,self.d),before[0])
        self.assertNotEqual(director.inputs_sig(data,self.d),before[1])
        self.assertEqual([module.inputs_sig(silent,self.d) for module in (segment_plan,director)],silent_before)

    def test_complete_sentence_shares_hyphen_rules_without_merging_words(self):
        data={'breakdown':{'speech_lines':[dict(start=0,end=1,text='Use high-end gear.',words=[0,2])]},
              'analysis':{'transcript':{'segments':[{'words':[dict(index=i,word=word,start=i/3,end=(i+1)/3) for i,word in enumerate(['Use','high-end','gear'])]}]}}}
        segment=dict(prompt='口播：Use high end gear. 连续说完。',source_start=0,source_end=1,stretch=1)
        self.assertEqual(director.speech_problem(segment,data),'')
        for prompt in ('Use high，切镜，再说 end gear.','Use highendgear.'):
            self.assertTrue(director.speech_problem({**segment,'prompt':prompt},data))
        data['breakdown']['speech_lines'][0]['text']='People are here.'
        data['analysis']['transcript']['segments'][0]['words']=[dict(word=word,start=i/3,end=(i+1)/3) for i,word in enumerate(['People','are','here'])]
        self.assertTrue(director.speech_problem({**segment,'prompt':'Peopleare here.'},data))


class SpeechRepairReviewTests(unittest.TestCase):
    setUp = test_v6.V6Tests.setUp
    frame = test_v6.V6Tests.frame
    report = test_v6.V6Tests.report
    run_record = test_v6.V6Tests.run_record
    output = test_v6.V6Tests.output

    def add_speech(self):
        def add(data):
            data['breakdown']['speech_lines'] = [dict(start=.2, end=.8, text='Keep it together.', words=[0,2], speaker='C1', pause_after=0)]
            data['segments_plan']['inputs_sig'] = segment_plan.inputs_sig(data, self.d)
        store.update(self.pid, add)

    def test_fix_prompt_uses_same_speech_rules_retry_and_advisory_validation(self):
        from console import fix_plan
        self.add_speech(); value = self.output(False)
        with patch.object(director, 'require_ready'), patch.object(gateway, 'reason_checked', return_value=(value,{})) as reason:
            saved = fix_plan.generate(self.pid,'d1','r1',{'issues':[0]})
        self.assertEqual(reason.call_count,2)
        self.assertIn('连续说完，切换镜头时不停顿', reason.call_args_list[0].args[1][0]['content'])
        self.assertTrue(saved['runs']['d1'][0]['fix_plan']['speech_warning'])
        applied = fix_plan.apply(self.pid, 'd1', 'r1', {'plan_id':saved['runs']['d1'][0]['fix_plan']['id']})
        row = director.get_segment(applied,'d1')
        self.assertTrue(row['speech_warning']); self.assertFalse(row['needs_rewrite'])

    def test_failed_speech_retry_keeps_first_fix_plan(self):
        from console import fix_plan
        from console.gateway.errors import CheckedLLMError
        self.add_speech()
        with patch.object(director, 'require_ready'), patch.object(gateway, 'reason_checked', side_effect=[(self.output(False),{}), CheckedLLMError([])]) as reason:
            saved = fix_plan.generate(self.pid,'d1','r1',{'issues':[0]})
        self.assertEqual(reason.call_count,2)
        self.assertTrue(saved['runs']['d1'][0]['fix_plan']['speech_warning'])

    def test_review_freezes_speech_and_adds_to_existing_requirements(self):
        from console import segment_review
        self.add_speech(); data=store.load(self.pid); segment=director.get_segment(data,'d1')
        frozen=segment_review.freeze_inputs(data,segment,director.references(data,segment,self.d),self.d,self.d/'packages/speech-review')
        self.assertEqual(frozen['speech_lines'][0]['text'], 'Keep it together.')
        folder=self.d/'review-speech';folder.mkdir()
        request,_=segment_review.messages(frozen,self.d,self.d/'clips/d1.mp4',folder)
        self.assertEqual(request[0]['content'],segment_review.SYSTEM)
        self.assertIn('本段理顺后的台词',json.dumps(request,ensure_ascii=False))
        self.assertIn('本版生成要求',json.dumps(request,ensure_ascii=False))
        older=self.run_record()
        older['review_inputs'].pop('speech_lines',None)
        rebuilt=segment_review._inputs(data,'d1',older,self.d,folder)
        self.assertIn('speech_lines',rebuilt)
        self.assertTrue(rebuilt['speech_lines'])
        self.assertNotIn('speech_lines',older['review_inputs'])

    def test_old_review_cannot_borrow_speech_after_source_replaced(self):
        from console import segment_review
        self.add_speech();data=store.load(self.pid);older=self.run_record()
        older['review_inputs'].pop('speech_lines',None)
        data['source']['sha256']='replacement-source'
        (self.d/'source/source.mp4').write_bytes(b'replacement source')
        rebuilt=segment_review._inputs(data,'d1',older,self.d,self.d)
        self.assertFalse(rebuilt.get('speech_lines'))
        # The oldest versions without evidence must not silently inherit new words.
        older['review_inputs'].pop('speech_source',None)
        rebuilt=segment_review._inputs(data,'d1',older,self.d,self.d)
        self.assertFalse(rebuilt.get('speech_lines'))

    def test_known_silent_freeze_remains_silent_after_later_reconstruction(self):
        from console import segment_review
        older=self.run_record()
        silent=store.load(self.pid);silent['breakdown']['dialogue']=[]
        segment=director.get_segment(silent,'d1')
        older['review_inputs']=segment_review.freeze_inputs(silent,segment,director.references(silent,segment,self.d),self.d,self.d/'packages/silent-review')
        self.assertEqual(older['review_inputs'].get('speech_lines'),[])
        self.add_speech()
        rebuilt=segment_review._inputs(store.load(self.pid),'d1',older,self.d,self.d)
        self.assertEqual(rebuilt['speech_lines'],[])


if __name__ == '__main__':
    unittest.main()
