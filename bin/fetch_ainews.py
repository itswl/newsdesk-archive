#!/usr/bin/env python3
"""AI 新闻采集 —— 不依赖任何模型侧的联网能力。

只用 curl 拉公开 RSS/Atom 与免鉴权 JSON 接口，落盘成模型可直接读的文本。
这样分析层（claude 或 codex）只需读本地文件，不需要 web search 工具，
和 trending / momoyu / douban 三个任务同构。

产物: data/ainews/<YYYY-MM-DD>/{items.json, digest.txt}
"""
import sys, os, re, json, html, time, datetime, argparse
import xml.etree.ElementTree as ET
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *

# name, url, kind, 权重提示（写进 digest 供模型判断源的性质）
FEEDS = [
    ('OpenAI',          'https://openai.com/news/rss.xml',                              'rss',  '一手·厂商公告'),
    ('Google DeepMind', 'https://deepmind.google/blog/rss.xml',                         'rss',  '一手·厂商公告'),
    ('TechCrunch AI',   'https://techcrunch.com/category/artificial-intelligence/feed/', 'rss', '二手·科技媒体'),
    ('The Verge AI',    'https://www.theverge.com/rss/ai-artificial-intelligence/index.xml', 'rss', '二手·科技媒体'),
    ('Ars Technica',    'https://feeds.arstechnica.com/arstechnica/technology-lab',     'rss',  '二手·科技媒体'),
    ('Simon Willison',  'https://simonwillison.net/atom/everything/',                   'rss',  '二手·独立评论'),
    ('Hacker News',     'https://news.ycombinator.com/rss',                             'rss',  '社区·首页'),
    ('arXiv cs.AI',     'https://export.arxiv.org/api/query?search_query=cat:cs.AI'
                        '&sortBy=submittedDate&sortOrder=descending&max_results=25',    'rss',  '一手·论文预印本'),
]

AI_KW = re.compile(r'\b(ai|llm|gpt|claude|gemini|openai|anthropic|deepmind|deepseek|qwen|llama|'
                   r'mistral|nvidia|gpu|tpu|agent|agentic|transformer|diffusion|model|inference|'
                   r'training|rag|embedding|multimodal|reasoning|copilot|cursor|xai|grok)\b', re.I)

def txt(el):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', el or ''))).strip()

def parse_feed(body):
    """RSS 2.0 与 Atom 通吃。namespace 各家不同，一律按 localname 匹配。"""
    out = []
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return out
    def local(e):
        return e.tag.rsplit('}', 1)[-1]
    for node in root.iter():
        if local(node) not in ('item', 'entry'):
            continue
        rec = {}
        for c in node:
            n = local(c)
            if n == 'title':
                rec['title'] = txt(c.text)
            elif n == 'link':
                rec['link'] = (c.get('href') or c.text or '').strip()
            elif n in ('pubDate', 'published', 'updated') and 'date' not in rec:
                rec['date'] = (c.text or '').strip()
            elif n in ('description', 'summary', 'content') and not rec.get('summary'):
                rec['summary'] = txt(c.text)[:400]
        if rec.get('title'):
            out.append(rec)
    return out

def norm_date(s):
    """各家日期格式不统一，统一成 ISO；解析不了就返回原串。"""
    s = (s or '').strip()
    for f in ('%a, %d %b %Y %H:%M:%S %z', '%a, %d %b %Y %H:%M:%S %Z',
              '%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S.%f%z'):
        try:
            d = datetime.datetime.strptime(s.replace('GMT', '+0000'), f)
            if d.tzinfo is None:
                d = d.replace(tzinfo=datetime.timezone.utc)
            return d.astimezone().isoformat(timespec='minutes')
        except ValueError:
            continue
    return s

ap = argparse.ArgumentParser()
ap.add_argument('--hours', type=int, default=36, help='只保留这个时间窗内的条目')
ap.add_argument('--per-feed', type=int, default=25)
a = ap.parse_args()

