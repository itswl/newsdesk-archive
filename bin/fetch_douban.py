#!/usr/bin/env python3
"""豆瓣热门电影采集。用法: fetch_douban.py [--tag 热门] [--n 10] [--sort recommend]

产物: data/douban/<YYYY-MM-DD>/{list.json, meta.json, detail/<id>.json}
"""
import sys, os, json, time, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *

TAGS = {'热门': '%E7%83%AD%E9%97%A8', '最新': '%E6%9C%80%E6%96%B0',
        '豆瓣高分': '%E8%B1%86%E7%93%A3%E9%AB%98%E5%88%86', '华语': '%E5%8D%8E%E8%AF%AD',
        '欧美': '%E6%AC%A7%E7%BE%8E', '日本': '%E6%97%A5%E6%9C%AC', '韩国': '%E9%9F%A9%E5%9B%BD'}

ap = argparse.ArgumentParser()
ap.add_argument('--tag', default='热门')
ap.add_argument('--n', type=int, default=10)
ap.add_argument('--sort', default='recommend', choices=['recommend', 'time', 'rank'])
a = ap.parse_args()
DD = data_dir('douban', create=True)

url = ('https://movie.douban.com/j/search_subjects?type=movie&tag=%s&sort=%s&page_limit=%d&page_start=0'
       % (TAGS.get(a.tag, a.tag), a.sort, a.n))
raw = curl(url, {'Referer': 'https://movie.douban.com/explore'})
try:
    subs = json.loads(raw)['subjects']
except Exception:
    sys.exit('列表接口失败: %s' % (raw or '')[:200])
json.dump(subs, open(os.path.join(DD, 'list.json'), 'w'), ensure_ascii=False, indent=1)
print('列表 %d 部' % len(subs))

det = os.path.join(DD, 'detail')
os.makedirs(det, exist_ok=True)
ok, fail = [], []
for s in subs:
    mid = s['id']
    body = curl('https://m.douban.com/rexxar/api/v2/movie/%s' % mid,
                {'Referer': 'https://movie.douban.com/subject/%s/' % mid})
    # 裸控制字符会让 JSON 解析失败，先清掉
    body = ''.join(ch for ch in (body or '') if ord(ch) >= 32 or ch == '\t')
    try:
        d = json.loads(body)
        assert d.get('title')
    except Exception:
        fail.append(mid); print('  FAIL %s' % mid); time.sleep(0.5); continue
    open(os.path.join(det, '%s.json' % mid), 'w').write(json.dumps(d, ensure_ascii=False))
    ok.append(mid)
    print('  OK   %s  %s  %s' % (mid, d['title'], (d.get('rating') or {}).get('value')))
    time.sleep(0.5)

json.dump({'date': iso(), 'tag': a.tag, 'sort': a.sort, 'ok': ok, 'failed': fail},
          open(os.path.join(DD, 'meta.json'), 'w'), ensure_ascii=False, indent=1)
print('成功 %d，失败 %d%s -> %s' % (len(ok), len(fail), ('：' + ','.join(fail)) if fail else '',
                                   os.path.relpath(DD, ROOT)))
