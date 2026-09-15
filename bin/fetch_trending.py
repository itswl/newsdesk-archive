#!/usr/bin/env python3
"""GitHub Trending 采集。

用法:
  fetch_trending.py              抓页面 + 全量 API 快照，自动选基线，写 snap_t3
  fetch_trending.py --tier t1    同上但写 snap_t1（早间加抓用）
  fetch_trending.py --date 2026-09-15   指定日期（补跑用）

产物: data/trending/<YYYY-MM-DD>/{page.json, page_raw_<tier>.html, snap_<tier>.json}
"""
import sys, os, re, json, html, argparse, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *

ap = argparse.ArgumentParser()
ap.add_argument('--tier', default='t3', choices=['t1', 't2', 't3'])
ap.add_argument('--date', default=None)
a = ap.parse_args()
D = datetime.date.fromisoformat(a.date) if a.date else today()
DD = data_dir('trending', D, create=True)

# ---------- 1. 抓页面并解析 ----------
src = curl('https://github.com/trending?since=daily')
if not src or len(src) < 50000:
    sys.exit('页面抓取失败或内容过短 (%s bytes)' % (len(src) if src else 0))
open(os.path.join(DD, 'page_raw_%s.html' % a.tier), 'w', encoding='utf-8').write(src)

rows = re.findall(r'<article class="Box-row">(.*?)</article>', src, re.S)
board = []
for i, r in enumerate(rows, 1):
    m = re.search(r'<h2[^>]*>\s*<a\b[^>]*?href="/([^"]+?)"', r, re.S)
    if not m:
        continue
    d = re.search(r'<p class="col-9[^"]*">(.*?)</p>', r, re.S)
    desc = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', d.group(1))).strip()) if d else ''
    l = re.search(r'itemprop="programmingLanguage"[^>]*>\s*([^<]+?)\s*<', r, re.S)
    def num(pat):
        x = re.search(pat, r, re.S)
        return int(x.group(1).replace(',', '')) if x else 0
    board.append(dict(rank=i, repo=m.group(1).strip(), lang=l.group(1).strip() if l else None,
                      desc=desc, stars=num(r'href="/[^"]+/stargazers"[^>]*>\s*(?:<[^>]*>\s*)*([\d,]+)'),
                      forks=num(r'href="/[^"]+/forks"[^>]*>\s*(?:<[^>]*>\s*)*([\d,]+)'),
                      stars_today_page=num(r'([\d,]+)\s*stars?\s*today')))
if not board:
    sys.exit('页面结构变了：匹配到 %d 个 Box-row 但解析出 0 项，检查选择器' % len(rows))
json.dump(board, open(os.path.join(DD, 'page.json'), 'w'), ensure_ascii=False, indent=1)
print('榜单 %d 项 -> %s/page.json' % (len(board), os.path.relpath(DD, ROOT)))

# ---------- 2. 选基线 ----------
# 追踪集合 = 今日榜单 + 上一期榜单。只取上一期的「榜单」而不是上一期快照里的
# 全部仓库——后者会把它自己的 prev 一并继承，逐日累积（实测 9 天涨到 49 个，
# 一个月后会是 300+）。方法论上只需要「上一期在榜、今天掉了」的那些来验证
# 掉榜=降温，历史上见过的所有仓库并不需要跟。
bpath, bdata = latest_snapshot(iso(D))
prev = []
if bpath:
    bday = os.path.basename(os.path.dirname(bpath))
    prev_board = os.path.join(ROOT, 'data', 'trending', bday, 'page.json')
    try:
        prev = [x['repo'] for x in json.load(open(prev_board))]
    except (OSError, ValueError):
        prev = list(bdata['repos'].keys())      # 上一期榜单丢了才退回全量
bts = baseline_ts(bdata) if bdata else None
print('基线: %s (%s)' % (os.path.relpath(bpath, ROOT) if bpath else '无',
                        bts.isoformat(timespec='seconds') if bts else '-'))

