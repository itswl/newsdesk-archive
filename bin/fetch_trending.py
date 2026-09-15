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
bpath, bdata = latest_snapshot(iso(D))
prev = list(bdata['repos'].keys()) if bdata else []
bts = baseline_ts(bdata) if bdata else None
print('基线: %s (%s)' % (os.path.relpath(bpath, ROOT) if bpath else '无',
                        bts.isoformat(timespec='seconds') if bts else '-'))

# ---------- 3. API 快照 ----------
repos = [x['repo'] for x in board] + [r for r in prev if r not in {x['repo'] for x in board}]
start = now_local()
out, errs = {}, []
for r in repos:
    d = gh('repos/%s' % r)
    if d is None:
        out[r] = {'error': 'repos_404'}; errs.append(r); continue
    db = d.get('default_branch')
    c = gh('repos/%s/commits/%s' % (r, db))
    lic = gh('repos/%s/license' % r)
    out[r] = dict(stars=d['stargazers_count'], forks=d['forks_count'], pushed_at=d['pushed_at'],
                  created_at=d['created_at'], default_branch=db,
                  head_date=(c or {}).get('commit', {}).get('author', {}).get('date'),
                  head_msg=((c or {}).get('commit', {}).get('message', '') or '').split('\n')[0][:120],
                  license_spdx=(d.get('license') or {}).get('spdx_id'),
                  license_endpoint_ok=lic is not None, license_file=(lic or {}).get('name'),
                  license_endpoint_spdx=((lic or {}).get('license') or {}).get('spdx_id'),
                  open_issues=d['open_issues_count'], subscribers=d['subscribers_count'],
                  size_kb=d['size'], archived=d['archived'], topics=d.get('topics', []),
                  homepage=d.get('homepage') or '', desc_api=d.get('description') or '',
                  owner_type=d['owner'].get('type'), is_fork=d['fork'])
end = now_local()

# ---------- 4. 榜单项目的补充指标 ----------
# 这些原本由模型按需调 bin/tools/*.py 现算，但那要求分析层持有 gh 凭据。
# 挪到采集层后，分析层可以完全不接触任何凭据，沙箱得以收紧。
extras = {}
for x in board:
    r = x['repo']
    rel = gh('repos/%s/releases?per_page=100' % r)
    if rel:
        tot = sum(a.get('download_count', 0) for e in rel for a in e.get('assets', []))
        lat = rel[0]
        extras.setdefault(r, {}).update(
            releases=len(rel), asset_downloads_total=tot,
            latest_tag=lat.get('tag_name'), latest_published=lat.get('published_at'),
            latest_downloads=sum(a.get('download_count', 0) for a in lat.get('assets', [])))
    else:
        extras.setdefault(r, {}).update(releases=0, asset_downloads_total=0)
    # 首次提交时间与提交总数：用 Link 头的 last page 反推，只花两次请求
    n = gh_last_page('repos/%s/commits?per_page=1' % r)
    if n > 1:
        d = gh('repos/%s/commits?per_page=1&page=%d' % (r, n))
        if d:
            extras[r]['total_commits_approx'] = n
            extras[r]['first_commit'] = d[0]['commit']['author']['date']
print('补充指标 %d 项（下载量 + 首次提交）' % len(extras))

snap = {'_ts_local_start': start.isoformat(timespec='seconds'),
        '_ts_local': end.isoformat(timespec='seconds'),
        '_ts_utc': end.astimezone(datetime.timezone.utc).isoformat(timespec='seconds'),
        '_baseline_file': os.path.relpath(bpath, ROOT) if bpath else None,
        '_baseline_ts': bts.isoformat(timespec='seconds') if bts else None,
        '_window_hours': round((start - bts).total_seconds() / 3600, 3) if bts else None,
        '_board_size': len(board), '_errors': errs, 'board_extras': extras, 'repos': out}
fn = os.path.join(DD, 'snap_%s.json' % a.tier)
json.dump(snap, open(fn, 'w'), ensure_ascii=False, indent=1)
print('快照 %d 项（%d 个 404）-> %s' % (len(out), len(errs), os.path.relpath(fn, ROOT)))
if snap['_window_hours']:
    print('窗口 %.2fh' % snap['_window_hours'])
