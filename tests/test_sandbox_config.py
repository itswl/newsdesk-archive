"""沙箱配置的静态不变量。

沙箱有 bin/sandbox-selftest.sh 做实测（那个更权威，但要起 sandbox-exec、
比较慢，而且依赖本机装了哪些工具）。这里只钉住几条静态不变量，让「防护
开关被悄悄改弱」在 CI 或本地跑测试时立刻暴露。
"""
import json, os, subprocess, unittest
from _base import bindir
ROOT = bindir()


def render(engine='claude'):
    """用真实脚本渲染一份权限文件，测的是实际产物而不是模板。"""
    out = os.path.join(ROOT, 'state', 'test-agent-settings.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    script = (
        'set -e; ROOT=%s; export ROOT HOME; . "$ROOT/bin/sandbox-paths.sh"; '
        '{ sbx_deny_dirs_for %s; printf "%%s\\n" "${SBX_DENY_FILES[@]}"; } | '
        'python3 "$ROOT/bin/render_settings.py" "$ROOT/bin/agent-settings.template.json" '
        '"%s" "$HOME" "$ROOT"' % (ROOT, engine, out)
    )
    subprocess.run(['/bin/bash', '-c', script], check=True, capture_output=True)
    with open(out) as f:
        return json.load(f)


class TestGeneratedSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = render()

    def test_sandbox_is_fail_closed(self):
        sb = self.cfg['sandbox']
        self.assertTrue(sb['enabled'])
        # 沙箱起不来必须整个拒绝运行，不能静默降级成无防护
        self.assertTrue(sb['failIfUnavailable'])
        # 为 true 时 dangerouslyDisableSandbox 参数可以绕过防护
        self.assertFalse(sb['allowUnsandboxedCommands'])

    def test_analysis_layer_has_no_network(self):
        # 所有联网都在采集层。分析层一旦能出网，沙箱拒读凭据就没意义了
        net = self.cfg['sandbox']['network']
        self.assertEqual(net['allowedDomains'], [])
        self.assertTrue(net['strictAllowlist'])

    def test_cannot_rewrite_own_scripts(self):
        dw = self.cfg['sandbox']['filesystem']['denyWrite']
        for p in ('bin', '.git'):
            self.assertTrue(any(x.endswith('/' + p) for x in dw), '%s 可被改写' % p)

    def test_known_credential_paths_denied(self):
        deny = ' '.join(self.cfg['sandbox']['filesystem']['denyRead'])
        for p in ('.oci', '.ssh', '.aws', '.config/gh', '.gnupg', 'Keychains'):
            self.assertIn(p, deny, '%s 未拒读' % p)

    def test_claude_json_sibling_file_is_covered(self):
        # ~/.claude.json 与 ~/.claude 同级、不在其下，按目录拒会漏掉它。
        # 这正是「两份手写清单」时代漏掉的那一条。
        deny = self.cfg['sandbox']['filesystem']['denyRead']
        self.assertTrue(any(x.endswith('/.claude.json') for x in deny))

    def test_every_deny_path_has_a_matching_read_rule(self):
        # denyRead（沙箱层）与 Read() 拒绝规则（工具层）必须一一对应，
        # 只有一层挡住等于另一层可以绕过去
        rules = ' '.join(self.cfg['permissions']['deny'])
        for p in self.cfg['sandbox']['filesystem']['denyRead']:
            self.assertIn(p, rules, '%s 只在沙箱层拒了，Read 工具仍可读' % p)


class TestEngineExemption(unittest.TestCase):
    def test_codex_keeps_its_own_auth(self):
        # codex 整个进程跑在外层沙箱里，拒掉 ~/.codex 就登录不了
        out = subprocess.run(
            ['/bin/bash', '-c',
             'ROOT=%s; export ROOT HOME; . "$ROOT/bin/sandbox-paths.sh"; sbx_deny_dirs_for codex' % ROOT],
            capture_output=True, text=True, check=True).stdout
        self.assertNotIn('/.codex', out)

    def test_claude_layer_still_denies_codex_dir(self):
        # 反过来，claude 那层没有这个限制，照拒不误
        deny = ' '.join(render('claude')['sandbox']['filesystem']['denyRead'])
        self.assertIn('/.codex', deny)


if __name__ == '__main__':
    unittest.main()
