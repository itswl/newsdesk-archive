#!/usr/bin/env python3
"""把 reports/<日期>/ 下的四份 Markdown 合成静态站。

用法: build_site.py [YYYY-MM-DD]
日期参数只用于提示，每次都会重建全部日期页 —— 因为「上一天/下一天」导航
要嵌进每个页面，只重建今天会让昨天的「下一天」永远指不到今天。
页数不多（一天一页），全量重建的开销可以忽略。

产物:
  site/<YYYY-MM-DD>.html   每日页，四 Tab + 日期导航
  site/index.html          最新一天的副本
  site/archive.html        全部日期总览，标出每天有哪几份报告
"""
import sys, os, glob, re, json, time, datetime, argparse, html as H
import markdown
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render import sanitize, cdata   # 渲染后清洗与 CDATA，见 bin/render.py
from render import today_state as _today_state
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, 'reports')

_ap = argparse.ArgumentParser(description='把 reports/ 渲染成静态站')
_ap.add_argument('--days', type=int, default=0,
                 help='只生成最近 N 天（0 = 全部）。对外发布用它限制可见范围')
_ap.add_argument('--out', default='site', help='输出目录，相对仓库根（默认 site）')
_args = _ap.parse_args()
SITE = os.path.join(ROOT, _args.out)
os.makedirs(SITE, exist_ok=True)

PANELS = [
    ('ai',       'AI 简报',         ['ai-news.md']),
    ('trending', 'GitHub Trending', ['github-trending.md', 'github-trending_draft*.md']),
    ('momoyu',   '摸摸鱼热榜',       ['momoyu.md']),
    ('douban',   '豆瓣电影',         ['douban.md']),
]
# 归档页是扫读视图，用短标签——全名会在窄屏折成两行，几十天下来页面长一倍
SHORT = {'ai': 'AI', 'trending': 'Trending', 'momoyu': '摸摸鱼', 'douban': '豆瓣'}

