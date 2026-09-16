"""英文翻译器。

翻译最危险的失败不是"翻错"，是**结构被改**和**输出被截断**——两者都不会报错，
页面照常渲染，只是表格塌了或者后半截没了。所以这里主要测结构指纹与守卫。
"""
import unittest, urllib.error
from _base import bindir; bindir()
import translate


class TestShape(unittest.TestCase):
    def test_counts_what_matters(self):
        md = ('# T\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n'
              '见 [x](https://a.com) 与 [y](https://b.com)\n\n```\ncode\n```\n')
        s = translate.shape(md)
        self.assertEqual(s['tables'], 3)     # 表头 + 对齐行 + 1 行数据
        self.assertEqual(s['links'], 2)
        self.assertEqual(s['headings'], 1)
        self.assertEqual(s['fences'], 2)

    def test_translation_preserving_structure_matches(self):
        zh = '# 标题\n\n| 名称 | 评分 |\n|---|---:|\n| 电影 | 8.1 |\n\n[链接](https://a.com)\n'
        en = '# Title\n\n| Name | Rating |\n|---|---:|\n| Film | 8.1 |\n\n[link](https://a.com)\n'
        self.assertEqual(translate.shape(zh), translate.shape(en))

    def test_dropped_table_row_is_detectable(self):
        zh = '| a |\n|---|\n| 1 |\n| 2 |\n'
        en = '| a |\n|---|\n| 1 |\n'
        self.assertNotEqual(translate.shape(zh)['tables'], translate.shape(en)['tables'])

    def test_mangled_link_is_detectable(self):
        # 模型有时会"顺手"把 URL 也翻了
        zh = '[豆瓣](https://movie.douban.com/subject/1/)\n'
        en = '[Douban](https://movie.douban.example/subject/1/)\n'
        self.assertEqual(translate.shape(zh)['links'], translate.shape(en)['links'])  # 数量相同
        # 数量测不出替换，所以正文里的 URL 集合也要比——这条在 run_task 之外由人工抽查
        import re
        self.assertNotEqual(set(re.findall(r'https?://\S+', zh)),
                            set(re.findall(r'https?://\S+', en)))


class TestGuards(unittest.TestCase):
    def test_plain_http_is_refused(self):
        orig = translate.conf
        translate.conf = lambda k, d='': {'FALLBACK_BASE_URL': 'http://api.example.com',
                                          'FALLBACK_AUTH_TOKEN': 'x' * 20,
                                          'FALLBACK_MODEL': 'm'}.get(k, '')
        try:
            with self.assertRaises(SystemExit) as cm:
                translate.call('hi')
            self.assertIn('https', str(cm.exception))
        finally:
            translate.conf = orig

    def test_redirects_are_not_followed(self):
        """urllib 跟随跨主机 302 时会把请求头原样带上——实测 x-api-key 与
        Authorization 都会被转发。头名在这件事上没区别，唯一可靠的做法是不跟随。"""
        h = translate._NoRedirect()

        class R:
            full_url = 'https://api.example.com/v1/messages'
        with self.assertRaises(urllib.error.HTTPError) as cm:
            h.redirect_request(R(), None, 302, 'Found', {}, 'https://evil.tld/x')
        self.assertIn('evil.tld', str(cm.exception.reason))

    def test_missing_config_exits_clearly(self):
        orig = translate.conf
        translate.conf = lambda k, d='': ''
        try:
            with self.assertRaises(SystemExit):
                translate.call('hi')
        finally:
            translate.conf = orig


if __name__ == '__main__':
    unittest.main()