# ---------- 3. API 快照 ----------
# 预算说明：未认证的 GitHub API 只有 60 次/小时。所以这里的原则是
#   · 每个仓库只花 1 次 API 调用——repos/{r} 一次就带回 stars / forks /
#     license.spdx_id / created_at / pushed_at / issues / subscribers / topics
#   · 停更判断（HEAD 提交时间）改走 github.com 的 commits.atom，不吃 API 限额
#   · 下载量、首次提交这类补充指标按剩余预算花，花不起就跳过并记录
# 有令牌时预算是 5000，以上限制自然失效，采集深度不受影响。
RESERVE = 8          # 预留，避免把配额榨干导致下次运行直接失败

repos = [x['repo'] for x in board] + [r for r in prev if r not in {x['repo'] for x in board}]
budget = gh_remaining()
authed = budget > 100
print('API 预算 %d 次（%s）' % (budget, '已认证' if authed else '未认证，将按预算降级'))

start = now_local()
out, errs, skipped = {}, [], []
for r in repos:
    if not authed and gh_remaining() <= RESERVE:
        skipped.append(r); continue
    d = gh('repos/%s' % r)
    if d is None:
        out[r] = {'error': 'repos_404'}; errs.append(r); continue
    head_date, head_msg = gh_head_commit(r)      # 免费，不占 API 预算
    out[r] = dict(
        stars=d['stargazers_count'], forks=d['forks_count'], pushed_at=d['pushed_at'],
        created_at=d['created_at'], default_branch=d.get('default_branch'),
        head_date=head_date, head_msg=head_msg,
        license_spdx=(d.get('license') or {}).get('spdx_id'),
        open_issues=d['open_issues_count'], subscribers=d['subscribers_count'],
        size_kb=d['size'], archived=d['archived'], topics=d.get('topics', []),
        homepage=d.get('homepage') or '', desc_api=d.get('description') or '',
        owner_type=d['owner'].get('type'), is_fork=d['fork'])
end = now_local()
if skipped:
    print('⚠ 预算不足，%d 个仓库未采集：%s' % (len(skipped), ', '.join(skipped[:5])))

# ---------- 4. 榜单项目的补充指标（按剩余预算）----------
# 原本由模型按需调 bin/tools/*.py 现算，挪到采集层后分析层就不需要任何凭据。
# 未认证时这些是第一批被牺牲的——核心的增量、授权、停更判断都不依赖它们。
extras, extras_skipped = {}, []
for x in board:
    r = x['repo']
    if r not in out or 'error' in out[r]:
        continue
    if gh_remaining() <= RESERVE:
        extras_skipped.append(r); continue
    rel = gh('repos/%s/releases?per_page=100' % r)
    if rel:
        lat = rel[0]
        extras[r] = dict(
            releases=len(rel),
            asset_downloads_total=sum(a.get('download_count', 0) for e in rel for a in e.get('assets', [])),
            latest_tag=lat.get('tag_name'), latest_published=lat.get('published_at'),
            latest_downloads=sum(a.get('download_count', 0) for a in lat.get('assets', [])))
    else:
        extras[r] = dict(releases=0, asset_downloads_total=0)
print('补充指标 %d 项%s' % (len(extras),
      ('，%d 项因预算跳过' % len(extras_skipped)) if extras_skipped else ''))

snap = {'_ts_local_start': start.isoformat(timespec='seconds'),
        '_ts_local': end.isoformat(timespec='seconds'),
        '_ts_utc': end.astimezone(datetime.timezone.utc).isoformat(timespec='seconds'),
        '_baseline_file': os.path.relpath(bpath, ROOT) if bpath else None,
        '_baseline_ts': bts.isoformat(timespec='seconds') if bts else None,
        '_window_hours': round((start - bts).total_seconds() / 3600, 3) if bts else None,
        '_board_size': len(board), '_errors': errs,
        '_authed': authed, '_budget_start': budget,
        '_skipped_repos': skipped, '_skipped_extras': extras_skipped,
        'board_extras': extras, 'repos': out}
fn = os.path.join(DD, 'snap_%s.json' % a.tier)
json.dump(snap, open(fn, 'w'), ensure_ascii=False, indent=1)
print('快照 %d 项（%d 个 404）-> %s' % (len(out), len(errs), os.path.relpath(fn, ROOT)))
if snap['_window_hours']:
    print('窗口 %.2fh' % snap['_window_hours'])
