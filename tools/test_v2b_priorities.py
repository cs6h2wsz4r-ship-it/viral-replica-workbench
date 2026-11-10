"""V2b prompt priorities and real refresh persistence, entirely offline."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from console import config, director, final_preview, jimmy_bridge, store, storyboard_edits


class V2bPromptPrioritiesTests(unittest.TestCase):
    def test_director_priorities_are_ordered_before_detail_instructions(self):
        markers = ('优先级从高到低', '① 人和商品换干净', '② 剧情逻辑通顺',
                   '③ 动作、机位、镜头顺序和节奏对上原片',
                   '④ 新衣服的款式细节、配饰、背景小物权重最低', '每段提示词按下面顺序')
        for marker in markers:
            self.assertIn(marker, director.SYSTEM)
        offsets = [director.SYSTEM.index(marker) for marker in markers]
        self.assertEqual(offsets, sorted(offsets))
        for text in ('原片人物的脸、发型、衣服和发饰配饰，旧商品、旧包装和字幕都不出现', '新商品的外观和用法正确',
                     '新衣服的款式细节', '不为它们写约束'):
            self.assertIn(text, director.SYSTEM)

    def test_director_dialogue_defaults_to_source_but_may_move_for_story_logic(self):
        for text in ('台词和反应放在剧情上说得通的时刻', '② 剧情逻辑通顺', '移到合理的时刻',
                     '台词的说话人和逐字内容', '原片时间点与剧情不合时',
                     'summary 里用一句话说明'):
            self.assertIn(text, director.SYSTEM)

    def test_director_forbid_keeps_old_faces_products_and_packaging_without_clothes(self):
        self.assertIn('原片人物的脸、发型、衣服、发饰配饰，旧商品、旧包装和品牌文字', director.SYSTEM)
        self.assertIn('人物外观、衣服、发饰、旧商品和文字一律不用', director.SYSTEM)

    def test_product_states_are_generic_without_inventing_features_or_interface_text(self):
        for system in (director.SYSTEM, storyboard_edits.SYSTEM):
            with self.subTest(system=system.split('。', 1)[0]):
                for text in ('真实功能和使用方式', '不照搬原片旧商品的外观和状态',
                             '旧商品没有、新商品具备的状态', '按常识表现',
                             '这不算编造功能', '界面上的具体文字不要写'):
                    self.assertIn(text, system)
        self.assertIn('不写素材里看不到、也无法确认的功能', director.SYSTEM)
        self.assertIn('不推测未确认的功能', storyboard_edits.SYSTEM)

    def test_storyboard_priorities_and_product_state_override_low_weight_clothing(self):
        markers = ('优先级从高到低', '① 人和商品换干净', '② 剧情逻辑通顺',
                   '③ 动作、机位、镜头顺序和节奏对上原片',
                   '④ 新衣服的款式细节、配饰、背景小物权重最低', '拼图里的每一格都要列出来')
        for marker in markers:
            self.assertIn(marker, storyboard_edits.SYSTEM)
        offsets = [storyboard_edits.SYSTEM.index(marker) for marker in markers]
        self.assertEqual(offsets, sorted(offsets))
        for text in ('被替换的人物默认穿人物参考照里的衣服', '新衣服的细节不必和参考照完全一致',
                     '不为它们写约束、不提问', '新商品要处于剧情需要的状态',
                     '商品按真实用法使用或摆放', '正面或功能面朝向镜头',
                     '手、手臂、腿、背影或衣角', '不能留空'):
            self.assertIn(text, storyboard_edits.SYSTEM)
        self.assertNotIn('衣服怎么处理', storyboard_edits.SYSTEM)
        self.assertNotIn('changes 要写明改成新人物的肤色、袖口和衣服', storyboard_edits.SYSTEM)


class V2bAutoSelectionTests(unittest.TestCase):
    def setUp(self):
        temp_root = ROOT / 'runtime/temp'
        temp_root.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=temp_root)))
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.tmp))
        self.enterContext(patch.object(config, '_parse', side_effect=AssertionError('no secret reads')))
        self.pid = store.create('V2b synthetic refresh')['id']
        self.directory = store.project_dir(self.pid)
        self.sid = 'd01'
        self.segment = dict(id=self.sid, cells=['c01'], source_start=0, source_end=4)
        self.new_run = dict(rev='r2', kind='director', state='running', mode='image',
                            run_dir='jimmy-runs/d01-r2')
        store.update(self.pid, lambda data: data.update(
            segments_plan={'segments': [copy.deepcopy(self.segment)]},
            director={'format_version': 3, 'segments': [copy.deepcopy(self.segment)]},
            runs={self.sid: [copy.deepcopy(self.new_run)]}))

    def write_child(self, phase='completed', with_result=True):
        child = {'phase': phase, 'status': phase}
        if with_result:
            result = self.directory / 'clips/r2.mp4'
            result.write_bytes(b'synthetic completed video; never submitted')
            child['local_path'] = str(result)
        store.atomic_write_json(self.directory / self.new_run['run_dir'] / 'state.json',
                                {'tasks': {self.sid: child}})
        return child

    def saved_segment(self):
        return store.load(self.pid)['director']['segments'][0]

    def old_run(self, state='completed', with_result=True):
        run = dict(rev='r1', kind='director', state=state, mode='image')
        if with_result:
            run['result'] = 'clips/r1.mp4'
            (self.directory / run['result']).write_bytes(b'previous synthetic video')
        store.update(self.pid, lambda data: data['runs'][self.sid].insert(0, run))
        return run

    def select(self, rev):
        store.update(self.pid, lambda data: data['director']['segments'][0].update(selected_run=rev))

    def test_completed_refresh_persists_selection_and_makes_step08_ready(self):
        child = self.write_child()
        self.assertEqual(jimmy_bridge.refresh(self.pid, self.sid, 'r2'), child)
        data = store.load(self.pid)
        self.assertEqual(data['runs'][self.sid][0]['state'], 'completed')
        self.assertEqual(data['director']['segments'][0].get('selected_run'), 'r2')
        clips, selected, warnings = final_preview.selections(data, self.directory)
        self.assertEqual(selected, [{'id': self.sid, 'rev': 'r2'}])
        self.assertEqual(clips, [self.directory / 'clips/r2.mp4'])
        self.assertEqual(warnings, [])
        self.assertTrue(final_preview.view(data, self.directory)['ready'])

    def test_completed_refresh_preserves_manual_completed_selection(self):
        self.old_run()
        director.save(self.pid, {'action': 'select-run', 'segment_id': self.sid, 'rev': 'r1'})
        self.write_child()
        jimmy_bridge.refresh(self.pid, self.sid, 'r2')
        jimmy_bridge.refresh(self.pid, self.sid, 'r2')
        self.assertEqual(self.saved_segment()['selected_run'], 'r1')

    def test_completed_selection_is_protected_even_without_its_result(self):
        self.old_run(with_result=False)
        self.select('r1')
        self.write_child()
        jimmy_bridge.refresh(self.pid, self.sid, 'r2')
        self.assertEqual(self.saved_segment()['selected_run'], 'r1')

    def test_failed_missing_or_unfinished_selected_run_is_replaced(self):
        self.write_child()
        for state in ('failed', 'running', 'unknown', 'missing'):
            with self.subTest(selected_state=state):
                previous = [] if state == 'missing' else [dict(rev='r1', state=state)]
                store.update(self.pid, lambda data: data['runs'].update(
                    {self.sid: previous + [copy.deepcopy(self.new_run)]}))
                self.select('r1')
                jimmy_bridge.refresh(self.pid, self.sid, 'r2')
                self.assertEqual(self.saved_segment().get('selected_run'), 'r2')

    def test_unfinished_or_failed_refresh_does_not_select_even_with_prior_result(self):
        for phase in ('running', 'failed', 'submission_outcome_unknown'):
            with self.subTest(phase=phase):
                store.update(self.pid, lambda data: data['runs'].update(
                    {self.sid: [{**self.new_run, 'result': 'clips/old.mp4'}]}))
                self.write_child(phase=phase)
                jimmy_bridge.refresh(self.pid, self.sid, 'r2')
                self.assertNotIn('selected_run', self.saved_segment())

    def test_completed_without_result_does_not_select(self):
        self.write_child(with_result=False)
        jimmy_bridge.refresh(self.pid, self.sid, 'r2')
        self.assertEqual(store.load(self.pid)['runs'][self.sid][0]['state'], 'completed')
        self.assertNotIn('selected_run', self.saved_segment())

    def test_already_completed_result_without_selection_can_recover_on_refresh(self):
        store.update(self.pid, lambda data: data['runs'][self.sid][0].update(state='completed'))
        self.write_child()
        jimmy_bridge.refresh(self.pid, self.sid, 'r2')
        self.assertEqual(self.saved_segment().get('selected_run'), 'r2')

    def test_legacy_run_does_not_change_director_selection(self):
        store.update(self.pid, lambda data: data['runs'][self.sid][0].pop('kind'))
        before = copy.deepcopy(store.load(self.pid)['director'])
        self.write_child()
        jimmy_bridge.refresh(self.pid, self.sid, 'r2')
        data = store.load(self.pid)
        self.assertEqual(data['director'], before)
        self.assertEqual(data['runs'][self.sid][0]['state'], 'completed')
        self.assertEqual(data['runs'][self.sid][0]['result'], 'clips/r2.mp4')

    def test_refresh_does_not_recreate_a_deleted_segment_or_director(self):
        self.write_child()
        for remove_director in (False, True):
            with self.subTest(remove_director=remove_director):
                def remove(data):
                    data['director']['segments'] = []
                    if remove_director:
                        data.pop('director')
                store.update(self.pid, remove)
                jimmy_bridge.refresh(self.pid, self.sid, 'r2')
                data = store.load(self.pid)
                if remove_director:
                    self.assertNotIn('director', data)
                else:
                    self.assertEqual(data['director']['segments'], [])

    def test_refresh_reads_latest_selection_inside_storage_update(self):
        self.old_run()
        self.write_child()
        real_update = store.update
        injected = False

        def update(pid, callback):
            nonlocal injected
            if not injected:
                injected = True
                real_update(pid, lambda data: data['director']['segments'][0].update(selected_run='r1'))
            return real_update(pid, callback)

        with patch.object(store, 'update', side_effect=update):
            jimmy_bridge.refresh(self.pid, self.sid, 'r2')
        self.assertTrue(injected)
        self.assertEqual(self.saved_segment()['selected_run'], 'r1')


if __name__ == '__main__':
    unittest.main()
