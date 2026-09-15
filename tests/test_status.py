"""站点状态条的判定。

这里踩过一个实际的坑：状态原本只读 scheduler.json，而 run_task.sh 在自己末尾
重建并发布站点、调度器要等它退出之后才写状态——站点永远早一步生成，刚跑完的那个
任务的 chip 一直是旧的，要等 5 小时后的下一档才变绿。日志时间戳是 17:02:16 建站、
17:02:48 才写状态。

这种错不会抛异常、不会进日志，只有人盯着页面才看得出来，所以必须有测试守着。
"""
import json, os, tempfile, unittest
from _base import bindir; bindir()
from render import today_state

PANELS = [('ai', 'AI 简报', ['ai-news.md']),
          ('trending', 'GitHub Trending', ['github-trending.md', 'github-trending_draft*.md']),
          ('momoyu', '摸摸鱼热榜', ['momoyu.md']),
          ('douban', '豆瓣电影', ['douban.md'])]
PLAN = {'ai': '06:30', 'douban': '11:35', 'trending': '16:40', 'momoyu': '21:45'}
TODAY = '2026-09-15'


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.reports = os.path.join(self.tmp.name, 'reports')
        os.makedirs(os.path.join(self.reports, TODAY))
        self.sched = os.path.join(self.tmp.name, 'scheduler.json')

    def tearDown(self):
        self.tmp.cleanup()

    def report(self, name):
        with open(os.path.join(self.reports, TODAY, name), 'w') as f:
            f.write('# x')

    def sched_json(self, d):
        with open(self.sched, 'w') as f:
            json.dump(d, f)

    def state(self):
        return today_state(self.reports, self.sched, PANELS, PLAN, today=TODAY)


class TestArtifactWins(Base):
    def test_report_exists_but_scheduler_not_yet_written(self):
        # 这就是那个 bug：报告已经产出，调度器还没来得及写状态
        self.report('github-trending.md')
        self.sched_json({})
        self.assertEqual(self.state()['trending'][0], 'ok')

    def test_report_exists_and_scheduler_still_says_retrying(self):
        # 更刁钻的版本：上一次重试失败过，这次成功了但状态还停在 retrying
        self.report('github-trending.md')
        self.sched_json({'trending': {'date': TODAY, 'status': 'retrying', 'attempts': 1}})
        self.assertEqual(self.state()['trending'][0], 'ok')

    def test_manual_run_without_scheduler_record(self):
        # run_task.sh momoyu 直接跑不经过调度器，scheduler.json 里没有记录
        self.report('momoyu.md')
        self.sched_json({})
        self.assertEqual(self.state()['momoyu'][0], 'ok')

    def test_no_scheduler_file_at_all(self):
        self.report('ai-news.md')
        self.assertEqual(self.state()['ai'][0], 'ok')


class TestSchedulerFillsTheGaps(Base):
    def test_failed_shows_when_no_report(self):
        self.sched_json({'ai': {'date': TODAY, 'status': 'failed', 'attempts': 3}})
        st, tip, _ = self.state()['ai']
        self.assertEqual(st, 'failed')
        self.assertIn('3', tip)

    def test_skipped_shows_when_no_report(self):
        self.sched_json({'momoyu': {'date': TODAY, 'status': 'skipped'}})
        self.assertEqual(self.state()['momoyu'][0], 'skipped')

    def test_pending_mentions_planned_time(self):
        self.sched_json({})
        st, tip, _ = self.state()['momoyu']
        self.assertEqual(st, 'pending')
        self.assertIn('21:45', tip)

    def test_yesterdays_record_does_not_count_as_today(self):
        self.sched_json({'ai': {'date': '2026-09-14', 'status': 'ok'}})
        self.assertEqual(self.state()['ai'][0], 'pending')


class TestDraft(Base):
    def test_draft_only_is_not_ok(self):
        # 早间草稿还没被下午的正式版取代，不该显示成跑完了
        self.report('github-trending_draft1045.md')
        self.assertEqual(self.state()['trending'][0], 'draft')

    def test_final_beats_draft(self):
        self.report('github-trending_draft1045.md')
        self.report('github-trending.md')
        self.assertEqual(self.state()['trending'][0], 'ok')


if __name__ == '__main__':
    unittest.main()
