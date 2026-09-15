#!/usr/bin/env python3
"""跨期比对：把「今天 vs 前 N 天」的差异算好落盘，供分析层直接引用。

为什么算在采集层而不是让模型自己翻历史：
  一是模型逐日读原始数据既贵又容易看漏；
  二是「新出现 / 已报过 / 连续第几天」这类判断必须是确定性的——
  交给模型凭印象判断，写出来的「今日增量」就不可信了。
Trending 之所以有说服力，正是因为它比的是预先算好的具体量，这里推广到另外三个源。

用法: history.py <源> [日期]     源 ∈ ainews|momoyu|douban|trending
产物: data/<源>/<日期>/history.json
"""
import sys, os, re, json, glob, datetime, collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 回看一周。再长意义不大：momoyu 的话题很少真热过三四天，
# 拉到两周主要是把不更新的源(掘金)的陈年条目翻出来，信号被噪声淹掉。
WINDOW = 7

# 只带信号不带清单：全量条目在各源自己的 digest/parsed 文件里已经有了，
# 这里重复一遍既占上下文又没新信息。下面各源只输出「需要单独标注的那部分」。
CAP = 12

def days_before(src, date, n=WINDOW):
    """按日期倒序返回 date 之前的数据目录。"""
    out = []
    for d in sorted(glob.glob(os.path.join(ROOT, 'data', src, '*')), reverse=True):
        b = os.path.basename(d)
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', b) and b < date:
            out.append((b, d))
        if len(out) >= n:
            break
    return out

def load(p, default=None):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return default

def norm_link(u):
    u = (u or '').split('?')[0].split('#')[0].rstrip('/')
    return re.sub(r'^https?://(www\.)?', '', u).lower()

def norm_title(t):
    return re.sub(r'\s+', '', re.sub(r'[【】\[\]（）()「」“”"\'：:，,。.！!？?、·\-—_]', '', t or '')).lower()

# ── ainews：按链接判重，让「只报增量」可核验而不是靠模型印象 ──
def h_ainews(date, cur):
    seen = {}
    for d, p in days_before('ainews', date):
        for it in (load(os.path.join(p, 'items.json'), {}) or {}).get('items', []):
            seen.setdefault(norm_link(it.get('link')), d)
    new, rep = 0, []
    for it in cur.get('items', []):
        k = norm_link(it.get('link'))
        if k in seen:
            rep.append({'title': it['title'], 'source': it['source'], 'first_seen': seen[k]})
        else:
            new += 1
    # 只报近期有失败的源；全都正常时不占版面
    bad = collections.defaultdict(list)
    for d, p in days_before('ainews', date, WINDOW):
        for name, st, _t, kept in (load(os.path.join(p, 'items.json'), {}) or {}).get('sources', []):
            if st != 'OK' or not kept:
                bad[name].append(d)
    return {'new_count': new, 'repeat_count': len(rep),
            'repeat_items': rep[:CAP],       # 新条目不列——不在这份名单里的就是新的
            'sources_with_gaps': dict(bad)}

# ── momoyu：话题在榜的连续性，比单日热度更能说明是真热还是一阵风 ──
def h_momoyu(date, cur):
    def titles(raw):
        out = {}
        for c in raw.get('data', []):
            for s in c.get('data', []):
                for it in (s.get('data') or [])[:15]:
                    out[norm_title(it.get('title'))] = {
                        'title': it.get('title'), 'extra': it.get('extra'), 'src': s.get('name')}
        return out
    today = titles(cur)
    hist = [(d, titles(load(os.path.join(p, 'raw.json'), {}) or {})) for d, p in days_before('momoyu', date)]
    persist, fresh = [], []
    for k, v in today.items():
        ds = [d for d, t in hist if k in t]
        rec = {'title': v['title'], 'src': v['src'], 'today_extra': v['extra'],
               'days_on_list': len(ds) + 1, 'prev_days': ds[:3]}
        if ds:
            rec['prev_extra'] = hist[[d for d, _ in hist].index(ds[0])][1][k]['extra']
            persist.append(rec)
        else:
            fresh.append(rec)
    dropped = []
    if hist:
        pd, pt = hist[0]
        for k, v in pt.items():
            if k not in today:
                dropped.append({'title': v['title'], 'src': v['src'], 'last_extra': v['extra'], 'last_day': pd})
    persist.sort(key=lambda x: -x['days_on_list'])
    # 掉榜项只留原本有热度值的：没热度值的源本来就分不出冷热，列出来是噪声
    dropped = [x for x in dropped if x['last_extra']]
    return {'history_days': len(hist), 'persistent': persist[:CAP],
            'persistent_total': len(persist),
            'new_today_count': len(fresh), 'dropped_from_prev': dropped[:CAP]}

