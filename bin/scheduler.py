#!/usr/bin/env python3
"""每日简报调度器 —— 纯 stdlib，不依赖 launchd / cron。

设计要点：
- 睡眠补跑：Mac 合盖睡过某个时间点后，唤醒时若仍在补跑窗口内会立即执行。
  这是 cron 做不到、launchd 才有的行为，这里用「按日状态 + 到点判定」自己实现。
- 串行执行：多个任务同时到期（典型场景是长时间睡眠后唤醒）时逐个跑，不并发。
- 幂等：每个任务每天只成功执行一次，状态落盘，重启进程不会重跑也不会漏跑。
- 失败重试：默认 2 次，间隔 10 分钟；仍失败则当天标记 failed，不再重试。

用法：
  scheduler.py              前台常驻
  scheduler.py --once TASK  立刻跑一个任务后退出（忽略调度表与状态）
  scheduler.py --status     打印今日状态后退出
  scheduler.py --dry-run    只打印未来 24h 的触发计划
"""
import os, sys, json, time, signal, argparse, subprocess, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, 'state', 'scheduler.json')
PIDF = os.path.join(ROOT, 'state', 'scheduler.pid')
LOG = os.path.join(ROOT, 'logs', 'scheduler.log')

# ┌─ 时刻 ─┬─ 任务 ─┐  改这里即可调整定时表，改完重启调度器
#
# ⚠️ 相邻两档间隔 5 小时 5 分是有意的：Claude 订阅的用量限额按 5 小时滚动窗口
#    重置，隔 5h05m 能让每个任务落进全新的窗口、互不抢额度，多出的 5 分钟是
#    防止卡在边界上。别把它「整理」成 6 小时整——那样看着齐整，实际会让相邻
#    两档共用同一个窗口。
SCHEDULE = [
    ('06:30', 'ai'),
    ('11:35', 'douban'),
    ('16:40', 'trending'),
    ('21:45', 'momoyu'),
]
def _conf(key, default):
    """读 bin/config.conf —— 引擎与模型的唯一配置入口，与 run_task.sh 共用。"""
    try:
        for line in open(os.path.join(ROOT, 'bin', 'config.conf')):
            line = line.split('#', 1)[0].strip()
            if line.startswith(key + '='):
                return line.split('=', 1)[1].strip().strip('\'"')
    except OSError:
        pass
    return default

ENGINE = _conf('ENGINE', 'claude')   # 默认分析引擎，改 bin/config.conf
TASK_TIMEOUT_MIN = 30  # 单任务上限。超时必须有：调度器是单线程串行的，
                       # 一个卡住的模型调用会让后面所有任务再也不触发，
                       # 而且是静默死掉——日志停在「分析中」就没下文。
# 补跑的截止点不是拍一个固定小时数，而是「下一档开始前 N 分钟」——这样它自动
# 跟随上面的时间表。写死 6 小时会越过 5h05m 的间隔，把补跑的任务推进下一档的
# 用量窗口里，正好毁掉这套间隔要避免的事。
CATCHUP_MARGIN_MIN = 30
RETRIES = 2            # 失败重试次数
RETRY_GAP_MIN = 10     # 重试间隔（分钟）
TICK = 30              # 轮询间隔（秒）

_stop = False

def log(msg):
    line = '%s  %s' % (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), msg)
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, 'a') as f:
        f.write(line + '\n')

def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}

def save_state(s):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + '.tmp'
    json.dump(s, open(tmp, 'w'), ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)      # 原子替换，避免进程被杀时留下半个文件

def due_at(task_time, day):
    h, m = map(int, task_time.split(':'))
    return datetime.datetime.combine(day, datetime.time(h, m))


def next_slot_after(task_time, day):
    """时间表里紧跟其后的那一档；已是当天最后一档则取次日第一档。"""
    times = sorted(t for t, _ in SCHEDULE)
    for t in times:
        if t > task_time:
            return due_at(t, day)
    return due_at(times[0], day + datetime.timedelta(days=1))

def run_task(task, engine=None):
    engine = engine or ENGINE
    log('▶ %s 开始 (engine=%s, 超时 %d 分钟)' % (task, engine, TASK_TIMEOUT_MIN))
    t0 = time.time()
    # start_new_session 让子进程自成进程组，超时时才能连同它派生的
    # claude / codex / curl 一起杀掉；只杀 bash 会留下孤儿继续跑。
    p = subprocess.Popen(['/bin/bash', os.path.join(ROOT, 'bin', 'run_task.sh'), task, engine],
                         cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, start_new_session=True)
    try:
        out, err = p.communicate(timeout=TASK_TIMEOUT_MIN * 60)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        log('⏱ %s 超过 %d 分钟未结束，终止整个进程组' % (task, TASK_TIMEOUT_MIN))
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(os.getpgid(p.pid), sig)
            except (ProcessLookupError, PermissionError):
                break
            try:
                p.communicate(timeout=10)
                break
            except subprocess.TimeoutExpired:
                continue
        out, err, rc = '', '超时终止', -1
    dt = time.time() - t0
    if rc == 0:
        log('✔ %s 完成，耗时 %.1f 分钟' % (task, dt / 60))
    else:
        tail = (out or '')[-400:] + (err or '')[-400:]
        log('✘ %s 失败 exit=%s，耗时 %.1f 分钟\n%s' % (task, rc, dt / 60, tail.strip()))
    return rc == 0

