"""对外可见范围（PUBLIC_DAYS / build_site --days）。

这条约束是「线上只能看最近 N 天」。容易只做一半：少放链接、但文件还在。
桶是 ObjectReadWithoutList，别人列不出清单，可 2026-09-02.html 这种地址是能猜的，
所以必须连页面本身都不生成、已发布的还要删掉。这里守生成这一半（删的那一半在
backup_oci.sh 的 prune_public，靠「只认 YYYY-MM-DD.html」+「本地为空就不删」兜底）。

跑真的 build_site 子进程：它一导入就扫目录写站点，没法 import。
"""
import os, re, shutil, subprocess, tempfile, unittest
from _base import bindir
ROOT = bindir()
PY = os.path.join(ROOT, '.venv', 'bin', 'python')
REPORTS = os.path.join(ROOT, 'reports')
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}\.html$')


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

    def test_local_full_build_is_unaffected(self):
        # 限窗只针对发布；本地与私有桶备份始终全量
        self.assertGreaterEqual(len(self.pages(self.build())), len(self.pages(self.build('--days', '3'))))


if __name__ == '__main__':
    unittest.main()
