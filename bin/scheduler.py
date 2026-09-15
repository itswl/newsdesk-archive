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
import os, sys, re, json, time, signal, argparse, subprocess, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alert                      # 告警发送，未配置时自动静默

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, 'state', 'scheduler.json')
PIDF = os.path.join(ROOT, 'state', 'scheduler.pid')
LOG = os.path.join(ROOT, 'logs', 'scheduler.log')

# ── 用量窗口假设 ───────────────────────────────────────────────────────
# 相邻两档间隔 5h05m 不是随手定的：Claude 订阅的用量限额按 5 小时滚动窗口
# 重置，隔满一个窗口能让每个任务落进全新窗口、互不抢额度，多出的 5 分钟防止
# 卡在边界上。
#
# ⚠️ 但这是在押注一个别人未文档化的行为，它有三个隐含前提：
#      ① 一直用 Claude 订阅（换成 codex 或 API 计费，这个间隔就没意义）
#      ② 窗口语义不变（长度、是否滚动，官方随时可改且不会通知）
#      ③ 重试与补跑不会挤进相邻窗口（由 CATCHUP_MARGIN_MIN 保证）
#    任一条变了，时间表**看起来仍然正确**却已经失效——这是最难发现的失效方式。
#    所以：把假设写成下面的常量，由 validate_schedule() 在启动时核对；
#    并且把日志里的「配额错误」单独识别出来告警，那才是失效的第一个信号。
USAGE_WINDOW_HOURS = 5.0    # 用量窗口长度。换计费方式就改这里，时间表会跟着被校验
SLOT_EDGE_MARGIN_MIN = 5    # 额外余量，防止卡在窗口边界上

# ┌─ 时刻 ─┬─ 任务 ─┐  改这里即可调整定时表，改完重启调度器
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

# 用量配额相关的报错特征。命中时单独告警——它意味着上面的窗口假设失效了，
# 而不是普通的任务失败。宁可多报也不要漏：漏掉它就会一直以为「偶尔失败」。
QUOTA_PATTERNS = re.compile(
    r'usage limit|rate.?limit|quota|too many requests|429|'
    r'5-hour|five.hour|limit reached|overloaded|capacity',
    re.I)

_stop = False


def schedule_gaps():
    """相邻两档的间隔（小时），含跨日那一段。"""
    times = sorted(t for t, _ in SCHEDULE)
    mins = [int(t[:2]) * 60 + int(t[3:]) for t in times]
    return [((mins[(i + 1) % len(mins)] - m) % (24 * 60)) / 60.0
            for i, m in enumerate(mins)]


def validate_schedule():
    """启动时核对时间表仍然满足用量窗口假设。不满足就拒绝启动。

    写死的数字会随着改时间表悄悄失效，这个函数让「间隔」和「窗口长度」绑在
    一起：改了 SCHEDULE 而没想清楚窗口，启动就会被拦下。
    """
    need = USAGE_WINDOW_HOURS + SLOT_EDGE_MARGIN_MIN / 60.0
    gaps = schedule_gaps()
    bad = [(t, g) for (t, _), g in zip(sorted(SCHEDULE), gaps) if g < need]
    problems = []
    for t, g in bad:
        problems.append('  %s 之后只隔 %.2fh，不足一个用量窗口（需 ≥ %.2fh）' % (t, g, need))
    if CATCHUP_MARGIN_MIN / 60.0 >= min(gaps):
        problems.append('  补跑余量 %d 分钟 ≥ 最小间隔 %.2fh，补跑会越进下一档的窗口'
                        % (CATCHUP_MARGIN_MIN, min(gaps)))
    if TASK_TIMEOUT_MIN / 60.0 >= min(gaps):
        problems.append('  单任务超时 %d 分钟 ≥ 最小间隔 %.2fh，超时任务会压到下一档'
                        % (TASK_TIMEOUT_MIN, min(gaps)))
    return problems

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
    tail = ((out or '')[-400:] + (err or '')[-400:]).strip()
    if rc == 0:
        log('✔ %s 完成，耗时 %.1f 分钟' % (task, dt / 60))
    else:
        log('✘ %s 失败 exit=%s，耗时 %.1f 分钟\n%s' % (task, rc, dt / 60, tail))
    return rc == 0, tail

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
            notify('⏭ newsdesk %s 今日跳过' % task,
                   '错过 %.1f 小时，已过补跑截止点。常见原因：机器睡眠或关机。' % late_h)
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
        ok, tail = run_task(task)
        attempts += 1
        if ok:
            state[task] = {'date': today, 'status': 'ok', 'attempts': attempts,
                           'finished': datetime.datetime.now().isoformat(timespec='seconds')}
        elif attempts > RETRIES:
            log('✘ %s 已重试 %d 次仍失败，今日放弃' % (task, RETRIES))
            quota = bool(QUOTA_PATTERNS.search(tail))
            if quota:
                # 单独标出来：这不是普通失败，是上面那套窗口假设失效的信号
                log('⚠ 失败信息命中配额特征——检查 USAGE_WINDOW_HOURS 与时间表是否仍然成立')
            notify('%s newsdesk %s 失败' % ('⚠️配额' if quota else '❌', task),
                   '重试 %d 次仍失败。\n%s\n%s' % (
                       RETRIES,
                       '命中配额特征，时间表的用量窗口假设可能已失效。' if quota else '',
                       tail[-300:]))
            state[task] = {'date': today, 'status': 'failed', 'attempts': attempts,
                           'quota': quota, 'last_attempt_ts': time.time()}
        else:
            log('… %s 第 %d 次失败，%d 分钟后重试' % (task, attempts, RETRY_GAP_MIN))
            state[task] = {'date': today, 'status': 'retrying', 'attempts': attempts,
                           'last_attempt_ts': time.time()}
        save_state(state)

