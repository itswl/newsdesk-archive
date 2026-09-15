"""调度的边界数学。

这些判断只在「睡过头」「跨日」「刚好卡在截止点」时才走到，正常日子一次都
碰不到——线上跑一个月也未必暴露，但错了就是任务默默不跑或者两个任务挤进
同一个用量窗口。属于典型的「改一行看着没事、跑一周才发现」。
"""
import datetime, unittest
from _base import bindir; bindir()
import scheduler as S


class TestNextSlot(unittest.TestCase):
    def setUp(self):
        self.orig = S.SCHEDULE
        S.SCHEDULE = [('06:30', 'ai'), ('11:35', 'douban'),
                      ('16:40', 'trending'), ('21:45', 'momoyu')]

    def tearDown(self):
        S.SCHEDULE = self.orig

    def test_next_slot_within_day(self):
        d = datetime.date(2026, 9, 15)
        self.assertEqual(S.next_slot_after('06:30', d),
                         datetime.datetime(2026, 9, 15, 11, 35))

    def test_last_slot_wraps_to_next_day(self):
        # 最后一档的「下一档」是次日第一档；算错会让补跑窗口变成负数，
        # 结果是最后一档永远立刻被判超时跳过
        d = datetime.date(2026, 9, 15)
        self.assertEqual(S.next_slot_after('21:45', d),
                         datetime.datetime(2026, 9, 16, 6, 30))

    def test_wrap_across_month_end(self):
        d = datetime.date(2026, 9, 30)
        self.assertEqual(S.next_slot_after('21:45', d),
                         datetime.datetime(2026, 10, 1, 6, 30))

    def test_wrap_across_year_end(self):
        d = datetime.date(2026, 12, 31)
        self.assertEqual(S.next_slot_after('21:45', d),
                         datetime.datetime(2027, 1, 1, 6, 30))

    def test_deadline_is_before_next_slot(self):
        # 补跑截止点必须严格早于下一档，否则补跑会和下一档挤同一个用量窗口
        for t, _ in S.SCHEDULE:
            nxt = S.next_slot_after(t, datetime.date(2026, 9, 15))
            deadline = nxt - datetime.timedelta(minutes=S.CATCHUP_MARGIN_MIN)
            self.assertLess(deadline, nxt)
            self.assertGreater(deadline, S.due_at(t, datetime.date(2026, 9, 15)))


class TestGaps(unittest.TestCase):
    def test_gaps_sum_to_a_day(self):
        S.SCHEDULE = [('06:30', 'a'), ('11:35', 'b'), ('16:40', 'c'), ('21:45', 'd')]
        self.assertAlmostEqual(sum(S.schedule_gaps()), 24.0, places=6)

    def test_current_schedule_passes(self):
        S.SCHEDULE = [('06:30', 'ai'), ('11:35', 'douban'),
                      ('16:40', 'trending'), ('21:45', 'momoyu')]
        self.assertEqual(S.validate_schedule(), [])


class TestValidate(unittest.TestCase):
    def setUp(self):
        self.orig = (S.SCHEDULE, S.CATCHUP_MARGIN_MIN, S.TASK_TIMEOUT_MIN)

    def tearDown(self):
        S.SCHEDULE, S.CATCHUP_MARGIN_MIN, S.TASK_TIMEOUT_MIN = self.orig

    def test_too_tight_is_rejected(self):
        S.SCHEDULE = [('06:00', 'a'), ('10:00', 'b'), ('14:00', 'c'), ('18:00', 'd')]
        self.assertTrue(S.validate_schedule())

    def test_exactly_window_is_rejected_no_margin(self):
        # 刚好 5 小时整会卡在窗口边界上，SLOT_EDGE_MARGIN_MIN 就是为这个留的
        S.SCHEDULE = [('06:00', 'a'), ('11:00', 'b'), ('16:00', 'c'), ('21:00', 'd')]
        self.assertTrue(S.validate_schedule())

    def test_wider_than_window_is_fine(self):
        # 6 小时整比窗口宽，是安全的。（旧注释说它「会让相邻两档共用同一个
        # 窗口」——那句话是错的，这个用例把正确的语义钉住。）
        S.SCHEDULE = [('06:00', 'a'), ('12:00', 'b'), ('18:00', 'c'), ('00:00', 'd')]
        self.assertEqual(S.validate_schedule(), [])

    def test_catchup_margin_larger_than_gap_is_rejected(self):
        S.SCHEDULE = [('06:00', 'a'), ('11:05', 'b'), ('16:10', 'c'), ('21:15', 'd')]
        S.CATCHUP_MARGIN_MIN = 400
        self.assertTrue(S.validate_schedule())

    def test_timeout_larger_than_gap_is_rejected(self):
        S.SCHEDULE = [('06:00', 'a'), ('11:05', 'b'), ('16:10', 'c'), ('21:15', 'd')]
        S.TASK_TIMEOUT_MIN = 400
        self.assertTrue(S.validate_schedule())


class TestQuotaPatterns(unittest.TestCase):
    def test_matches_real_shapes(self):
        for s in ('Claude usage limit reached', 'HTTP 429 Too Many Requests',
                  'rate_limit_error', 'You have exceeded your quota',
                  'server overloaded', '5-hour limit'):
            self.assertRegex(s, S.QUOTA_PATTERNS, '未识别为配额错误: %r' % s)

    def test_does_not_match_ordinary_failure(self):
        for s in ('FileNotFoundError: data/trending', 'json.decoder.JSONDecodeError',
                  'connection reset by peer'):
            self.assertIsNone(S.QUOTA_PATTERNS.search(s), '误判为配额错误: %r' % s)


if __name__ == '__main__':
    unittest.main()
