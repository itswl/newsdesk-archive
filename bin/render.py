"""报告正文的渲染与清洗。

单独成模块是为了能测：build_site.py 一导入就扫目录、读文件、写站点，
没法在单测里 import。这里只有纯函数。
"""
import re

try:
    import nh3
except ImportError:                                  # fail-closed，与沙箱同一个原则
    raise SystemExit('!! 缺少 nh3（HTML 清洗器），拒绝生成站点。\n'
                     '   报告正文含第三方文本，不清洗直接发布等于存储型 XSS。\n'
                     '   安装： .venv/bin/pip install nh3')

# ── 渲染后清洗 ──────────────────────────────────────────────────────────
# python-markdown 默认原样放行 HTML（3.0 之后 safe_mode 已被移除，没有开关可开）。
# 而报告正文里混着的全是别人写的文本：HN 标题、仓库描述、豆瓣简介、热榜标题。
# 只要模型把一条构造过的标题原样抄进报告，`<img src=x onerror=...>` 就会在公开
# 页面上执行——站点虽然没有登录态，但它和 down./wechat./blog. 同属
# wetalk.eu.org，父域 cookie 会被带到，所以不能按「反正没会话」放过。
# feed.xml 用的是同一个 body，清洗放在这里两边都覆盖到。
ALLOWED_TAGS = {
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'p', 'br', 'hr', 'blockquote',
    'ul', 'ol', 'li',
    'strong', 'em', 'del', 's', 'sup', 'sub',
    'code', 'pre',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
    'a',
}
# 刻意不含 img/svg/iframe/video/object/form/input/style/script：报告从不用它们，
# 少一个标签就少一整类注入面。img 尤其关键——onerror 是最好用的那个。

# tables 扩展会给对齐列生成 style="text-align: center"，551 处，不能一刀切丢掉。
# 但 style 是历史悠久的注入面，所以只认这一种形状，其余一律丢弃。
_ALIGN_RE = re.compile(r'^text-align:\s*(left|right|center)\s*;?$', re.I)

def _attr_filter(tag, attr, value):
    """返回 None 表示丢弃该属性。"""
    if attr == 'style':
        return value if tag in ('td', 'th') and _ALIGN_RE.match(value.strip()) else None
    return value

def sanitize(fragment):
    # nh3 自带 URL scheme 白名单（javascript: 会被整个丢掉）并补 rel=noopener。
    return nh3.clean(fragment, tags=ALLOWED_TAGS, attributes={'a': {'href', 'title'},
                                                              'td': {'style'}, 'th': {'style'}},
                     attribute_filter=_attr_filter, link_rel='noopener noreferrer')


def cdata(text):
    """把正文包进 CDATA，供 Atom 的 <content type="html"> 使用。

    为什么不用 XML 转义：转义会把每个 < 变成 &lt;，正文体积近乎翻倍
    （实测 387 KB → 298 KB 是反过来的收益）。CDATA 内部只有 ]]> 这一个
    序列需要处理——它会提前结束 CDATA 段，把后面的正文当标记解析，
    整个 feed 就此损坏。拆成两段是唯一正确的做法，没有转义写法。
    """
    return '<![CDATA[' + text.replace(']]>', ']]]]><![CDATA[>') + ']]>'


def today_state(reports_dir, sched_path, panels, plan, today=None, find=None):
    """今天各任务的状态。key -> (状态, 完成时刻, 附加信息)

    **只返回数据，不拼提示文字**：提示要按站点语言渲染，在这里拼死中文的话
    英文/繁中版就没法翻。调用方拿 status + meta 自己组句子。

    以「今天这份报告有没有产出」为准，而不是只读 scheduler.json。原因是顺序：
    run_task.sh 在自己末尾重建并发布站点，而调度器要等 run_task.sh 退出之后
    才把状态写成 ok——站点永远早一步生成，刚跑完的那个任务的 chip 一直是旧的，
    要等下一档（5 小时后）重建才会变绿。实测 17:02:16 建站、17:02:48 才写状态。

    顺带也修好了手动跑的情况：`run_task.sh momoyu` 直接跑不经过调度器，
    scheduler.json 里根本没有记录，但报告确实产出了。

    scheduler.json 仍然要读——「失败 / 重试中 / 跳过」这些是文件表达不了的，
    没有产出文件时才轮到它说话。
    """
    import datetime, glob, json, os, time
    if today is None:
        today = datetime.date.today().isoformat()
    if find is None:
        def find(rdir, pats):
            hits = [f for pat in pats for f in glob.glob(os.path.join(rdir, pat))]
            return max(hits, key=os.path.getmtime) if hits else None
    try:
        with open(sched_path, encoding='utf-8') as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        st = {}
    rdir = os.path.join(reports_dir, today)
    out = {}
    for key, _label, pats in panels:
        f = find(rdir, pats) if os.path.isdir(rdir) else None
        r = st.get(key) or {}
        cur = r if r.get('date') == today else {}
        if f:
            at = time.strftime('%H:%M', time.localtime(os.path.getmtime(f)))
            if '_draft' in os.path.basename(f):
                out[key] = ('draft', at, {})
            else:
                out[key] = ('ok', at, {})
            continue
        status = cur.get('status')
        if status == 'failed':
            out[key] = ('failed', '', {'attempts': cur.get('attempts', '?')})
        elif status == 'retrying':
            out[key] = ('retrying', '', {'attempts': cur.get('attempts', '?')})
        elif status == 'skipped':
            out[key] = ('skipped', '', {})
        else:
            out[key] = ('pending', '', {'planned': plan.get(key, '')})
    return out