CSS = """
/* 配色抽成变量，两套主题只差这一块。避免维护两份完整样式表然后慢慢漂移。
   对比度有意压低：深色 #c9ced8/#16181d 约 9.5:1、浅色约 10:1，都远高于
   WCAG AA 的 4.5:1，但比纯白纯黑柔和——这些报告动辄几千字，要能久看。 */
:root{
  --bg:#16181d; --bg2:#1b1e25; --bg3:#232730; --bg4:#20242c; --bg5:#1a1d24;
  --fg:#c9ced8; --fg2:#8f96a3; --fg3:#666d79; --fgh:#eef1f6; --fgh2:#aeb6c2;
  --bd:#272b34; --bd2:#2f343e; --bd3:#262a33;
  --link:#6ba4ff; --accent:#3b7bf0; --code:#e0a373;
  --stripe:#1a1d23; --quote-bd:#3c4250;
  --ok-fg:#74c894; --ok-bd:#2b4b36;
  --bad-fg:#f09b9b; --bad-bd:#6e3131; --bad-bg:#2a1a1a;
  --warn-fg:#d4bd7e; --warn-bd:#5f5329; --warn-bg:#2e2a1b;
  --chip-fg:#93b8ef; --chip-bg:#1d2a41; --chip-bd:#2b4166;
  --alert-bg:#2a1a1a; --alert-bd:#6e3131;
  --fade:22,24,29;
  color-scheme:dark;
}
html[data-theme=light]{
  --bg:#fcfcfb; --bg2:#f5f5f3; --bg3:#fff; --bg4:#f1f1ee; --bg5:#f7f7f5;
  --fg:#3a3f47; --fg2:#6b7280; --fg3:#949aa3; --fgh:#1d2126; --fgh2:#454b54;
  --bd:#e8e7e3; --bd2:#dcdbd6; --bd3:#e4e3df;
  --link:#2563c7; --accent:#3b7bf0; --code:#9a5416;
  --stripe:#faf9f7; --quote-bd:#d5d4cf;
  --ok-fg:#1f7a45; --ok-bd:#bcdfc8;
  --bad-fg:#c02626; --bad-bd:#f0c2c2; --bad-bg:#fdefef;
  --warn-fg:#8a6108; --warn-bd:#e8d5a4; --warn-bg:#fdf7e8;
  --chip-fg:#1f5aa8; --chip-bg:#eaf1fc; --chip-bd:#c6d9f5;
  --alert-bg:#fdefef; --alert-bd:#f0c2c2;
  --fade:252,252,251;
  color-scheme:light;
}
@media (prefers-color-scheme:light){
  html:not([data-theme]){
    --bg:#fcfcfb; --bg2:#f5f5f3; --bg3:#fff; --bg4:#f1f1ee; --bg5:#f7f7f5;
    --fg:#3a3f47; --fg2:#6b7280; --fg3:#949aa3; --fgh:#1d2126; --fgh2:#454b54;
    --bd:#e8e7e3; --bd2:#dcdbd6; --bd3:#e4e3df;
    --link:#2563c7; --code:#9a5416;
    --stripe:#faf9f7; --quote-bd:#d5d4cf;
    --ok-fg:#1f7a45; --ok-bd:#bcdfc8;
    --bad-fg:#c02626; --bad-bd:#f0c2c2; --bad-bg:#fdefef;
    --warn-fg:#8a6108; --warn-bd:#e8d5a4; --warn-bg:#fdf7e8;
    --chip-fg:#1f5aa8; --chip-bg:#eaf1fc; --chip-bd:#c6d9f5;
    --alert-bg:#fdefef; --alert-bd:#f0c2c2;
    --fade:252,252,251;
    color-scheme:light;
  }
}

*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);overflow-wrap:break-word;
  font:16.5px/1.9 -apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB",
       "Microsoft YaHei","Helvetica Neue",Arial,sans-serif;
  letter-spacing:.01em;-webkit-font-smoothing:antialiased}

/* 表头随内容滚走，只留 Tab 条吸顶——Tab 是唯一高频操作，日期导航和状态条
   看一眼就够，常驻会在手机上白占四分之一屏。 */
header{background:var(--bg2);border-bottom:1px solid var(--bd);padding:16px 24px}
.tabbar{position:sticky;top:0;z-index:9;background:var(--bg2);
  border-bottom:1px solid var(--bd);padding:9px 24px}
.bar{display:flex;align-items:center;gap:14px;flex-wrap:wrap;
  max-width:860px;margin:0 auto 10px}
h1.site{margin:0;font-size:16px;font-weight:600;color:var(--fgh);letter-spacing:.02em}
.nav{display:flex;align-items:center;gap:6px;margin-left:auto}
.nav a,.nav span.dis,.nav button.tg{display:inline-block;padding:6px 12px;border-radius:7px;
  font-size:13px;border:1px solid transparent;background:var(--bg3);color:var(--fg2);
  text-decoration:none;font-family:inherit;cursor:pointer;transition:background .15s}
.nav a:hover,.nav button.tg:hover{background:var(--bg4);color:var(--fgh2);text-decoration:none}
.nav span.dis{opacity:.4}
.nav select{background:var(--bg3);color:var(--fg2);border:1px solid transparent;border-radius:7px;
  padding:6px 10px;font-size:13px;font-family:inherit;min-width:0;max-width:100%}
.nav a.all{color:var(--link)}
.tabs{display:flex;gap:6px;flex-wrap:wrap;max-width:860px;margin:0 auto}
.tab{background:transparent;color:var(--fg2);border:1px solid transparent;border-radius:8px;
  padding:7px 14px;font-size:14.5px;cursor:pointer;font-family:inherit;transition:background .15s}
.tab:hover{background:var(--bg3);color:var(--fgh2)}
.tab.on{background:var(--accent);color:#fff}
.tab em{font-style:normal;font-size:11px;opacity:.85;background:rgba(255,255,255,.2);
  padding:1px 6px;border-radius:5px;margin-left:6px}
.tab.absent{opacity:.3;cursor:not-allowed;text-decoration:line-through}

/* 正文宽度 760px——中文 16.5px 下约 42 字一行。原来 1000px 是 66 字，
   眼睛每行都要横扫一遍，长文读着累。表格允许在此基础上横向滚。 */
main{max-width:760px;margin:0 auto;padding:44px 24px 96px}
.pane{display:none}.pane.on{display:block}
.src{color:var(--fg3);font-size:12px;margin-bottom:34px;font-family:ui-monospace,Menlo,monospace}

h1{font-size:27px;line-height:1.45;margin:0 0 26px;color:var(--fgh);font-weight:650;letter-spacing:0}
h2{font-size:20px;line-height:1.5;margin:52px 0 16px;color:var(--fgh);font-weight:650;letter-spacing:0}
h3{font-size:17px;line-height:1.6;margin:34px 0 10px;color:var(--fgh2);font-weight:600}
h4{font-size:16px;margin:26px 0 8px;color:var(--fg2);font-weight:600}
p{margin:17px 0}
a{color:var(--link);text-decoration:none;border-bottom:1px solid transparent;transition:border-color .15s}
a:hover{border-bottom-color:var(--link);text-decoration:none}

/* 行内代码不再给方框，只用色相区分——满屏小方块是视觉噪声的主要来源 */
code{font-size:.9em;color:var(--code);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  letter-spacing:0;word-break:break-word}
pre{background:var(--bg5);padding:16px 18px;border-radius:10px;overflow-x:auto;
  border:1px solid var(--bd3);font-size:13.5px;line-height:1.7}
pre code{color:var(--fg)}

/* 引用块去掉背景与圆角，只留一道竖线——它在报告里承担「口径说明」，
   不该比正文更抢眼 */
blockquote{margin:24px 0;padding:2px 0 2px 20px;border-left:2px solid var(--quote-bd);
  color:var(--fg2);font-size:15.5px}
blockquote p{margin:9px 0}

.tw{position:relative;margin:28px 0}
.tw::after{content:"";position:absolute;top:0;right:0;width:28px;height:100%;
  background:linear-gradient(90deg,rgba(var(--fade),0),rgba(var(--fade),.95));
  pointer-events:none;opacity:0;transition:opacity .2s}
.tw.more::after{opacity:1}
.tw>div{overflow-x:auto;-webkit-overflow-scrolling:touch}
/* 表格只留横线，去掉竖线和单元格边框——报告表格很密，四边框会让整块变成网格噪声 */
table{border-collapse:collapse;width:100%;font-size:14px;line-height:1.65}
th,td{border:0;border-bottom:1px solid var(--bd3);padding:10px 14px 10px 0;
  text-align:left;vertical-align:top}
th{color:var(--fg2);font-weight:600;white-space:nowrap;font-size:13px;
  letter-spacing:.03em;border-bottom:1px solid var(--bd2)}
tr:last-child td{border-bottom:0}
tbody tr:hover td{background:var(--stripe)}

strong{color:var(--fgh);font-weight:650}
ul,ol{padding-left:22px;margin:17px 0}
li{margin:8px 0}
li::marker{color:var(--fg3)}
hr{border:0;border-top:1px solid var(--bd);margin:44px 0}
em{color:var(--fg2);font-style:normal}

.arch td.d{white-space:nowrap;font-family:ui-monospace,Menlo,monospace}
.arch tr.mh td{background:var(--bg4);color:var(--fgh2);font-weight:600;
  font-family:ui-monospace,Menlo,monospace;padding:8px 12px;position:sticky;top:0}
.arch tr.mh span{font-weight:400;color:var(--fg3);font-size:12px}
.arch .chip{margin:1px 5px 1px 0;white-space:nowrap}
.arch td{padding:8px 10px}
.arch td.d{width:1%}
.arch .new{margin-left:6px;padding:1px 6px}
.chip{display:inline-block;padding:2px 9px;border-radius:6px;font-size:12px;margin-right:5px;
  border:1px solid var(--bd2);background:transparent;color:var(--fg3)}
.chip.has{background:var(--chip-bg);border-color:transparent;color:var(--chip-fg)}
.chip.draft{background:var(--warn-bg);border-color:transparent;color:var(--warn-fg)}
.new{margin-left:9px;font-size:11px;font-weight:600;vertical-align:2px;
  padding:2px 8px;border-radius:6px;background:var(--chip-bg);color:var(--chip-fg)}
.status{display:flex;gap:7px;flex-wrap:wrap;max-width:860px;margin:0 auto}
.status.alert{padding:8px 11px;border-radius:8px;background:var(--alert-bg);
  border:1px solid var(--alert-bd)}
.st{font-size:12px;padding:3px 10px;border-radius:6px;border:1px solid var(--bd2);
  background:transparent;color:var(--fg3)}
.st[title]{cursor:help}
.st.ok{color:var(--ok-fg);border-color:var(--ok-bd)}
.st.bad{color:var(--bad-fg);border-color:var(--bad-bd);background:var(--bad-bg);font-weight:600}
.st.warn{color:var(--warn-fg);border-color:var(--warn-bd)}
.st.pend{opacity:.55}
.keep{margin:0 0 20px;padding:9px 13px;border-left:2px solid var(--quote-bd);
  background:var(--bg5);color:var(--fg2);font-size:13.5px;line-height:1.7;border-radius:0 4px 4px 0}
footer.dis .keep{margin:0 0 12px;background:none;padding:0 0 0 11px}
footer.dis{margin-top:72px;padding-top:22px;border-top:1px solid var(--bd);
  color:var(--fg3);font-size:12.5px;line-height:1.95}
footer.dis a{color:var(--fg2);border-bottom:1px solid var(--bd2)}

@media (max-width:640px){
  body{font-size:16px;line-height:1.85}
  header{padding:12px 16px}
  .tabbar{padding:8px 16px}
  .bar{gap:9px;margin-bottom:9px}
  h1.site{font-size:14.5px;width:100%}
  h1.site span{display:block;margin:2px 0 0;font-size:11px}
  .nav{margin-left:0;width:100%;gap:6px}
  .nav a,.nav span.dis,.nav select,.nav button.tg{flex:1;text-align:center;padding:9px 6px;
    font-size:13px;min-height:38px;white-space:nowrap;display:flex;align-items:center;
    justify-content:center}
  .nav select{flex:1.4 1 0;min-width:0}
  .nav a.all,.nav button.tg{flex:0 0 auto;padding:9px 12px}
  .tabs{gap:4px}
  .tab{flex:1 1 auto;padding:9px 8px;font-size:13.5px;min-height:38px}
  .tab em{display:none}
  main{padding:28px 16px 64px;padding-bottom:calc(64px + env(safe-area-inset-bottom))}
  .src{font-size:11px;margin-bottom:24px}
  h1{font-size:22px;margin-bottom:20px}
  h2{font-size:18px;margin:38px 0 13px}
  h3{font-size:16px;margin:26px 0 9px}
  p{margin:15px 0}
  table{font-size:13px}
  th,td{padding:8px 10px 8px 0}
  blockquote{margin:20px 0;padding-left:15px;font-size:15px}
  pre{padding:12px;font-size:12.5px}
  ul,ol{padding-left:19px}
  .status{gap:5px}
  .st{font-size:11px;padding:2px 8px}
  footer.dis{margin-top:52px;font-size:12px}
}
@media (max-width:380px){
  .tab{flex:1 1 calc(50% - 2px);font-size:13px;padding:9px 5px}
  .nav a,.nav span.dis{padding:9px 4px;font-size:12px}
  .nav a.all,.nav button.tg{padding:9px 9px}
  table{font-size:12px}
  th,td{padding:7px 8px 7px 0}
  main{padding:22px 14px 56px}
}
"""

