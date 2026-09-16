"""发布前的凭据扫描。

这道防护存在的理由：分析层是唯一接触外部不可信文本的环节，而它的产出会公开发布。
沙箱禁网能挡住「把令牌发出去」，挡不住「把令牌写进报告」——报告第二天就在站点上了。
ENGINE=api 时令牌走环境变量，模型在沙箱里看不看得到探不出来，所以按最坏情况兜底。

漏报（真凭据没拦住）比误报严重得多，所以形状规则宁可宽一点。
"""
import os, tempfile, unittest
from _base import bindir; bindir()
import leakcheck


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, text):
        p = os.path.join(self.tmp.name, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(text)
        return p

    def scan(self):
        return leakcheck.scan([self.tmp.name])


class TestShapes(Base):
    def test_catches_common_credential_shapes(self):
        # 全部是编造的形状样本，不要用任何真实凭据派生的串——这个仓库是公开的，
        # 测试文件本身也会被读到。字面量用 'EXAMPLE'/'0123456789' 这类一眼假的填充。
        cases = {
            'anthropic.md': 'key: sk-ant-api03-EXAMPLEEXAMPLEEXAMPLEEXAMPLE',
            'openai.md':    'key: sk-EXAMPLE0123456789EXAMPLE0123456789',
            'github.md':    'ghp_EXAMPLE0123456789EXAMPLE0123456789',
            'cf.md':        'cfut_EXAMPLE0123456789EXAMPLE0123456789',
            'aws.md':       'AKIAIOSFODNN7EXAMPLE',      # AWS 文档里的官方示例值
            'feishu.md':    'https://open.larksuite.com/open-apis/bot/v2/hook/'
                            '00000000-0000-0000-0000-000000000000',
            'pem.md':       '-----BEGIN RSA PRIVATE KEY-----',
        }
        for name, body in cases.items():
            self.write(name, body)
        found = {os.path.basename(p) for p, _l, _w in self.scan()}
        self.assertEqual(found, set(cases), '漏掉: %s' % (set(cases) - found))

    def test_ordinary_report_is_clean(self):
        # 报告里天天有链接、代码块、长串英文，不能一惊一乍
        self.write('r.md', '# 日报\n\n- [仓库](https://github.com/foo/bar) ★12.3k\n'
                           '`npm ci --no-audit` 与 `sha256:abcdef0123456789abcdef0123456789`\n'
                           '提交 a3b7179da8d10b5f，版本 v1.2.3-rc4\n')
        self.assertEqual(self.scan(), [])

    def test_only_scans_text_outputs(self):
        self.write('x.png', 'ghp_EXAMPLE0123456789EXAMPLE0123456789')
        self.assertEqual(self.scan(), [])


class TestReporting(Base):
    def test_reports_location_not_content(self):
        # 把命中的凭据打进日志就自相矛盾了
        secret = 'ghp_EXAMPLE0123456789EXAMPLE0123456789'
        self.write('a.md', 'x\ny\n' + secret)
        hits = self.scan()
        self.assertEqual(len(hits), 1)
        path, line, what = hits[0]
        self.assertEqual(line, 3)
        self.assertNotIn(secret, what)
        self.assertNotIn(secret, path)


class TestConfSecrets(unittest.TestCase):
    def test_short_values_are_ignored(self):
        # GITHUB_TOKEN=none 这种空/占位值不能当凭据，否则会命中一切
        for v in leakcheck.conf_secrets().values():
            self.assertGreaterEqual(len(v), 12)

    def test_keys_cover_what_config_actually_holds(self):
        # 配置里新增凭据项时，这里也要跟上——漏了就等于这道防护对它不生效
        conf = os.path.join(leakcheck.ROOT, 'bin', 'config.conf')
        if not os.path.exists(conf):
            self.skipTest('本机无 config.conf')
        keys = {ln.split('=', 1)[0].strip()
                for ln in open(conf, encoding='utf-8')
                if '=' in ln and not ln.strip().startswith('#')}
        sensitive = {k for k in keys if any(w in k for w in ('TOKEN', 'WEBHOOK', 'KEY', 'SECRET'))}
        self.assertTrue(sensitive <= set(leakcheck.SECRET_KEYS),
                        '配置里这些凭据项没被扫描覆盖: %s' % (sensitive - set(leakcheck.SECRET_KEYS)))


if __name__ == '__main__':
    unittest.main()
