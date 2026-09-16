"""站点界面文案的三语表。

只有简中与英文是手写的，**繁中由简中经 OpenCC 推导**——s2twp 做的是台湾用词转换
（软件→軟體、网络→網路、内存→記憶體、缓存→快取），不是简单换字，所以不值得再维护
第三份表。报告正文同理：繁中版在构建时转换，不落盘。

新增文案时只需补 zh-CN 与 en 两项；漏了 en 会在 T() 里退回 zh-CN 并不报错，
所以 tests/test_i18n.py 有一条用例检查两边键集合一致。
"""
try:
    from opencc import OpenCC
    _cc = OpenCC('s2twp')
except Exception:                       # 没装就退化成不转换，站点仍能出，只是繁中=简中
    _cc = None

LANGS = ('zh-CN', 'zh-TW', 'en')
HTML_LANG = {'zh-CN': 'zh-CN', 'zh-TW': 'zh-TW', 'en': 'en'}
LANG_LABEL = {'zh-CN': '简体', 'zh-TW': '繁體', 'en': 'English'}
# 窄屏用的缩写。完整标签三个加起来 142px，在 320px 的屏上把日期下拉挤没了。
# 两份都渲染出来、用 CSS 切换，比按屏宽在服务端猜要可靠（同一份 HTML 两种宽度都对）。
LANG_ABBR = {'zh-CN': '简', 'zh-TW': '繁', 'en': 'EN'}