JS = """
document.querySelectorAll('.tab:not(.absent)').forEach(function(b){
  b.onclick=function(){
    document.querySelectorAll('.tab').forEach(function(x){x.classList.remove('on')});
    document.querySelectorAll('.pane').forEach(function(x){x.classList.remove('on')});
    b.classList.add('on');
    document.getElementById('p-'+b.dataset.t).classList.add('on');
    history.replaceState(null,'','#'+b.dataset.t);
    window.scrollTo(0,0);
  };
});
(function(){
  var h=location.hash.slice(1);
  if(h){var t=document.querySelector('.tab[data-t="'+h+'"]:not(.absent)'); if(t)t.click();}
})();
var sel=document.getElementById('daysel');
if(sel) sel.onchange=function(){
  if(!this.value) return;                          // 停在占位项时不跳
  if(this.value==='@archive'){location.href='archive.html';return;}
  location.href=this.value+'.html'+location.hash;
};

function go(dir){
  var el=document.getElementById(dir>0?'nextday':'prevday');
  if(el&&el.href) location.href=el.href;
}
// 桌面：左右方向键换天
document.onkeydown=function(e){
  if(e.target.tagName==='SELECT'||e.metaKey||e.ctrlKey) return;
  if(e.key==='ArrowLeft') go(-1);
  if(e.key==='ArrowRight') go(1);
};
// 手机：横向滑动换天。起点在可横向滚动的表格里时不接管，否则会和看表冲突。
(function(){
  var x0=null,y0=null,lock=false;
  addEventListener('touchstart',function(e){
    var t=e.touches[0]; x0=t.clientX; y0=t.clientY;
    lock=!!e.target.closest('.tw');
  },{passive:true});
  addEventListener('touchend',function(e){
    if(x0===null||lock) return;
    var t=e.changedTouches[0], dx=t.clientX-x0, dy=t.clientY-y0;
    if(Math.abs(dx)>70 && Math.abs(dx)>Math.abs(dy)*2) go(dx<0?1:-1);
    x0=null;
  },{passive:true});
})();
// 深浅色切换：未手动选过就跟随系统，选过之后记住
(function(){
  var tg=document.getElementById('theme');
  function sysLight(){return matchMedia('(prefers-color-scheme:light)').matches}
  function now(){return document.documentElement.dataset.theme || (sysLight()?'light':'dark')}
  function paint(){if(tg) tg.textContent = now()==='light' ? '☾' : '☀'}
  paint();
  if(tg) tg.onclick=function(){
    var next = now()==='light' ? 'dark' : 'light';
    document.documentElement.dataset.theme = next;
    try{localStorage.setItem('theme',next)}catch(e){}
    paint();
  };
  // 没手动选过时，系统切换要跟着变
  matchMedia('(prefers-color-scheme:light)').addEventListener('change',function(){
    if(!document.documentElement.dataset.theme) paint();
  });
})();
// 表格还能继续横向滚时才显示右缘渐变
(function(){
  function sync(w){var d=w.firstElementChild;
    w.classList.toggle('more', d.scrollWidth-d.clientWidth-d.scrollLeft>4);}
  document.querySelectorAll('.tw').forEach(function(w){
    sync(w); w.firstElementChild.addEventListener('scroll',function(){sync(w)},{passive:true});
  });
  addEventListener('resize',function(){document.querySelectorAll('.tw').forEach(sync)});
})();
"""

