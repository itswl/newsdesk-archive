"""哪些语言真的生效。

判定错了不会报错，只会安静地发布一个坏东西：
  英文缺 API 配置 → 构建出所有报告都缺失的空站，照样上线
  繁中缺 opencc   → 跟简中逐字节相同，挂在 /zh-TW/ 下是在骗人
两者都比"没有这个语言"糟糕。
"""
import unittest
from _base import bindir; bindir()
import langs

FULL = {'FALLBACK_BASE_URL': 'https://api.example.com',
        'FALLBACK_AUTH_TOKEN': 'k' * 20, 'FALLBACK_MODEL': 'm'}


def get(d):
    return lambda k, default='': d.get(k, default)


class TestGating(unittest.TestCase):
    def test_en_needs_both_listed_and_configured(self):
        # 配了 key 但没列 en → 不开
        keep, _ = langs.effective('zh-CN', get(FULL), opencc_ok=True)
        self.assertNotIn('en', keep)
        # 列了 en 但没配 key → 不开
        keep, dropped = langs.effective('zh-CN,en', get({}), opencc_ok=True)
        self.assertNotIn('en', keep)
        self.assertTrue(any(c == 'en' for c, _w in dropped))
        # 两个都满足 → 开
        keep, _ = langs.effective('zh-CN,en', get(FULL), opencc_ok=True)
        self.assertIn('en', keep)

    def test_partial_api_config_is_not_enough(self):
        for missing in FULL:
            partial = {k: v for k, v in FULL.items() if k != missing}
            keep, dropped = langs.effective('zh-CN,en', get(partial), opencc_ok=True)
            self.assertNotIn('en', keep, '缺 %s 时不该开启' % missing)
            self.assertIn(missing, dict(dropped)['en'])

    def test_zh_tw_needs_opencc(self):
        keep, dropped = langs.effective('zh-CN,zh-TW', get(FULL), opencc_ok=False)
        self.assertNotIn('zh-TW', keep)
        self.assertIn('opencc', dict(dropped)['zh-TW'])
        keep, _ = langs.effective('zh-CN,zh-TW', get(FULL), opencc_ok=True)
        self.assertIn('zh-TW', keep)


class TestDefaults(unittest.TestCase):
    def test_empty_means_simplified_only(self):
        # 不配就只发简体——多语言必须是主动开启的
        for v in ('', None, '   '):
            keep, _ = langs.effective(v, get(FULL), opencc_ok=True)
            self.assertEqual(keep, ['zh-CN'])

    def test_first_stays_first(self):
        # 第一个语言放根目录，顺序不能被打乱
        keep, _ = langs.effective('en,zh-CN,zh-TW', get(FULL), opencc_ok=True)
        self.assertEqual(keep[0], 'en')

    def test_unknown_code_is_dropped_not_fatal(self):
        keep, dropped = langs.effective('zh-CN,ja,en', get(FULL), opencc_ok=True)
        self.assertEqual(keep, ['zh-CN', 'en'])
        self.assertIn('ja', dict(dropped))

    def test_duplicates_collapse(self):
        keep, _ = langs.effective('zh-CN,zh-CN,en,en', get(FULL), opencc_ok=True)
        self.assertEqual(keep, ['zh-CN', 'en'])

    def test_never_returns_empty(self):
        # 全被剔掉也得有个默认语言，站点不能不出
        keep, _ = langs.effective('en,zh-TW', get({}), opencc_ok=False)
        self.assertEqual(keep, ['zh-CN'])

    def test_dropped_reasons_are_actionable(self):
        _keep, dropped = langs.effective('en,zh-TW', get({}), opencc_ok=False)
        for code, why in dropped:
            self.assertGreater(len(why), 15, '%s 的原因说不清该做什么' % code)


if __name__ == '__main__':
    unittest.main()