def notify(title, text):
    """发告警。未配置 ALERT_WEBHOOK 时静默跳过，失败也不影响流水线。"""
    r = alert.send(title, text)
    if r is True:
        log('📣 已发出告警: %s' % title)
    elif r is False:
        log('!! 告警发送失败: %s' % title)


def daily_audit(state):
    """日终核对：最后一档的补跑截止点之后，把今天没成功的任务汇总报一次。

    为什么单独做一次而不是只靠逐条告警：漏跑（任务压根没触发，比如机器整天
    没开）不会产生任何一条失败记录，只有主动核对才看得见。
    每天只报一次，状态落盘防重复。
    """
    now = datetime.datetime.now()
    today = now.date().isoformat()
    last_time = max(t for t, _ in SCHEDULE)
    audit_at = (next_slot_after(last_time, now.date())
                - datetime.timedelta(minutes=CATCHUP_MARGIN_MIN))
    if audit_at.date() != now.date():       # 最后一档的下一档在次日，核对点提前到当天末尾
        audit_at = datetime.datetime.combine(now.date(), datetime.time(23, 50))
    if now < audit_at or state.get('_audit') == today:
        return
    bad = []
    for _, task in SCHEDULE:
        rec = state.get(task) or {}
        if rec.get('date') != today:
            bad.append('%s：今日未触发' % task)
        elif rec.get('status') != 'ok':
            bad.append('%s：%s' % (task, rec.get('status')))
    state['_audit'] = today
    save_state(state)
    if bad:
        log('⚠ 日终核对：%d/%d 个任务未成功' % (len(bad), len(SCHEDULE)))
        notify('⚠️ newsdesk 日终核对 %s' % today,
               '%d/%d 个任务未成功：\n%s' % (len(bad), len(SCHEDULE), '\n'.join(bad)))
    else:
        log('✔ 日终核对：%d 个任务全部成功' % len(SCHEDULE))


def heartbeat(state):
    """把调度器存活与各任务最近成功时刻写进 state/health.json。

    本机的调度器报不了自己的死——机器关了、进程挂了，它没机会发任何东西。
    这个文件由 build_site.py 复制进 site/ 一起发布，外部监控（uptime-kuma
    之类）轮询它、看时间戳是否变陈旧，才是唯一能覆盖「整机不在」的办法。
    """
    _now = datetime.datetime.now().astimezone()
    data = {'heartbeat': _now.isoformat(timespec='seconds'),   # 带时区，见 build_site.write_status
            'heartbeat_epoch': int(_now.timestamp()),
            'pid': os.getpid(),
            'schedule': ['%s %s' % (t, k) for t, k in SCHEDULE],
            'tasks': {task: {'date': (state.get(task) or {}).get('date'),
                             'status': (state.get(task) or {}).get('status'),
                             'finished': (state.get(task) or {}).get('finished')}
                      for _, task in SCHEDULE}}
    path = os.path.join(ROOT, 'state', 'health.json')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


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
        ok, _ = run_task(a.once, a.engine)
        sys.exit(0 if ok else 1)

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
        print('告警: %s' % ('已配置 ALERT_WEBHOOK'
                           if alert._conf('ALERT_WEBHOOK')
                           else '未配置——任务失败或整条停摆不会有任何通知'))
        hp = os.path.join(ROOT, 'state', 'health.json')
        if os.path.exists(hp):
            print('心跳: %s' % json.load(open(hp)).get('heartbeat'))
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

    # 时间表必须仍然满足用量窗口假设，不满足就别带着一份看着正确、实际失效的
    # 时间表跑下去——那是最难发现的故障。
    problems = validate_schedule()
    if problems:
        for x in problems:
            log('!! 时间表校验未通过：%s' % x.strip())
        sys.exit('时间表与 USAGE_WINDOW_HOURS=%s 的假设冲突，拒绝启动。\n'
                 '改时间表就一起想清楚窗口，或显式调整 USAGE_WINDOW_HOURS。'
                 % USAGE_WINDOW_HOURS)

    log('调度器启动 pid=%d  引擎=%s  定时表: %s' % (
        os.getpid(), ENGINE, ' · '.join('%s→%s' % (t, k) for t, k in SCHEDULE)))
    log('补跑截止=下一档前 %d 分钟 · 重试 %d 次/间隔 %d 分钟 · 轮询 %ds' % (
        CATCHUP_MARGIN_MIN, RETRIES, RETRY_GAP_MIN, TICK))
    log('用量窗口 %.1fh，最小间隔 %.2fh · 告警 %s' % (
        USAGE_WINDOW_HOURS, min(schedule_gaps()),
        '已配置' if alert._conf('ALERT_WEBHOOK') else '未配置（失败不会有人知道）'))
    consecutive_errors = 0
    try:
        while not _stop:
            try:
                st = load_state()
                tick(st)
                daily_audit(st)
                heartbeat(st)
                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                log('!! 调度循环异常（已忽略，继续运行）: %r' % e)
                # 循环异常原本只写进日志。连着坏下去时，调度器还活着、pid 还在，
                # 但一个任务也不会跑——从外面完全看不出来，所以这里必须叫一声。
                if consecutive_errors in (3, 30):
                    notify('❌ newsdesk 调度循环连续异常 %d 次' % consecutive_errors,
                           '调度器进程还在，但任务已经不触发了。\n%r' % e)
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