def _schedule():
    """从 scheduler.py 读时间表，供状态条的提示文案用——写死会跟真实调度漂移。"""
    out = {}
    try:
        src = open(os.path.join(ROOT, 'bin', 'scheduler.py')).read()
        blk = src.split('SCHEDULE = [', 1)[1].split(']', 1)[0]
        for t, k in re.findall(r"\('(\d{1,2}:\d{2})',\s*'(\w+)'\)", blk):
            out[k] = t
    except (OSError, IndexError):
        pass
    return out


def today_state():
    return _today_state(REPORTS, os.path.join(ROOT, 'state', 'scheduler.json'),
                        PANELS, _schedule(), find=find)


def task_status():
    """把 today_state() 渲染成状态条。无人值守的东西必须把失败摆在看得见的地方——
    只写进日志等于没有告警。每个 chip 带 title 说明，光靠颜色说不清。"""
    state = today_state()
    mark = {'ok': ('ok', '✓'), 'draft': ('warn', '✎'), 'failed': ('bad', '✘'),
            'retrying': ('warn', '…'), 'skipped': ('warn', '⊘'), 'pending': ('pend', '·')}
    chips, bad = [], False
    for key, label in (('ai', 'AI'), ('douban', '豆瓣'), ('trending', 'Trending'), ('momoyu', '摸摸鱼')):
        status, tip, _ = state[key]
        cls, ico = mark.get(status, ('pend', '·'))
        if status in ('failed', 'skipped'):
            bad = True
        chips.append('<span class="st %s" title="%s">%s %s</span>'
                     % (cls, H.escape(tip), ico, label))
    return '<div class="status%s">%s</div>' % (' alert' if bad else '', ''.join(chips))