def tick(state):
    now = datetime.datetime.now()
    today = now.date().isoformat()
    for task_time, task in SCHEDULE:
        rec = state.get(task) or {}
        if rec.get('date') == today and rec.get('status') in ('ok', 'skipped', 'failed'):
            continue
        due = due_at(task_time, now.date())
        if now < due:
            continue
        late_h = (now - due).total_seconds() / 3600
        deadline = next_slot_after(task_time, now.date()) - datetime.timedelta(minutes=CATCHUP_MARGIN_MIN)
        if now > deadline:
            log('⊘ %s 错过 %.1fh，已过补跑截止点（下一档前 %d 分钟），今日跳过'
                % (task, late_h, CATCHUP_MARGIN_MIN))
            state[task] = {'date': today, 'status': 'skipped', 'late_hours': round(late_h, 2)}
            save_state(state)
            continue
        # 重试节流：距上次尝试不足间隔就先不动
        last = rec.get('last_attempt_ts')
        if last and (time.time() - last) < RETRY_GAP_MIN * 60:
            continue
        attempts = rec.get('attempts', 0) if rec.get('date') == today else 0
        if late_h > 0.05:
            # 一并报出离下一档还有多久：补跑必然与下一档挤同一个用量窗口，
            # 余量太小时值得知道
            left = (deadline + datetime.timedelta(minutes=CATCHUP_MARGIN_MIN) - now).total_seconds() / 3600
            log('↻ %s 补跑（错过 %.1fh，距下一档 %.1fh）' % (task, late_h, left))
        ok = run_task(task)
        attempts += 1
        if ok:
            state[task] = {'date': today, 'status': 'ok', 'attempts': attempts,
                           'finished': datetime.datetime.now().isoformat(timespec='seconds')}
        elif attempts > RETRIES:
            log('✘ %s 已重试 %d 次仍失败，今日放弃' % (task, RETRIES))
            state[task] = {'date': today, 'status': 'failed', 'attempts': attempts,
                           'last_attempt_ts': time.time()}
        else:
            log('… %s 第 %d 次失败，%d 分钟后重试' % (task, attempts, RETRY_GAP_MIN))
            state[task] = {'date': today, 'status': 'retrying', 'attempts': attempts,
                           'last_attempt_ts': time.time()}
        save_state(state)

def on_signal(sig, frame):
    global _stop
    _stop = True
    log('收到信号 %d，本轮结束后退出' % sig)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--once', metavar='TASK')
    ap.add_argument('--engine', choices=['claude', 'codex'], default=None,
                    help='覆盖默认分析引擎')
    ap.add_argument('--status', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    if a.once:
        valid = [t for _, t in SCHEDULE]
        if a.once not in valid:
            sys.exit('未知任务 %s，可选: %s' % (a.once, ', '.join(valid)))
        sys.exit(0 if run_task(a.once, a.engine) else 1)

    if a.status:
        s = load_state()
        today = datetime.date.today().isoformat()
        print('今天 %s' % today)
        for tt, task in SCHEDULE:
            r = s.get(task) or {}
            mark = {'ok': '✔', 'failed': '✘', 'skipped': '⊘', 'retrying': '…'}.get(
                r.get('status') if r.get('date') == today else None, '·')
            print('  %s %-9s %s  %s' % (mark, task, tt,
                  ('%s %s' % (r.get('status'), r.get('finished') or '')).strip()
                  if r.get('date') == today else '今日未跑'))
        if os.path.exists(PIDF):
            pid = open(PIDF).read().strip()
            alive = subprocess.run(['kill', '-0', pid], capture_output=True).returncode == 0
            print('调度器 pid=%s %s' % (pid, '运行中' if alive else '（已死，pid 文件过期）'))
        else:
            print('调度器未运行')
        return

    if a.dry_run:
        now = datetime.datetime.now()
        print('未来 24 小时触发计划（现在 %s）：' % now.strftime('%Y-%m-%d %H:%M'))
        for tt, task in SCHEDULE:
            d = due_at(tt, now.date())
            if d <= now:
                d = due_at(tt, now.date() + datetime.timedelta(days=1))
            print('  %s  %-9s  %.1f 小时后' % (d.strftime('%m-%d %H:%M'), task,
                                              (d - now).total_seconds() / 3600))
        print('补跑截止：各档下一档开始前 %d 分钟 · 失败重试 %d 次 · 间隔 %d 分钟'
              % (CATCHUP_MARGIN_MIN, RETRIES, RETRY_GAP_MIN))
        return

    # 单实例
    if os.path.exists(PIDF):
        old = open(PIDF).read().strip()
        if subprocess.run(['kill', '-0', old], capture_output=True).returncode == 0:
            sys.exit('调度器已在运行 (pid %s)' % old)
        log('清理过期 pid 文件 (%s)' % old)
    os.makedirs(os.path.dirname(PIDF), exist_ok=True)
    open(PIDF, 'w').write(str(os.getpid()))
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    if a.engine:
        globals()['ENGINE'] = a.engine
    log('调度器启动 pid=%d  引擎=%s  定时表: %s' % (
        os.getpid(), ENGINE, ' · '.join('%s→%s' % (t, k) for t, k in SCHEDULE)))
    log('补跑截止=下一档前 %d 分钟 · 重试 %d 次/间隔 %d 分钟 · 轮询 %ds' % (
        CATCHUP_MARGIN_MIN, RETRIES, RETRY_GAP_MIN, TICK))
    try:
        while not _stop:
            try:
                tick(load_state())
            except Exception as e:
                log('!! 调度循环异常（已忽略，继续运行）: %r' % e)
            for _ in range(TICK):
                if _stop:
                    break
                time.sleep(1)
    finally:
        if os.path.exists(PIDF):
            os.remove(PIDF)
        log('调度器退出')

if __name__ == '__main__':
    main()