DD = data_dir('ainews', create=True)
cutoff = datetime.datetime.now().astimezone() - datetime.timedelta(hours=a.hours)
items, stats = [], []

for name, url, kind, tag in FEEDS:
    body = curl(url, timeout=30)
    if not body:
        stats.append((name, 'FAIL', 0, 0)); print('  %-18s 抓取失败' % name); continue
    recs = parse_feed(body)
    kept = []
    for r in recs[:a.per_feed]:
        iso_d = norm_date(r.get('date'))
        fresh = True
        try:
            fresh = datetime.datetime.fromisoformat(iso_d) >= cutoff
        except Exception:
            pass                      # 解析不出日期的一律保留，交给模型判断
        if not fresh:
            continue
        kept.append(dict(source=name, source_kind=tag, title=r['title'],
                         link=r.get('link', ''), date=iso_d, summary=r.get('summary', '')))
    items += kept
    stats.append((name, 'OK', len(recs), len(kept)))
    print('  %-18s 总 %-4d 窗内 %d' % (name, len(recs), len(kept)))

# Hacker News：按分数取，比 RSS 更能反映社区关注度
hn_url = ('https://hn.algolia.com/api/v1/search_by_date?tags=story'
          '&numericFilters=created_at_i%%3E%d,points%%3E40&hitsPerPage=80'
          % int((datetime.datetime.now() - datetime.timedelta(hours=a.hours)).timestamp()))
body = curl(hn_url, timeout=30)
hn_kept = 0
try:
    for h in json.loads(body)['hits']:
        t = h.get('title') or ''
        if not AI_KW.search(t):
            continue
        items.append(dict(source='HN Algolia', source_kind='社区·按分数',
                          title=t, link=h.get('url') or ('https://news.ycombinator.com/item?id=%s' % h['objectID']),
                          date=norm_date(h.get('created_at')), points=h.get('points'),
                          comments=h.get('num_comments'),
                          hn_link='https://news.ycombinator.com/item?id=%s' % h['objectID'], summary=''))
        hn_kept += 1
    stats.append(('HN Algolia', 'OK', '-', hn_kept))
    print('  %-18s AI 相关 %d' % ('HN Algolia', hn_kept))
except Exception as e:
    stats.append(('HN Algolia', 'FAIL', 0, 0)); print('  HN Algolia 失败: %r' % e)

json.dump({'fetched_at': now_local().isoformat(timespec='seconds'),
           'window_hours': a.hours, 'sources': [list(s) for s in stats], 'items': items},
          open(os.path.join(DD, 'items.json'), 'w'), ensure_ascii=False, indent=1)

# 给模型读的纯文本版
L = ['AI 新闻采集 · %s' % now_local().strftime('%Y-%m-%d %H:%M %z'),
     '时间窗: 最近 %d 小时 · 条目 %d' % (a.hours, len(items)), '',
     '源状态:']
for n, st, tot, kept in stats:
    L.append('  %-18s %-5s 窗内 %s' % (n, st, kept))
L.append('')
by_src = {}
for it in items:
    by_src.setdefault(it['source'], []).append(it)
for src in [f[0] for f in FEEDS] + ['HN Algolia']:
    lst = by_src.get(src)
    if not lst:
        continue
    L += ['=' * 72, '## %s  [%s]' % (src, lst[0]['source_kind']), '']
    for i, it in enumerate(sorted(lst, key=lambda x: x.get('points') or 0, reverse=True), 1):
        extra = ('  [%s分/%s评]' % (it['points'], it.get('comments'))) if it.get('points') else ''
        L.append('%2d. %s%s' % (i, it['title'], extra))
        L.append('    %s  %s' % (it['date'], it['link']))
        if it.get('summary'):
            L.append('    %s' % it['summary'][:300])
    L.append('')
open(os.path.join(DD, 'digest.txt'), 'w').write('\n'.join(L))
print('-> %s/{items.json, digest.txt}  共 %d 条' % (os.path.relpath(DD, ROOT), len(items)))