def find(rdir, pats):
    hits = [f for pat in pats for f in glob.glob(os.path.join(rdir, pat))]
    return max(hits, key=os.path.getmtime) if hits else None

# ---------- 收集所有有报告的日期 ----------
days = sorted(d for d in os.listdir(REPORTS) if re.fullmatch(r'\d{4}-\d{2}-\d{2}', d)) \
       if os.path.isdir(REPORTS) else []
if not days:
    sys.exit('reports/ 下没有找到任何 YYYY-MM-DD 目录')

# 截断放在这里、只此一处：下面的日期下拉、上一天/下一天、归档页、feed、index
# 全都由 days 推导，砍掉尾巴之后它们会自动一致。
# 对外发布用 --days 限制可见范围——注意光少生成页面还不够，公开桶里已经传上去的
# 旧页面仍然按 URL 可取（桶是 ObjectReadWithoutList，列不出但地址能猜），
# 所以 backup_oci.sh 还要把超窗的对象删掉，见那里的 prune_public。
_all_days = list(days)
if _args.days > 0:
    days = days[-_args.days:]

# 限窗之后有些措辞会变成假话：归档页的「共 N 天」读起来像「总共就这些」，
# 日期页最早那天的「‹ 最早」读起来像「没有更早的了」。实际是更早的被下线了，
# 本地和私有桶里都还在。所以限窗时改口径，并明说保留多少天。
WINDOWED = _args.days > 0 and len(_all_days) > len(days)
WINDOW_NOTE = ('本站对外仅保留最近 %d 天的简报，更早的已下线。' % len(days)) if WINDOWED else ''

RECENT = 14     # 下拉里直接列出的天数，其余走归档页

def _conf(key, default=''):
    """读 bin/config.conf 的一项（与 run_task.sh / scheduler.py 共用同一份）。"""
    try:
        for line in open(os.path.join(ROOT, 'bin', 'config.conf')):
            line = line.split('#', 1)[0].strip()
            if line.startswith(key + '='):
                return line.split('=', 1)[1].strip().strip('\'"')
    except OSError:
        pass
    return default

