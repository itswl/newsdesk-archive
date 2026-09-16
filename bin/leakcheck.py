#!/usr/bin/env python3
"""发布前扫一遍产出里有没有混进凭据。

为什么需要这一道：分析层是唯一接触外部不可信文本的环节（RSS 正文、仓库描述、
热榜标题），而它的产出会**公开发布**。沙箱禁网，令牌拿不出去，但「写进报告」
这条路径禁网拦不住——报告第二天就在公开站点上了。

ENGINE=api 时令牌通过环境变量传给 claude，模型在沙箱里是否看得到这个变量，
非交互下探不出来（带变量展开的探针会被权限系统拒掉）。所以按最坏情况设计：
假设看得到，在发布前拦一道。这一层不依赖任何引擎内部行为，确定有效。

    leakcheck.py <目录>...      发现即以 2 退出，调用方负责中止发布

不打印命中的内容，只报文件、行号和是哪个配置项——把凭据打进日志就自相矛盾了。
"""
import os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 从 config.conf 取值的这些键，其值一旦出现在产出里就是泄漏
SECRET_KEYS = ('FALLBACK_AUTH_TOKEN', 'ALERT_WEBHOOK', 'GITHUB_TOKEN')

# 不依赖配置、按形状认的常见凭据。配置里没写全、或模型从别处抄来的也能拦下。
SHAPES = [
    ('Anthropic key',    re.compile(r'sk-ant-[A-Za-z0-9_\-]{20,}')),
    ('OpenAI 风格 key',  re.compile(r'\bsk-[A-Za-z0-9]{32,}\b')),
    ('GitHub token',     re.compile(r'\bgh[pousr]_[A-Za-z0-9]{20,}\b')),
    ('Cloudflare token', re.compile(r'\bcf(?:ut|at|k)_[A-Za-z0-9_\-]{20,}\b')),
    ('AWS key id',       re.compile(r'\bAKIA[0-9A-Z]{16}\b')),
    ('Slack/飞书 webhook',
     re.compile(r'https://(?:hooks\.slack\.com|open\.(?:feishu|larksuite)\.com)/\S{20,}')),
    ('私钥块',           re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')),
]


def conf_secrets():
    """config.conf 里那几个键的真实值。太短的不要——空值或 none 会命中一切。"""
    out = {}
    try:
        with open(os.path.join(ROOT, 'bin', 'config.conf'), encoding='utf-8') as f:
            for line in f:
                line = line.split('#', 1)[0].strip()
                for k in SECRET_KEYS:
                    if line.startswith(k + '='):
                        v = line.split('=', 1)[1].strip().strip('\'"')
                        if len(v) >= 12:
                            out[k] = v
    except OSError:
        pass
    return out


def scan(paths):
    secrets = conf_secrets()
    hits = []
    for base in paths:
        for dirpath, _dirs, files in os.walk(base):
            for name in files:
                if not name.endswith(('.md', '.html', '.xml', '.json', '.txt')):
                    continue
                p = os.path.join(dirpath, name)
                try:
                    with open(p, encoding='utf-8', errors='replace') as f:
                        for i, line in enumerate(f, 1):
                            for k, v in secrets.items():
                                if v in line:
                                    hits.append((p, i, 'config.conf 里的 %s' % k))
                            for label, rx in SHAPES:
                                if rx.search(line):
                                    hits.append((p, i, label))
                except OSError:
                    continue
    return hits


def main():
    paths = sys.argv[1:] or [os.path.join(ROOT, 'reports'), os.path.join(ROOT, 'site')]
    paths = [p for p in paths if os.path.exists(p)]
    hits = scan(paths)
    if not hits:
        print('  ✓ 产出中未发现凭据')
        return 0
    print('!! 产出里混进了凭据，拒绝发布：')
    for p, line, what in hits:
        # 只报位置和类型，不打印命中内容——把凭据写进日志就自相矛盾了
        print('   %s:%d  %s' % (os.path.relpath(p, ROOT), line, what))
    print('!! 先把这些内容从报告里删掉，确认凭据是否需要轮换，再重新发布。')
    return 2


if __name__ == '__main__':
    sys.exit(main())