S = {
    # ── 站点骨架 ──
    'site.name':        ('每日简报',      'Daily Briefing'),
    'site.archive':     ('历史简报',      'Archive'),
    'site.latest':      ('最新',          'Latest'),
    'site.none_today':  ('当天没有任何报告。', 'No reports for this day.'),
    'site.feed_title':  ('每日简报',      'Daily Briefing'),
    'site.feed_sub':    ('AI 新闻 · GitHub Trending · 摸摸鱼热榜 · 豆瓣电影',
                         'AI News · GitHub Trending · Momoyu Hot List · Douban Films'),
    # ── 四个板块 ──
    'panel.ai':         ('AI 简报',       'AI News'),
    'panel.trending':   ('GitHub Trending', 'GitHub Trending'),
    'panel.momoyu':     ('摸摸鱼热榜',    'Momoyu Hot List'),
    'panel.douban':     ('豆瓣电影',      'Douban Films'),
    'short.ai':         ('AI',            'AI'),
    'short.trending':   ('Trending',      'Trending'),
    'short.momoyu':     ('摸摸鱼',        'Momoyu'),
    'short.douban':     ('豆瓣',          'Douban'),
    # ── 导航 ──
    'nav.jump':         ('跳到日期…',     'Jump to date…'),
    'nav.newest_mark':  ('（最新）',      ' (latest)'),
    'nav.more':         ('更多历史（共 %d 天）…', 'More history (%d days)…'),
    'nav.all':          ('全部 %d 天',    'All %d days'),
    'nav.newest_end':   ('最新 ›',        'Latest ›'),
    'nav.oldest_end':   ('‹ 最早',        '‹ Oldest'),
    'nav.window_end':   ('‹ 仅存 %d 天',  '‹ %d days kept'),
    'nav.oldest_tip':   ('这是最早的一天', 'This is the earliest day available.'),
    'nav.back':         ('回到最新 (%s) ›', 'Back to latest (%s) ›'),
    'nav.theme':        ('切换深浅色',    'Toggle light/dark'),
    'nav.lang':         ('切换语言',      'Switch language'),
    # ── 标签页 ──
    'tab.absent':       ('当天没有这份报告——任务没跑，或者跑了但失败了',
                         'No such report for this day — the task did not run, or it failed.'),
    'tab.draft_tip':    ('这是草稿版本：当天的正式版还没生成，正式版出来后会取代它',
                         'Draft: the final version for this day has not been generated yet.'),
    'tab.draft_badge':  ('草稿',          'draft'),
    'tab.draft_mark':   ('·草稿',         '·draft'),
    'tab.draft_title':  ('草稿版本，非最终稿', 'Draft, not the final version'),
    # ── 归档页 ──
    'arch.date':        ('日期',          'Date'),
    'arch.reports':     ('报告',          'Reports'),
    'arch.days':        ('%d 天',         '%d days'),
    'arch.total':       ('共',            'all'),
    'arch.recent':      ('最近',          'last'),
    # ── 保留期提示 ──
    'keep.note':        ('本站对外仅保留最近 %d 天的简报，更早的已下线。',
                         'Only the last %d days are kept online; earlier reports have been taken down.'),
    # ── 状态条 ──
    'st.ok':            ('今天 %s 跑完',  'finished today at %s'),
    'st.draft':         ('今天 %s 出了草稿，正式版还没生成',
                         'draft produced today at %s; final version pending'),
    'st.failed':        ('今天失败了，重试 %s 次后放弃；详见 logs/',
                         'failed today, gave up after %s retries; see logs/'),
    'st.retrying':      ('第 %s 次重试中', 'retrying, attempt %s'),
    'st.skipped':       ('错过补跑截止点，今日跳过', 'missed the catch-up deadline; skipped today'),
    'st.pending':       ('今天还没跑',    'not run yet today'),
    'st.planned':       ('，计划 %s',     ', scheduled %s'),
    # ── 页脚声明 ──
    'dis.body':         ('本站为个人非商业性质的信息聚合与评述项目，内容由程序自动采集公开可访问的信息源，'
                         '交由 AI 自主分析生成，仅供个人学习与研究使用，不代表任何机构立场，亦不用于任何商业目的。'
                         '文中引用的标题、摘要、简介、仓库描述等材料，著作权均归原作者或原平台所有，'
                         '本站仅作必要引用以支撑评述，并在各处标注来源、保留原文链接，'
                         '不主张对这些材料的任何权利，也不鼓励脱离原始出处传播。'
                         '分析与评述部分为 AI 自主分析，可能存在错误、遗漏或偏差，一切以原始来源为准。',
                         'This is a personal, non-commercial aggregation-and-commentary project. '
                         'Content is collected automatically from publicly accessible sources and analysed by AI, '
                         'for personal study and research only; it represents no organisation and is not used commercially. '
                         'Copyright in quoted titles, summaries, synopses and repository descriptions remains with the '
                         'original authors or platforms; this site quotes only what the commentary requires, attributes '
                         'sources throughout, keeps links to the originals, claims no rights over that material, and does '
                         'not encourage circulating it apart from its source. '
                         'The analysis and commentary are produced by AI and may contain errors, omissions or bias — '
                         'always defer to the original source.'),
    'dis.contact':      ('如您是权利人并认为本站内容侵犯了您的权益，或希望移除对某一来源的引用，'
                         '请联系 <a href="mailto:%s">%s</a>，核实后立即删除，无需其他前置程序。',
                         'If you are a rights holder and believe this site infringes your rights, or you would like a '
                         'source removed, contact <a href="mailto:%s">%s</a>; it will be deleted upon verification, '
                         'with no further formalities.'),
    'dis.contact_none': ('如您是权利人并认为本站内容侵犯了您的权益，请与本站联系，核实后立即删除。',
                         'If you are a rights holder and believe this site infringes your rights, please contact us; '
                         'it will be deleted upon verification.'),
}


def table(lang):
    """某个语言的完整文案表。繁中现推，不单独维护。"""
    if lang == 'zh-CN':
        return {k: v[0] for k, v in S.items()}
    if lang == 'en':
        return {k: v[1] for k, v in S.items()}
    if lang == 'zh-TW':
        return {k: (_cc.convert(v[0]) if _cc else v[0]) for k, v in S.items()}
    raise ValueError('未知语言: %s（可选 %s）' % (lang, '/'.join(LANGS)))


def translator(lang):
    """返回 T(key, *args)。缺 key 直接抛错——静默退回会让漏翻的地方一直没人发现。"""
    t = table(lang)

    def T(key, *args):
        s = t[key]
        return s % args if args else s
    return T


def convert_body(text, lang):
    """报告正文的语言转换。

    zh-TW 现转不落盘：OpenCC 转 24 KB 报告只要 0.02 秒，存一份反而多一处会过期的副本。
    en 走不到这里——英文正文是 translate.py 预先翻好的独立文件。
    """
    if lang == 'zh-TW' and _cc:
        return _cc.convert(text)
    return text
