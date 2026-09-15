#!/usr/bin/env python3
"""查提交节奏，用来识别机器批量生成的提交历史。
用法: cadence.py <owner/repo>...

看三个信号：间隔中位数、60 秒内的提交数、同秒提交数。
真人写代码不会几十次提交挤在同一秒。"""
import sys, os, collections, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import gh

for r in sys.argv[1:]:
    cs = gh('repos/%s/commits?per_page=100' % r)
    if not cs:
        print('%-38s 取不到' % r); continue
    ts = [datetime.datetime.fromisoformat(c['commit']['author']['date'].replace('Z', '+00:00'))
          for c in cs]
    authors = collections.Counter(c['commit']['author']['name'] for c in cs)
    gaps = [(ts[i] - ts[i + 1]).total_seconds() for i in range(len(ts) - 1)]
    same_sec = sum(1 for g in gaps if g == 0)
    med = sorted(gaps)[len(gaps) // 2] if gaps else 0
    print('%-38s last%d span=%7.1fh median_gap=%7.0fs <60s=%3d same_sec=%3d authors=%d top=%s'
          % (r, len(cs), (ts[0] - ts[-1]).total_seconds() / 3600, med,
             sum(1 for g in gaps if g < 60), same_sec, len(authors), authors.most_common(3)))
