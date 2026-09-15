#!/usr/bin/env python3
"""查首次提交时间与提交总数（用 Link 头反推页数，只花两次请求）。
用法: firstcommit.py <owner/repo>...

用途：核查 created_at 是否等于项目真实年龄——旧仓库壳套新项目时两者会差很多。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import gh, gh_last_page

for r in sys.argv[1:]:
    n = gh_last_page('repos/%s/commits?per_page=1' % r)
    d = gh('repos/%s/commits?per_page=1&page=%d' % (r, n))
    if not d:
        print('%-40s 取不到' % r); continue
    c = d[0]['commit']
    print('%-40s total_commits~%-7d first=%s  %s'
          % (r, n, c['author']['date'], c['message'].split('\n')[0][:60]))
