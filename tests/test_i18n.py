"""三语文案表。

繁中由简中经 OpenCC 推导，所以只有简中与英文是手写的——漏翻英文不会报错，
只会在页面上留一句中文，所以要靠用例把两边键集合钉住。
"""
import re, unittest
from _base import bindir; bindir()
import i18n


class TestTable(unittest.TestCase):
    def test_every_key_has_both_handwritten_languages(self):
        for k, v in i18n.S.items():
            self.assertEqual(len(v), 2, k)
            self.assertTrue(v[0].strip(), '%s 缺简中' % k)
            self.assertTrue(v[1].strip(), '%s 缺英文' % k)

    def test_all_langs_have_the_same_keys(self):
        base = set(i18n.S)
        for lang in i18n.LANGS:
            self.assertEqual(set(i18n.table(lang)), base, lang)

    def test_format_placeholders_match_across_languages(self):
        # 占位符对不上会在运行时抛 TypeError，而且只在某一种语言下触发
        rx = re.compile(r'%[sd]')
        for k, (zh, en) in i18n.S.items():
            self.assertEqual(rx.findall(zh), rx.findall(en), '%s 的占位符不一致' % k)
            tw = i18n.table('zh-TW')[k]
            self.assertEqual(rx.findall(zh), rx.findall(tw), '%s 繁中占位符被转坏了' % k)

    def test_english_has_no_han_characters(self):
        # 漏翻的表现就是英文项里还留着汉字
        han = re.compile(r'[一-鿿]')
        for k, (_zh, en) in i18n.S.items():
            found = han.findall(en)
            # 平台名保留原文是有意的（Douban (豆瓣) 这种），只放行括号内的
            leftover = [c for c in found if not re.search(r'[（(][^）)]*%s' % re.escape(c), en)]
            self.assertFalse(leftover, '%s 的英文里还有汉字: %s' % (k, ''.join(found)))

    def test_unknown_language_is_rejected(self):
        with self.assertRaises(ValueError):
            i18n.table('ja')

    def test_missing_key_raises(self):
        # 静默退回会让漏翻的地方一直没人发现
        T = i18n.translator('en')
        with self.assertRaises(KeyError):
            T('no.such.key')


class TestTraditional(unittest.TestCase):
    def test_is_actually_converted(self):
        tw = i18n.table('zh-TW')
        if tw['site.name'] == i18n.S['site.name'][0]:
            self.skipTest('未装 opencc，繁中退化为简中')
        self.assertEqual(tw['site.name'], '每日簡報')

    def test_taiwan_vocabulary_not_just_characters(self):
        # s2twp 做的是用词转换。只换字的话「软件」会变成「軟件」而不是「軟體」
        if not i18n._cc:
            self.skipTest('未装 opencc')
        for simp, trad in (('软件', '軟體'), ('网络', '網路'), ('内存', '記憶體'),
                           ('鼠标', '滑鼠'), ('缓存', '快取'), ('数据库', '資料庫')):
            self.assertEqual(i18n._cc.convert(simp), trad)

    def test_body_conversion_leaves_other_langs_alone(self):
        src = '软件与网络'
        self.assertEqual(i18n.convert_body(src, 'zh-CN'), src)
        self.assertEqual(i18n.convert_body(src, 'en'), src)   # 英文正文是独立文件，不走这里

    def test_body_conversion_preserves_markdown_and_urls(self):
        if not i18n._cc:
            self.skipTest('未装 opencc')
        src = '| 软件 | 链接 |\n|---|---|\n| `code` | [x](https://a.com/数据) |\n'
        out = i18n.convert_body(src, 'zh-TW')
        self.assertEqual(src.count('|'), out.count('|'))
        self.assertEqual(src.count('`'), out.count('`'))
        self.assertIn('https://a.com/', out)
        self.assertIn('軟體', out)


if __name__ == '__main__':
    unittest.main()