CONTACT  = _conf('CONTACT')     # 留空则只显示声明、不显示联系方式
SITE_URL = _conf('SITE_URL').rstrip('/')   # 对外地址；留空则不生成 feed（feed 里的链接必须是绝对地址）
# feed 的覆盖范围必须跟站点一致，否则「对外只保留 N 天」这句话在 feed 上不成立。
# 以前是写死 20：满负荷时 7 天 × 4 篇 = 28 条 > 20，feed 会悄悄收窄到 5 天，
# 而站点还是 7 天——两个互不相干的常数撞出来的结果，不是谁决定的。
#
# 限窗时按「窗口天数 × 每天篇数」算，范围由 PUBLIC_DAYS 一个旋钮决定。
# 不限窗时保留原来的 20：那种情况下 days 可能攒到上百天，全放进去 feed 会几十 MB。
FEED_MAX = _args.days * len(PANELS) if _args.days > 0 else 20
# 声明同时出现在每日页、归档页和 Atom feed 的页脚。
# 站点分发的内容里含第三方文本（豆瓣简介、GitHub 仓库描述、各平台热榜标题），
# 这不只是隐私问题，也是版权与各平台 ToS 的问题：自己看是一回事，
# 公开站点 + feed 分发是另一回事。内容维持现状，靠声明把边界说清楚。
# 页脚提示（日期页用）。不写「完整历史在私有备份里」——那是运维侧的事，
# 访客既拿不到也不关心，只会让人以为还有别的入口。
NOTE_HTML = ('<p class="keep">%s</p>' % WINDOW_NOTE) if WINDOWED else ''
DISCLAIMER = (
    '本站为个人非商业性质的信息聚合与评述项目，内容由程序自动采集公开可访问的信息源，'
    '交由 AI 自主分析生成，仅供个人学习与研究使用，不代表任何机构立场，亦不用于任何商业目的。'
    '文中引用的标题、摘要、简介、仓库描述等材料，著作权均归原作者或原平台所有，'
    '本站仅作必要引用以支撑评述，并在各处标注来源、保留原文链接，'
    '不主张对这些材料的任何权利，也不鼓励脱离原始出处传播。'
    '分析与评述部分为 AI 自主分析，可能存在错误、遗漏或偏差，一切以原始来源为准。'
    + ('如您是权利人并认为本站内容侵犯了您的权益，或希望移除对某一来源的引用，'
       '请联系 <a href="mailto:%s">%s</a>，核实后立即删除，无需其他前置程序。'
       % (CONTACT, CONTACT) if CONTACT else
       '如您是权利人并认为本站内容侵犯了您的权益，请与本站联系，核实后立即删除。')
)

HEAD_JS = ('<script>try{var t=localStorage.getItem("theme");'
           'if(t)document.documentElement.dataset.theme=t}catch(e){}</script>')
FEED_LINK = ('<link rel="alternate" type="application/atom+xml" title="每日简报" href="feed.xml">'
             if SITE_URL else '')
md = markdown.Markdown(extensions=['tables', 'fenced_code', 'attr_list'])

STATUS = task_status()
built_at = datetime.datetime.now().strftime('%m-%d %H:%M')
index_of = {d: i for i, d in enumerate(days)}
summary = {}

def nav_html(day):
    i = index_of[day]
    prev = ('<a id="prevday" href="%s.html">‹ %s</a>' % (days[i-1], days[i-1][5:])) if i > 0 \
           else '<span class="dis" title="%s">%s</span>' % (
               H.escape(WINDOW_NOTE or '这是最早的一天'),
               '‹ 仅存 %d 天' % len(days) if WINDOWED else '‹ 最早')
    nxt = ('<a id="nextday" href="%s.html">%s ›</a>' % (days[i+1], days[i+1][5:])) if i < len(days)-1 \
          else '<span class="dis">最新 ›</span>'
    # 当前日期已经在标题里了，下拉只作跳转用，默认不落在任何一天上——
    # 预选某天会让人以为「我选了这天」，而不是「这是最新的一天」。
    #
    # 只列近 RECENT 天：攒上几个月后扁平列表会有上百项，桌面端拉成一长条、
    # iOS 上是个滚不完的滚轮。长尾交给归档页，那本来就是为列日期设计的。
    recent = list(reversed(days))[:RECENT]
    if day not in recent:                 # 当前在更早的日子，把它并进来免得下拉里没有
        recent.append(day)
    opts = '<option value="" selected>跳到日期…</option>' + ''.join(
        '<option value="%s">%s%s</option>' % (d, d, '（最新）' if d == days[-1] else '')
        for d in recent)
    if len(days) > len(recent):
        opts += '<option value="@archive">更多历史（共 %d 天）…</option>' % len(days)
    return ('<div class="nav">%s<select id="daysel">%s</select>%s'
            '<a class="all" href="archive.html">全部 %d 天</a>'
            '<button class="tg" id="theme" title="切换深浅色">☀</button></div>'
            % (prev, opts, nxt, len(days)))

# ---------- 逐日渲染 ----------
feed_pool = []          # (日期, 面板键, 标题, 生成时刻, 正文HTML)

