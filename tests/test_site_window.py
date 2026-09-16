"""对外可见范围（PUBLIC_DAYS / build_site --days）。

这条约束是「线上只能看最近 N 天」。容易只做一半：少放链接、但文件还在。
桶是 ObjectReadWithoutList，别人列不出清单，可 2026-09-02.html 这种地址是能猜的，
所以必须连页面本身都不生成、已发布的还要删掉。这里守生成这一半（删的那一半在
backup_oci.sh 的 prune_public，靠「只认 YYYY-MM-DD.html」+「本地为空就不删」兜底）。

跑真的 build_site 子进程：它一导入就扫目录写站点，没法 import。
"""
import glob, os, re, shutil, subprocess, tempfile, unittest
from _base import bindir
ROOT = bindir()
PY = os.path.join(ROOT, '.venv', 'bin', 'python')
REPORTS = os.path.join(ROOT, 'reports')
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}\.html$')
PANELS = [('ai', 'AI', ['ai-news.md']),
          ('trending', 'Trending', ['github-trending.md', 'github-trending_draft*.md']),
          ('momoyu', '摸摸鱼', ['momoyu.md']),
          ('douban', '豆瓣', ['douban.md'])]


def all_days():
    if not os.path.isdir(REPORTS):
        return []
    return sorted(d for d in os.listdir(REPORTS) if re.fullmatch(r'\d{4}-\d{2}-\d{2}', d))


