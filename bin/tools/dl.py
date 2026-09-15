#!/usr/bin/env python3
"""查 release 资产下载总量，作为使用度的第二代理。
用法: dl.py <owner/repo>...

注意边界：走 Docker / PyPI / HuggingFace 分发的项目下载数为 0，不代表无人使用。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import gh

for r in sys.argv[1:]:
    rel = gh('repos/%s/releases?per_page=100' % r)
    if not rel:
        print('%-38s no releases' % r); continue
    tot = sum(a.get('download_count', 0) for x in rel for a in x.get('assets', []))
    latest = rel[0]
    lat = sum(a.get('download_count', 0) for a in latest.get('assets', []))
    print('%-38s releases=%3d total_asset_dl=%9s latest(%s %s)=%s'
          % (r, len(rel), format(tot, ','), latest['tag_name'][:18],
             latest['published_at'][:10], format(lat, ',')))
