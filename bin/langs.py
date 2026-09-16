#!/usr/bin/env python3
"""算出实际会发布哪些语言。

一个语言"配了"不等于"能用"：

  en     需要 FALLBACK_BASE_URL / FALLBACK_AUTH_TOKEN / FALLBACK_MODEL 三个都齐，
         否则 translate.py 根本翻不出东西，构建出来的是一个**所有报告都缺失的
         空英文站**——然后照样发布出去。这比没有英文站糟糕得多。
  zh-TW  需要 opencc。缺了的话繁中版会跟简中版逐字节相同，挂在 /zh-TW/ 下面
         是在骗人。

所以两个条件都满足才算开启：**列进 SITE_LANGS + 依赖齐备**。缺依赖就剔除并说明
原因，其余语言照常发布——不因为英文没配好就把整条流水线停掉。

    langs.py            打印生效的语言，逗号分隔（供 run_task.sh 取用）
    langs.py --explain  连同被剔除的一起打印，给人看

⚠️ 必须用**将要做构建的那个解释器**跑（项目内 .venv）。opencc 装在 venv 里，
拿系统 python3 来问会误判成「没装」，把繁中错误地剔掉。
"""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KNOWN = ('zh-CN', 'zh-TW', 'en')
DEFAULT = 'zh-CN'


def conf(key, default=''):
    try:
        for line in open(os.path.join(ROOT, 'bin', 'config.conf'), encoding='utf-8'):
            line = line.split('#', 1)[0].strip()
            if line.startswith(key + '='):
                return line.split('=', 1)[1].strip().strip('\'"')
    except OSError:
        pass
    return default


def has_opencc():
    try:
        import opencc  # noqa: F401
        return True
    except ImportError:
        return False


def effective(site_langs, get=conf, opencc_ok=None):
    """返回 (生效的语言列表, [(被剔除的语言, 原因)])。

    纯函数（依赖都可注入）是为了能测——这里的判断错了不会报错，
    只会安静地发布一个空站点或一个假的繁体站。
    """
    if opencc_ok is None:
        opencc_ok = has_opencc()
    wanted = [x.strip() for x in (site_langs or '').split(',') if x.strip()]
    if not wanted:
        wanted = [DEFAULT]

    keep, dropped = [], []
    for code in wanted:
        if code not in KNOWN:
            dropped.append((code, '不认识这个语言代码（可选 %s）' % '/'.join(KNOWN)))
            continue
        if code in keep:
            continue
        if code == 'en':
            missing = [k for k in ('FALLBACK_BASE_URL', 'FALLBACK_AUTH_TOKEN', 'FALLBACK_MODEL')
                       if not get(k, '')]
            if missing:
                dropped.append((code, '缺 %s——没有这些就翻不出英文，'
                                      '构建出来会是一个所有报告都缺失的空站'
                                % '、'.join(missing)))
                continue
        if code == 'zh-TW' and not opencc_ok:
            dropped.append((code, '未安装 opencc——繁中版会跟简中版逐字节相同，'
                                  '挂出去是在骗人（装： .venv/bin/pip install '
                                  'opencc-python-reimplemented）'))
            continue
        keep.append(code)

    if not keep:                     # 全被剔掉也要有个默认语言，站点不能不出
        keep = [DEFAULT]
    return keep, dropped


def main():
    keep, dropped = effective(conf('SITE_LANGS'))
    if '--explain' in sys.argv:
        print('生效: %s' % ','.join(keep))
        for code, why in dropped:
            print('剔除: %-6s %s' % (code, why))
    else:
        print(','.join(keep))
    return 0


if __name__ == '__main__':
    sys.exit(main())