@unittest.skipUnless(os.path.exists(PY), '需要项目内 .venv')
@unittest.skipUnless(len(all_days()) >= 3, 'reports/ 不足 3 天，跳过')
class TestWindow(unittest.TestCase):
    def build(self, *extra):
        out = tempfile.mkdtemp(prefix='sitewin-', dir=os.path.join(ROOT, 'state'))
        rel = os.path.relpath(out, ROOT)
        r = subprocess.run([PY, os.path.join(ROOT, 'bin', 'build_site.py'), '--out', rel, *extra],
                           cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        self.addCleanup(shutil.rmtree, out, True)
        return out

    def pages(self, out):
        return sorted(f for f in os.listdir(out) if DATE_RE.match(f))

    def test_days_limits_page_count(self):
        out = self.build('--days', '3')
        self.assertEqual(len(self.pages(out)), 3)

    def test_keeps_the_newest_not_the_oldest(self):
        # 截错方向的话线上会停在最早那几天，而且首页看着「正常」
        out = self.build('--days', '3')
        self.assertEqual([p[:-5] for p in self.pages(out)], all_days()[-3:])

    def test_zero_means_all(self):
        self.assertEqual(len(self.pages(self.build('--days', '0'))), len(all_days()))

    def test_default_is_all(self):
        self.assertEqual(len(self.pages(self.build())), len(all_days()))

    def test_days_larger_than_history_is_harmless(self):
        self.assertEqual(len(self.pages(self.build('--days', '9999'))), len(all_days()))

    def test_archive_does_not_link_outside_the_window(self):
        # 归档页链到没生成的日子 = 点进去 404。范围必须在生成时就一致。
        out = self.build('--days', '3')
        kept = {p[:-5] for p in self.pages(out)}
        linked = set(re.findall(r'href="(\d{4}-\d{2}-\d{2})\.html"',
                                open(os.path.join(out, 'archive.html'), encoding='utf-8').read()))
        self.assertTrue(linked <= kept, '归档页链到窗外: %s' % (linked - kept))

    def test_day_dropdown_does_not_link_outside_the_window(self):
        out = self.build('--days', '3')
        kept = {p[:-5] for p in self.pages(out)}
        linked = set(re.findall(r'<option value="(\d{4}-\d{2}-\d{2})"',
                                open(os.path.join(out, 'index.html'), encoding='utf-8').read()))
        self.assertTrue(linked <= kept, '日期下拉链到窗外: %s' % (linked - kept))

    def test_feed_does_not_reference_outside_the_window(self):
        # feed 是全文推送，条目 id 是页面锚点 URL——指向已下线的日子就成了死链
        out = self.build('--days', '3')
        feed = os.path.join(out, 'feed.xml')
        if not os.path.exists(feed):
            self.skipTest('未配置 SITE_URL，不生成 feed')
        kept = {p[:-5] for p in self.pages(out)}
        linked = set(re.findall(r'/(\d{4}-\d{2}-\d{2})\.html', open(feed, encoding='utf-8').read()))
        self.assertTrue(linked <= kept, 'feed 指向窗外: %s' % (linked - kept))

    def test_notice_shown_only_when_windowed(self):
        # 限窗了就要在页面上说明白，否则访客只会觉得「历史怎么没了」
        win = open(os.path.join(self.build('--days', '3'), 'index.html'), encoding='utf-8').read()
        self.assertIn('class="keep"', win)
        self.assertIn('最近 3 天', win)
        full = open(os.path.join(self.build(), 'index.html'), encoding='utf-8').read()
        self.assertNotIn('class="keep"', full)   # 没限窗就不该有这句话

    def test_archive_wording_is_not_a_lie_when_windowed(self):
        # 「共 N 天」在限窗时读起来像「总共就这些」，而更早的只是下线了
        win = open(os.path.join(self.build('--days', '3'), 'archive.html'), encoding='utf-8').read()
        self.assertIn('最近 3 天', win)
        self.assertNotIn('共 3 天', win)
        full = open(os.path.join(self.build(), 'archive.html'), encoding='utf-8').read()
        self.assertIn('共 %d 天' % len(all_days()), full)

    def test_reports_are_never_touched(self):
        # 限窗只影响 HTML 展示。原文必须原封不动——它是私有备份和重建的唯一来源
        before = set(all_days())
        self.build('--days', '1')
        self.assertEqual(before, set(all_days()))

    def test_feed_covers_every_published_day(self):
        """feed 的范围必须和站点一致。

        以前 FEED_MAX 写死 20，而满负荷时 7 天 × 4 篇 = 28 条——feed 会悄悄
        收窄到 5 天，站点还是 7 天。两个互不相干的常数撞出来的，谁都没决定过，
        而且不会报错，只有逐条数日期才看得出来。
        """
        out = self.build('--days', '5')
        feed = os.path.join(out, 'feed.xml')
        if not os.path.exists(feed):
            self.skipTest('未配置 SITE_URL，不生成 feed')
        page_days = {p[:-5] for p in self.pages(out)}
        feed_days = set(re.findall(r'/(\d{4}-\d{2}-\d{2})\.html', open(feed, encoding='utf-8').read()))
        # 当天目录里一篇报告都没有的日子，页面在但 feed 里没条目，属正常
        has_report = {d for d in page_days
                      if any(glob.glob(os.path.join(REPORTS, d, pat))
                             for _k, _l, pats in PANELS for pat in pats)}
        self.assertEqual(feed_days, has_report,
                         'feed 与站点覆盖的日期不一致：feed 缺 %s' % (has_report - feed_days))

    def test_translated_files_do_not_leak_into_other_languages(self):
        """带通配符的模式会连译文一起匹配到。

        `github-trending_draft*.md` 会匹配 `github-trending_draft0916.en.md`，
        而刚翻完的译文 mtime 最新，`max(mtime)` 就把英文选进了中文站——实测中招过，
        某天的简中页整篇是英文，而且不报错、不进日志，只有逐页看才发现。
        """
        # 找一个既有中文原文、又有对应译文的日子
        day = None
        for d in all_days():
            files = os.listdir(os.path.join(REPORTS, d))
            if any(f.endswith('.en.md') for f in files) and any(
                    f.endswith('.md') and not f.endswith('.en.md') for f in files):
                day = d
                break
        if not day:
            self.skipTest('reports/ 里没有中英并存的日子')
        out = self.build('--lang', 'zh-CN')
        page = open(os.path.join(out, day + '.html'), encoding='utf-8').read()
        body = re.sub(r'<[^>]+>', '', page)
        han = len(re.findall(r'[\u4e00-\u9fff]', body))
        self.assertGreater(han, 200,
                           '简中页只有 %d 个汉字，多半是把 .en.md 选进来了' % han)

    def test_english_build_picks_translated_files(self):
        day = None
        for d in all_days():
            if any(f.endswith('.en.md') for f in os.listdir(os.path.join(REPORTS, d))):
                day = d
                break
        if not day:
            self.skipTest('reports/ 里没有译文')
        out = self.build('--lang', 'en')
        page = open(os.path.join(out, day + '.html'), encoding='utf-8').read()
        # 英文版该有正文，不该整页都是「当天没有这份报告」
        self.assertLess(page.count('class="tab absent"'), 4, '英文版没挑到任何译文')

    def test_local_full_build_is_unaffected(self):
        # 限窗只针对发布；本地与私有桶备份始终全量
        self.assertGreaterEqual(len(self.pages(self.build())), len(self.pages(self.build('--days', '3'))))


if __name__ == '__main__':
    unittest.main()
