#!/usr/bin/env python3
"""把 bin/agent-settings.template.json 渲染成 state/agent-settings.json。

拒绝路径不再在模板里手写——由 bin/sandbox-paths.sh 从 stdin 传进来，
和 codex 的 seatbelt 用的是同一份清单。以前两边各写各的，装了新工具
只改一头，另一头就默默落后（实测漏掉过 ~/.claude.json 与 ~/.pi）。

用法（由 run_task.sh 调用）：
    printf '%s\n' "${dirs[@]}" | render_settings.py <模板> <输出> <HOME> <ROOT>
目录与文件都按路径传，是不是目录这里自己判断。
"""
import json, sys, os


def main():
    tpl, out, home, root = sys.argv[1:5]
    paths = [ln.strip() for ln in sys.stdin if ln.strip()]

    raw = open(tpl, encoding='utf-8').read()
    cfg = json.loads(raw.replace('{{HOME}}', home).replace('{{ROOT}}', root))

    # sandbox.filesystem.denyRead 收路径本身；permissions.deny 收 Read() 规则。
    # 目录要带 /** 才能覆盖到里面的文件，普通文件不能带。
    deny_read, read_rules = [], []
    for p in paths:
        deny_read.append(p)
        read_rules.append('Read(%s/**)' % p if os.path.isdir(p) else 'Read(%s)' % p)

    fs = cfg.setdefault('sandbox', {}).setdefault('filesystem', {})
    # 模板里保留的是「引擎自身相关、不能由共享清单决定」的条目（如钥匙串），
    # 生成的部分追加在后面，去重但保持顺序稳定，便于 diff。
    fs['denyRead'] = _dedup(fs.get('denyRead', []) + deny_read)
    perms = cfg.setdefault('permissions', {})
    perms['deny'] = _dedup(read_rules + perms.get('deny', []))

    fs['allowWrite'] = [root]
    fs['denyWrite'] = _dedup(fs.get('denyWrite', []))

    cfg.pop('_comment', None)
    cfg['_generated'] = ('由 bin/render_settings.py 从 agent-settings.template.json '
                         '+ sandbox-paths.sh 生成，勿手改')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print('  权限文件已渲染：%d 条拒读路径' % len(fs['denyRead']))


def _dedup(seq):
    seen, res = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x); res.append(x)
    return res


if __name__ == '__main__':
    main()