for day in days:
    rdir = os.path.join(REPORTS, day)
    tabs, panes, have, entries = [], [], [], []
    for key, label, pats in PANELS:
        f = find(rdir, pats)
        if not f:
            tabs.append('<button class="tab absent" data-t="%s" '
                        'title="当天没有这份报告——任务没跑，或者跑了但失败了">%s</button>'
                        % (key, label))
            continue
        draft = '_draft' in os.path.basename(f)
        have.append((key, label, 'draft' if draft else 'has'))
        md.reset()
        body = sanitize(md.convert(open(f, encoding='utf-8').read()))
        # 宽表在窄屏只能横向滚，包一层容器才能加滚动提示
        body = body.replace('<table>', '<div class="tw"><div><table>').replace('</table>', '</table></div></div>')
        tabs.append('<button class="tab%s" data-t="%s"%s>%s%s</button>' % (
            ' on' if not panes else '', key,
            ' title="这是草稿版本：当天的正式版还没生成，正式版出来后会取代它"' if draft else '',
            label, ' <em title="草稿版本，非最终稿">草稿</em>' if draft else ''))
        panes.append('<section class="pane%s" id="p-%s"><div class="src">%s</div>%s</section>' % (
            '' if panes else ' on', key, H.escape(os.path.relpath(f, ROOT)), body))
        entries.append((day, key, label, os.path.getmtime(f), body))
    summary[day] = have
    feed_pool.extend(entries)
    doc = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
           '<meta name="viewport" content="width=device-width,initial-scale=1">'
           '<title>每日简报 %s</title>%s%s<style>%s</style></head><body>'
           '<header><div class="bar"><h1 class="site">每日简报 · %s%s</h1>%s</div>%s</header>'
           '<nav class="tabbar"><div class="tabs">%s</div></nav><main>%s'
           '<footer class="dis">%s%s</footer></main><script>%s</script></body></html>'
           % (day, HEAD_JS, FEED_LINK, CSS, day,
              '<b class="new">最新</b>' if day == days[-1] else '',
              nav_html(day),
              STATUS if day == days[-1] else '',
              ''.join(tabs),
              ''.join(panes) or '<p>当天没有任何报告。</p>', NOTE_HTML, DISCLAIMER, JS))
    open(os.path.join(SITE, day + '.html'), 'w', encoding='utf-8').write(doc)

# ---------- index = 最新一天 ----------
newest = days[-1]
open(os.path.join(SITE, 'index.html'), 'w', encoding='utf-8').write(
    open(os.path.join(SITE, newest + '.html'), encoding='utf-8').read())

# ---------- 归档总览 ----------
rows, cur_month = [], None
for d in reversed(days):
    m = d[:7]
    if m != cur_month:                    # 按月分段，天数一多才扫得动
        cur_month = m
        n = sum(1 for x in days if x[:7] == m)
        rows.append('<tr class="mh"><td colspan="2">%s　<span>%d 天</span></td></tr>' % (m, n))
    chips = ''
    for key, label, _ in PANELS:
        st = dict((k, s) for k, _l, s in summary[d]).get(key)
        cls = 'chip has' if st == 'has' else ('chip draft' if st == 'draft' else 'chip')
        chips += '<span class="%s">%s%s</span>' % (cls, SHORT[key], '·草稿' if st == 'draft' else '')
    # 月份已在分组表头里，行内只显示月-日，省下的宽度让报告 chip 收进一行
    rows.append('<tr><td class="d"><a href="%s.html">%s</a>%s</td><td>%s</td></tr>'
                % (d, d[5:], '<b class="new">最新</b>' if d == days[-1] else '', chips))
