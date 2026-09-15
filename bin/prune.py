#!/usr/bin/env python3
"""按保留期清理旧数据与日志。每天由 run_task.sh 末尾调用一次。

保留期按「这东西坏了之后还查不查得着」来定，不是按体积：
  原始抓取页  纯诊断用途，出问题当天就看了，留 7 天
  任务日志    排查回溯窗口，留 30 天
  采集数据    trending 快照是方法论基线，留 180 天；且最新一份永不删
  站点页面    每天几十 KB，全留
"""
import os, re, sys, glob, time, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_HTML_DAYS = 7
LOG_DAYS      = 30
DATA_DAYS     = 180

def day_of(path):
    m = re.search(r'(\d{4}-\d{2}-\d{2})', path)
    return m.group(1) if m else None

def older_than(path, days):
    d = day_of(path)
    if d:
        return (datetime.date.today() - datetime.date.fromisoformat(d)).days > days
    return (time.time() - os.path.getmtime(path)) / 86400 > days

freed, removed = 0, []

# 1. 原始抓取页
for f in glob.glob(os.path.join(ROOT, 'data', 'trending', '*', 'page_raw*.html')):
    if older_than(f, RAW_HTML_DAYS):
        freed += os.path.getsize(f); os.remove(f); removed.append(f)

# 2. 日志
for f in glob.glob(os.path.join(ROOT, 'logs', '*.log')):
    if os.path.basename(f) == 'scheduler.log':
        continue                      # 调度器日志单独按大小截断，见下
    if older_than(f, LOG_DAYS):
        freed += os.path.getsize(f); os.remove(f); removed.append(f)

# scheduler.log 是长期追加的，按大小保留尾部
sl = os.path.join(ROOT, 'logs', 'scheduler.log')
if os.path.exists(sl) and os.path.getsize(sl) > 2 * 1024 * 1024:
    tail = open(sl, errors='replace').readlines()[-5000:]
    freed += os.path.getsize(sl)
    open(sl, 'w').writelines(tail)
    freed -= os.path.getsize(sl)
    removed.append(sl + '（截断至最后 5000 行）')

# 3. 采集数据目录（保护每个源的最新一份——trending 的是次日增量基线）
for src in ('trending', 'momoyu', 'douban', 'ainews'):
    dirs = sorted(d for d in glob.glob(os.path.join(ROOT, 'data', src, '*'))
                  if os.path.isdir(d) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', os.path.basename(d)))
    for d in dirs[:-1]:               # 最新一份永不删
        if older_than(d, DATA_DAYS):
            for r, _, fs in os.walk(d):
                for x in fs:
                    freed += os.path.getsize(os.path.join(r, x))
            __import__('shutil').rmtree(d); removed.append(d)

print('清理 %d 项，释放 %.1f MB' % (len(removed), freed / 1048576))
for r in removed[:10]:
    print('  ' + os.path.relpath(r, ROOT))
if len(removed) > 10:
    print('  …另外 %d 项' % (len(removed) - 10))
