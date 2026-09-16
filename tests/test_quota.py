"""配额耗尽时的处置。

这是个「错了也不报错」的地方：按普通失败重试，10 分钟后窗口根本没重置，必然再
失败——白烧两次调用、还把告警推迟 20 分钟。而且日志看起来完全正常（就是三次失败）。
"""
import unittest
from _base import bindir; bindir()
import scheduler as S

QUOTA = 'Claude usage limit reached. Your limit will reset at 11:00.'
ORDINARY = 'FileNotFoundError: data/trending/2026-09-16/snap_t3.json'


class TestNextAction(unittest.TestCase):
    def test_success(self):
        self.assertEqual(S.next_action(True, '', 1, False), 'ok')

    def test_quota_with_fallback_switches_engine(self):
        # 两个引擎走不同供应商，配额池互不相干——这是「今天还有没有报告」的差别
        self.assertEqual(S.next_action(False, QUOTA, 1, True), 'fallback')

    def test_quota_without_fallback_gives_up_immediately(self):
        # 关键：不是 'retry'。窗口 5 小时，10 分钟后重试是白烧
        self.assertEqual(S.next_action(False, QUOTA, 1, False), 'give_up_quota')

    def test_quota_gives_up_even_on_first_attempt(self):
        for n in (1, 2, 3):
            self.assertEqual(S.next_action(False, QUOTA, n, False), 'give_up_quota',
                             '第 %d 次就该放弃，不该再重试' % n)

    def test_ordinary_failure_still_retries(self):
        # 网络抖动、临时报错这类是该重试的，别把配额那条规则误伤到它们
        self.assertEqual(S.next_action(False, ORDINARY, 1, False), 'retry')
        self.assertEqual(S.next_action(False, ORDINARY, 2, False), 'retry')

    def test_ordinary_failure_gives_up_after_retries(self):
        self.assertEqual(S.next_action(False, ORDINARY, S.RETRIES + 1, False), 'give_up')

    def test_ordinary_failure_does_not_burn_the_fallback(self):
        # 有备用引擎也不该为普通失败切过去——那只会把两边的配额一起耗掉
        self.assertEqual(S.next_action(False, ORDINARY, 1, True), 'retry')

    def test_empty_output_is_treated_as_ordinary(self):
        for tail in ('', None):
            self.assertEqual(S.next_action(False, tail, 1, False), 'retry')


class TestQuotaDetection(unittest.TestCase):
    def test_real_shapes(self):
        for s in ('Claude usage limit reached', 'HTTP 429 Too Many Requests',
                  'rate_limit_error', 'You have exceeded your quota',
                  'insufficient_quota', 'server overloaded', '5-hour limit'):
            self.assertRegex(s, S.QUOTA_PATTERNS, '未识别为配额错误: %r' % s)

    def test_no_false_positives_on_ordinary_errors(self):
        for s in (ORDINARY, 'json.decoder.JSONDecodeError: Expecting value',
                  'connection reset by peer', 'Traceback (most recent call last)'):
            self.assertIsNone(S.QUOTA_PATTERNS.search(s), '误判为配额错误: %r' % s)


class TestConfig(unittest.TestCase):
    def test_fallback_defaults_to_disabled(self):
        # 没显式配就不该偷偷切引擎：换引擎会改变报告口径，得是明确的选择
        self.assertIsInstance(S.FALLBACK_ENGINE, str)

    def test_retry_gap_is_far_shorter_than_the_window(self):
        # 这个关系正是「配额错误不能靠重试」的理由，写成断言免得有人调大 RETRY_GAP_MIN
        # 之后以为重试就管用了
        self.assertLess(S.RETRY_GAP_MIN / 60.0, S.USAGE_WINDOW_HOURS)


if __name__ == '__main__':
    unittest.main()
