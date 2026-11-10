"""SB3g reference roles, with synthetic fixtures and no model requests."""
import unittest

from console import director, segment_plan
from tools import test_director_sb3 as fixtures
from tools.v2_fixture import choose


VIDEO_REFERENCE = (
    '@video1 is the original footage of this part, time-stretched evenly to exactly {n} seconds. '
    "Follow it for the scene layout, camera angles and movement, shot order and cut timing, every person's "
    'body motion, and how the product is picked up, presented, worn, pressed and handled, with the same rhythm. '
    'The appearance of every person and of the product comes only from the storyboard and the photos: '
    'replace the original people, the original product, its packaging and all on-screen text from @video1; '
    'never let them appear.'
)
VIDEO_ROLE = (
    '原片视频负责场景、机位与运镜、镜头顺序、人物动作，以及商品被拿起、展示、佩戴、按压的方式和节奏；'
    '人和商品的外观只以新分镜和照片为准。'
)
EMPIRICAL_FACTS = (
    '实测：只换人物、商品不变时，原片视频能保住动作和商品结构；但新分镜里没有改到的画面'
    '（例如只露出手和手臂的特写），旧人物的手臂会回来。换了商品时，含旧商品画面的段落如果传原片视频，'
    '旧商品、旧包装、旧脸都会回来，参考图压不住，这类段落用 image。'
)


class DirectorSB3gTests(unittest.TestCase):
    setUp = fixtures.DirectorSB3Tests.setUp
    fixture = fixtures.DirectorSB3Tests.fixture
    segment = fixtures.DirectorSB3Tests.segment
    plan = fixtures.DirectorSB3Tests.plan

    def test_video_reference_exact_text_matches_duration_and_stretched_clock(self):
        for seconds,target in ((.7,4),(5.333,6),(9.4,10),(12.3,13),(15,15)):
            with self.subTest(seconds=seconds):
                data=self.fixture(2,seconds);plan=self.plan(data,[['c01'],['c02']])
                rows=director.validate_plan(plan,data,self.d)['segments']
                for index,row in enumerate(rows):
                    self.assertEqual(row['duration'],target);self.assertEqual(row['target_seconds'],target)
                    self.assertAlmostEqual(row['source_start'],index*seconds);self.assertAlmostEqual(row['source_seconds'],seconds)
                    self.assertAlmostEqual(row['stretch'],target/seconds);self.assertAlmostEqual(row['source_seconds']*row['stretch'],target)
                    video=director.generation_materials(data,row,self.d)[-1]
                    self.assertEqual(video['label'],'视频1')
                    self.assertEqual(video['description'],f"原片 {row['source_start']:g}–{row['source_end']:g} 秒，均匀拉伸到 {target} 秒")
                    self.assertFalse(row['needs_rewrite'])

    def test_image_mode_has_no_video_reference_and_keeps_other_sections(self):
        data=self.fixture(3,4.1);video=director.validate_plan(self.plan(data),data,self.d)['segments'][0]
        choose(data,self.d,'d1',mode='image');image=director.validate_plan(self.plan(data),data,self.d)['segments'][0]
        self.assertNotIn('视频1',image['prompt']);self.assertIn('视频1',video['prompt'])
        self.assertTrue(all(r['type']=='image' for r in director.generation_materials(data,image,self.d)))
        self.assertEqual(image['stretch'],video['stretch']);self.assertEqual(image['duration'],video['duration'])
        self.assertEqual(image['prompt'],video['prompt'].replace('；视频1 只提供场景与动作节奏',''))

    def test_full_and_rewrite_system_explains_video_role_and_preserves_facts(self):
        data=self.fixture(3,4.1);data['director']=self.plan(data)
        for target in (None,data['director']['segments'][0]):
            system=director.messages(data,self.d,target=target)[0]['content']
            self.assertIn('视频1只提供场景、机位运镜、镜头顺序、切点和人物动作节奏',system)
            self.assertIn('商品被拿取、展示和使用的方式和节奏',system)
            self.assertIn('人物外观、衣服、发饰、旧商品和文字一律不用',system)
        for fact in ('只换人物、商品不变时','也要提交目标新画面','清楚可见时，无论大小，传原片视频都有漏回风险','权衡本段动作的价值和漏回风险','在 mode_reason 里说明理由'):
            self.assertIn(fact,segment_plan.SYSTEM)
        self.assertNotIn('原片视频安全',segment_plan.SYSTEM);self.assertNotIn(EMPIRICAL_FACTS,director.SYSTEM)


if __name__ == '__main__':
    unittest.main()
