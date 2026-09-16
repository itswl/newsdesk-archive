#!/usr/bin/env python3
"""摸摸鱼热榜采集 + 解析。产物: data/momoyu/<YYYY-MM-DD>/{raw.json, parsed.txt}

接口变更时自动降级：抓首页 HTML 找 /assets/index-*.js，用 Python 读进内存正则搜 url:"
（文件是单行压缩的，grep 会超时，必须整体读入）。

按日期分目录后，原先「当前文件 + 轮转归档」的机制已不需要。
"""
import sys, os, re, json, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *

API = 'https://momoyu.cc/api/hot/list?type=1'
HDR = {'Referer': 'https://momoyu.cc/'}

def discover():
    """接口路径变了就从打包产物里重新提取。"""
    home = curl('https://momoyu.cc/')
    if not home:
        return []
    found = []
    for js in re.findall(r'/assets/index-[\w.-]+\.js', home):
        body = curl('https://momoyu.cc' + js, timeout=60)
        if not body:
            continue
        found += [u for u in re.findall(r'url:"([^"]+)"', body) if 'hot' in u or 'list' in u]
    return sorted(set(found))

raw = curl(API, HDR)
data = None
if raw:
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
if not data or not data.get('data'):
    print('主接口异常，启动降级探测…', file=sys.stderr)
    cands = discover()
    print('候选接口: %s' % (cands or '无'), file=sys.stderr)
    sys.exit('momoyu 接口不可用，候选路径见上，需人工确认')

cats = data['data']
print('分类 %d，源 %d，条目 %d' % (
    len(cats), sum(len(c.get('data') or []) for c in cats),
    sum(len(s.get('data') or []) for c in cats for s in c.get('data') or [])))

# 按内容日期归档，而不是运行日期 —— 21:45 跑的是当天的榜
first = cats[0]['data'][0].get('create_time')
content_day = (datetime.datetime.fromisoformat(first.replace('Z', '+00:00'))
               + datetime.timedelta(hours=8)).date()
DD = data_dir('momoyu', content_day, create=True)

lines, stale = [], []
NOW = datetime.datetime.now().astimezone()
for c in cats:
    lines += ['=' * 70, '## 分类: %s' % c.get('name'), '']
    for s in c.get('data') or []:
        ct = s.get('create_time')
        try:
            t = datetime.datetime.fromisoformat(ct.replace('Z', '+00:00')) + datetime.timedelta(hours=8)
            ts, lag = t.strftime('%m-%d %H:%M'), (NOW - t.replace(tzinfo=NOW.tzinfo)).total_seconds() / 3600
        except (ValueError, TypeError, AttributeError):
            ts, lag = str(ct), None
        if lag and lag > 6:
            stale.append((s.get('name'), ts, round(lag, 1)))
        items = s.get('data') or []
        lines.append('### [%s] 更新:%s (共%d条)' % (s.get('name'), ts, len(items)))
        for i, it in enumerate(items[:15], 1):
            lines.append('%2d. %s || %s' % (i, it.get('title', ''), it.get('extra', '')))
        lines.append('')

open(os.path.join(DD, 'raw.json'), 'w').write(json.dumps(data, ensure_ascii=False))
open(os.path.join(DD, 'parsed.txt'), 'w').write('\n'.join(lines))
print('-> %s/{raw.json, parsed.txt}' % os.path.relpath(DD, ROOT))
if stale:
    print('⚠ 滞后源: ' + '; '.join('%s(%s, 滞后%.1fh)' % s for s in stale))
