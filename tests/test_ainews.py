"""AI 简报采集里最脆的两块：Anthropic 页面解析 与 日期归一。

Anthropic 官网没有 RSS，只能解析 HTML，而 HTML 会改版。这个源坏掉的方式
是**安静的**——解析不到就是 0 条，混在 15 个源里没人会注意少了一个。
所以既测两套布局都认得，也测「解析出 0 条时必须报失败而不是返回空」。
"""
import unittest
from _base import bindir; bindir()
import fetch_ainews as F

# 两套真实布局的骨架（class 名里的构建哈希会随改版变，所以代码按 class 含
# "title" 匹配而不认完整 class 名——这里就用改过的哈希来验证这一点）
FEATURED_GRID = '''
<a href="/news/claude-opus-5" class="Card_link__9a3f2">
  <time datetime="2026-09-10">Sep 10, 2026</time>
  <h4 class="Card_title__88bc1">Introducing Claude Opus 5</h4>
  <p class="Card_desc__11aa">A new frontier model.</p>
</a>'''
PUBLICATION_LIST = '''
<a href="/news/economic-index-update" class="PostList_item__ff00">
  <time datetime="2026-09-08">Sep 8, 2026</time>
  <span class="PostList_title__77ee">Anthropic Economic Index update</span>
  <p>Fresh data on AI adoption.</p>
</a>'''


class TestAnthropicParse(unittest.TestCase):
    def parse(self, html):
        orig = F.curl
        F.curl = lambda *a, **k: html
        try:
            return F.fetch_anthropic()
        finally:
            F.curl = orig

    def test_featured_grid_h4_layout(self):
        got = self.parse(FEATURED_GRID)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]['title'], 'Introducing Claude Opus 5')
        self.assertEqual(got[0]['link'], 'https://www.anthropic.com/news/claude-opus-5')

    def test_publication_list_span_layout(self):
        got = self.parse(PUBLICATION_LIST)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]['title'], 'Anthropic Economic Index update')

    def test_both_layouts_on_one_page(self):
        got = self.parse(FEATURED_GRID + PUBLICATION_LIST)
        self.assertEqual(len(got), 2)

    def test_class_hash_change_does_not_break_it(self):
        # 改版只改构建哈希时必须照常认得——这是按 class 含 title 匹配的全部理由
        got = self.parse(FEATURED_GRID.replace('Card_title__88bc1', 'Card_title__zzzz9'))
        self.assertEqual(len(got), 1)

    def test_duplicate_entries_deduped(self):
        # 同一条目在首屏和列表里各出现一次
        self.assertEqual(len(self.parse(FEATURED_GRID * 2)), 1)

    def test_zero_parsed_returns_none_not_empty(self):
        # 关键：返回 [] 会被当成「今天没有新闻」静默吞掉，返回 None 才会上报失败
        self.assertIsNone(self.parse('<html><body>改版了，什么都匹配不到</body></html>'))

    def test_fetch_failure_returns_none(self):
        self.assertIsNone(self.parse(''))


class TestNormDate(unittest.TestCase):
    def test_common_shapes_parse(self):
        for s in ('2026-09-10', 'Thu, 10 Sep 2026 12:00:00 +0000', '2026-09-10T12:00:00Z'):
            self.assertTrue(F.norm_date(s), '解析失败: %r' % s)

    def test_garbage_does_not_raise(self):
        for s in ('', None, '不是日期', '99-99-99'):
            F.norm_date(s)      # 不抛异常即可；采集不能因为一条脏日期整个挂掉


if __name__ == '__main__':
    unittest.main()
