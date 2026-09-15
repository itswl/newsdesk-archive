"""跨期比对的判重。

「只报增量」这条约定完全建立在这里：判重错了，要么把昨天的旧闻当今日新闻
报出去，要么把真的新条目当旧的滤掉。两种错都不会报异常，只会让报告悄悄失真。
"""
import unittest
from _base import bindir; bindir()
import history as H


class TestNormLink(unittest.TestCase):
    def test_same_url_variants_collapse(self):
        base = 'https://example.com/a/b'
        for v in ('http://example.com/a/b', 'https://www.example.com/a/b',
                  'https://example.com/a/b/', 'https://example.com/a/b?utm_source=x',
                  'https://example.com/a/b#frag', 'HTTPS://EXAMPLE.COM/a/b'):
            self.assertEqual(H.norm_link(v), H.norm_link(base), '未归一: %r' % v)

    def test_different_paths_stay_different(self):
        self.assertNotEqual(H.norm_link('https://example.com/a'),
                            H.norm_link('https://example.com/b'))

    def test_empty_is_safe(self):
        self.assertEqual(H.norm_link(None), '')
        self.assertEqual(H.norm_link(''), '')


class TestNormTitle(unittest.TestCase):
    def test_punctuation_and_space_insensitive(self):
        a = H.norm_title('OpenAI 发布 GPT-6：新一代模型')
        for v in ('OpenAI发布GPT6，新一代模型', 'OpenAI 发布 GPT-6 ： 新一代模型',
                  '【OpenAI】发布 GPT-6：新一代模型'):
            self.assertEqual(H.norm_title(v), a, '未归一: %r' % v)

    def test_different_titles_stay_different(self):
        self.assertNotEqual(H.norm_title('模型 A 发布'), H.norm_title('模型 B 发布'))

    def test_empty_is_safe(self):
        self.assertEqual(H.norm_title(None), '')


class TestWindow(unittest.TestCase):
    def test_window_is_bounded(self):
        # 回看窗口无界会把不更新的源（掘金）的陈年条目一直翻出来，信号被噪声淹掉
        self.assertGreaterEqual(H.WINDOW, 3)
        self.assertLessEqual(H.WINDOW, 14)

    def test_cap_is_bounded(self):
        # 输出只带信号不带清单；CAP 过大等于把全量条目又抄一遍进上下文
        self.assertLessEqual(H.CAP, 30)


if __name__ == '__main__':
    unittest.main()