# ── douban：评分会漂、评价人数增速才是真热度 ──
def h_douban(date, cur_list, cur_detail):
    prev = days_before('douban', date)
    pl = {x['id']: x for x in (load(os.path.join(prev[0][1], 'list.json'), []) or [])} if prev else {}
    first = {}
    for d, p in reversed(prev):                       # 由早到晚，记下最早一次的数值
        for mid, det in _details(p).items():
            first.setdefault(mid, (d, det))
    rows, entered = [], []
    for m in cur_list:
        mid = m['id']
        det = cur_detail.get(mid, {})
        r = (det.get('rating') or {})
        row = {'id': mid, 'title': det.get('title') or m.get('title'),
               'rating': r.get('value'), 'count': r.get('count')}
        if mid in first:
            fd, fdet = first[mid]
            fr = (fdet.get('rating') or {})
            row.update(since=fd,
                       rating_delta=round((r.get('value') or 0) - (fr.get('value') or 0), 2),
                       count_delta=(r.get('count') or 0) - (fr.get('count') or 0))
        else:
            entered.append(row['title'])
        rows.append(row)
    left = [pl[i].get('title') for i in pl if i not in {m['id'] for m in cur_list}]
    return {'history_days': len(prev), 'movies': rows,
            'entered_since_prev': entered, 'left_since_prev': left}

def _details(p):
    out = {}
    for f in glob.glob(os.path.join(p, 'detail', '*.json')):
        d = load(f)
        if d:
            out[os.path.basename(f)[:-5]] = d
    return out

# ── trending：在榜连续性与名次轨迹，补足单期基线看不到的东西 ──
def h_trending(date, board):
    hist = [(d, [x['repo'] for x in (load(os.path.join(p, 'page.json'), []) or [])])
            for d, p in days_before('trending', date)]
    rows = []
    for x in board:
        appear = [(d, b.index(x['repo']) + 1) for d, b in hist if x['repo'] in b]
        rows.append({'repo': x['repo'], 'rank_today': x['rank'],
                     'boards_seen': len(appear) + 1,
                     'rank_history': [{'date': d, 'rank': r} for d, r in appear[:4]],
                     'returning': bool(appear) and appear[0][0] != (hist[0][0] if hist else None)})
    prev = hist[0][1] if hist else []
    return {'history_boards': len(hist), 'repos': rows,
            'dropped_from_prev': [r for r in prev if r not in {x['repo'] for x in board}]}

def main():
    src = sys.argv[1]
    date = sys.argv[2] if len(sys.argv) > 2 else datetime.date.today().isoformat()
    dd = os.path.join(ROOT, 'data', src, date)
    if not os.path.isdir(dd):
        sys.exit('没有 %s 的 %s 数据' % (src, date))
    if src == 'ainews':
        out = h_ainews(date, load(os.path.join(dd, 'items.json'), {}) or {})
    elif src == 'momoyu':
        out = h_momoyu(date, load(os.path.join(dd, 'raw.json'), {}) or {})
    elif src == 'douban':
        out = h_douban(date, load(os.path.join(dd, 'list.json'), []) or [], _details(dd))
    elif src == 'trending':
        out = h_trending(date, load(os.path.join(dd, 'page.json'), []) or [])
    else:
        sys.exit('未知源: %s' % src)
    out['_window_days'] = WINDOW
    out['_generated'] = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    json.dump(out, open(os.path.join(dd, 'history.json'), 'w'), ensure_ascii=False, indent=1)
    k = {'ainews': 'new_count', 'momoyu': 'new_today_count', 'douban': 'history_days',
         'trending': 'history_boards'}[src]
    print('跨期比对 -> %s/history.json（回看 %d 天，%s=%s）'
          % (os.path.relpath(dd, ROOT), WINDOW, k, out.get(k)))

if __name__ == '__main__':
    main()