# ── 给外部监控用的机器可读状态 ────────────────────────────────────────
# 本机的调度器报不了自己的死：机器关了、进程被杀，它没机会发任何告警。
# 把状态写成一个随站点一起发布的小文件，外部监控轮询它、看 generated 是否
# 变陈旧，才能覆盖「整机不在」这种本地告警永远发现不了的情况。
# 用 uptime-kuma 的话：HTTP(s) - Json Query，Query `$.stale_hours`，
# 期望值小于 6；或直接监控 `$.ok` 是否为 true。
def write_status():
    try:
        health = json.load(open(os.path.join(ROOT, 'state', 'health.json')))
    except (OSError, ValueError):
        health = {}
    today = datetime.date.today().isoformat()
    # 和状态条同一套判定：以报告是否产出为准，避免建站早于状态落盘导致的假阴性
    tasks = {k: {'status': v[0], 'finished': v[2] or None}
             for k, v in today_state().items()}
    now = datetime.datetime.now().astimezone()
    hb = health.get('heartbeat')
    # 优先用 epoch 算，不受时区与新旧格式影响；老的心跳文件没有这个字段，
    # 退回解析 ISO，并给可能不带偏移量的旧值补上本地时区
    stale = None
    hb_epoch = health.get('heartbeat_epoch')
    if not hb_epoch and hb:
        try:
            t = datetime.datetime.fromisoformat(hb)
            hb_epoch = (t if t.tzinfo else t.astimezone()).timestamp()
        except ValueError:
            hb_epoch = None
    if hb_epoch:
        stale = round((now.timestamp() - float(hb_epoch)) / 3600, 2)
    doc = {'generated': now.isoformat(timespec='seconds'),
           # 带时区的 ISO 给人看，epoch 给机器用。消费者是跑在 UTC 里的
           # Cloudflare Worker：不带偏移量的 ISO 会被 JS 当成它自己的本地时间
           # （即 UTC），北京时间的 17:47 被读成 UTC 17:47，算出来差整整 8 小时，
           # 结果是机器关了 26 小时它只看到 18 小时，监控永远晚一个时区才响。
           'generated_epoch': int(now.timestamp()),
           'date': today,
           'heartbeat': hb,                 # 调度器最近一次转动的时刻
           'stale_hours': stale,            # 距今多久。变大 = 调度器或整机不在了
           'tasks': tasks,
           'ok': bool(hb) and stale is not None and stale < 1
                 and all(t['status'] in ('ok', 'draft', 'pending') for t in tasks.values())}
    # 注意：这个文件会随 site/ 发布到公开桶，不要往里放路径、pid 之类的本机信息
    with open(os.path.join(SITE, 'status.json'), 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print('  %s/status.json  (stale %s h, ok=%s)'
          % (os.path.relpath(SITE, ROOT), stale, doc['ok']))

write_status()

open(os.path.join(SITE, 'archive.html'), 'w', encoding='utf-8').write(
    '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    '<title>历史简报</title>%s%s<style>%s</style></head><body>'
    '<header><div class="bar"><h1 class="site">历史简报 · %s %d 天</h1>'
    '<div class="nav"><a href="index.html">回到最新 (%s) ›</a>'
    '<button class="tg" id="theme" title="切换深浅色">☀</button></div></div></header>'
    '<main>%s<table class="arch"><tr><th>日期</th><th>报告</th></tr>%s</table>'
    '<footer class="dis">%s%s</footer></main><script>%s</script></body></html>'
    % (HEAD_JS, FEED_LINK, CSS,
       # 「共 N 天」在限窗时是假话——更早的只是下线了，不是不存在
       '最近' if WINDOWED else '共', len(days), newest,
       # 归档页是「历史都在哪」的页面，这条提示放在表格正上方最该被看到
       ('<p class="keep">%s</p>' % WINDOW_NOTE) if WINDOWED else '',
       # 页脚不再重复：这一页表格正上方已经有一条了
       ''.join(rows), '', DISCLAIMER, JS))

# ---------- Atom feed ----------
# 一篇报告一条，比整天打包一条更实用——订阅者可能只关心其中一路。
# 没配 SITE_URL 就不生成：feed 里的链接必须是绝对地址，拼不出来的 feed 是坏的。
if SITE_URL:
    def t(x):
        return datetime.datetime.fromtimestamp(x, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    items = sorted(feed_pool, key=lambda e: e[3], reverse=True)[:FEED_MAX]
    parts = ['<?xml version="1.0" encoding="utf-8"?>',
             '<feed xmlns="http://www.w3.org/2005/Atom">',
             '<title>每日简报</title>',
             '<subtitle>AI 新闻 · GitHub Trending · 摸摸鱼热榜 · 豆瓣电影</subtitle>',
             '<id>%s/</id>' % SITE_URL,
             '<link rel="alternate" type="text/html" href="%s/"/>' % SITE_URL,
             '<link rel="self" type="application/atom+xml" href="%s/feed.xml"/>' % SITE_URL,
             '<updated>%s</updated>' % t(items[0][3] if items else time.time()),
             '<generator uri="%s">newsdesk</generator>' % SITE_URL]
    if CONTACT:
        parts.append('<author><name>newsdesk</name><email>%s</email></author>' % H.escape(CONTACT))
    for day, key, label, mtime, body in items:
        url = '%s/%s.html#%s' % (SITE_URL, day, key)
        parts += ['<entry>',
                  '<title>%s · %s</title>' % (H.escape(label), day),
                  '<id>%s</id>' % url,              # 稳定不变，否则阅读器会重复推送
                  '<link rel="alternate" type="text/html" href="%s"/>' % url,
                  '<updated>%s</updated>' % t(mtime),
                  '<published>%s</published>' % t(mtime),
                  '<category term="%s"/>' % key,
                  # CDATA 的细节（为什么不转义、]]> 怎么拆）见 bin/render.py:cdata
                  '<content type="html">%s</content>' % cdata(body),
                  '</entry>']
    parts.append('</feed>')
    open(os.path.join(SITE, 'feed.xml'), 'w', encoding='utf-8').write('\n'.join(parts))
    covered = len({e[0] for e in items})
    print('  %s/feed.xml  (%d 条 · 覆盖 %d 天 / 站点 %d 天%s，%.0f KB)'
          % (os.path.relpath(SITE, ROOT), len(items), covered, len(days),
             '' if covered == len(days) else ' ← 被 FEED_MAX 截短了',
             os.path.getsize(os.path.join(SITE, 'feed.xml')) / 1024))
else:
    print('  未配 SITE_URL，跳过 feed')

print('已重建 %d 天：%s … %s%s' % (
    len(days), days[0], newest,
    '（共 %d 天，按 --days %d 截断）' % (len(_all_days), _args.days)
    if _args.days > 0 and len(_all_days) > len(days) else ''))
print('  输出目录: %s' % os.path.relpath(SITE, ROOT))
print('  %s/index.html  -> %s' % (os.path.relpath(SITE, ROOT), newest))
print('  %s/archive.html' % os.path.relpath(SITE, ROOT))
