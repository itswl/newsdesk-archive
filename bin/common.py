import os, re, json, glob, datetime, subprocess, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')

def today():
    return datetime.date.today()

def iso(d=None):
    return (d or today()).isoformat()

def p(*parts):
    return os.path.join(ROOT, *parts)

def data_dir(source, d=None, create=False):
    """data/<源>/<YYYY-MM-DD>/ —— 每源每天一个目录。"""
    path = p('data', source, iso(d))
    if create:
        os.makedirs(path, exist_ok=True)
    return path

def report_dir(d=None, create=False):
    """reports/<YYYY-MM-DD>/ —— 每天一个目录，文件名只留任务名。"""
    path = p('reports', iso(d))
    if create:
        os.makedirs(path, exist_ok=True)
    return path

def now_local():
    return datetime.datetime.now().astimezone()

def conf(key, default=''):
    """读 bin/config.conf 的一项。环境变量优先，便于临时覆盖。"""
    if os.environ.get(key):
        return os.environ[key]
    try:
        for line in open(p('bin', 'config.conf')):
            line = line.split('#', 1)[0].strip()
            if line.startswith(key + '='):
                return line.split('=', 1)[1].strip().strip('\'"')
    except OSError:
        pass
    return default


_TOKEN = None

def gh_token():
    """GitHub 令牌解析顺序：config.conf / 环境变量 → gh CLI（若恰好装了）→ 无。
    不强制依赖 gh：装了就借它的令牌，没装就用自己配的。"""
    global _TOKEN
    if _TOKEN is not None:
        return _TOKEN
    t = conf('GITHUB_TOKEN')
    if not t:
        r = subprocess.run(['gh', 'auth', 'token'], capture_output=True, text=True)
        if r.returncode == 0:
            t = r.stdout.strip()
    _TOKEN = t or ''
    return _TOKEN


def gh(path, with_headers=False):
    """GitHub API 调用，直接走 HTTPS，不依赖 gh CLI。

    原先每次调用 fork 一个 gh 进程，单次 trending 采集约 190 次，开销都在进程创建上。
    未认证时 GitHub 只给 60 次/小时，远不够用——所以令牌基本是必需的，
    在 bin/config.conf 里配 GITHUB_TOKEN，或者装着 gh 让它借用。

    失败返回 None；with_headers=True 时返回 (数据, 响应头)。
    """
    url = path if path.startswith('http') else 'https://api.github.com/' + path.lstrip('/')
    req = urllib.request.Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'newsdesk',
        'X-GitHub-Api-Version': '2022-11-28',
        **({'Authorization': 'Bearer ' + gh_token()} if gh_token() else {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode('utf-8'))
            return (data, dict(r.headers)) if with_headers else data
    except (urllib.error.URLError, ValueError, TimeoutError):
        return (None, None) if with_headers else None


def gh_last_page(path):
    """用 Link 头的 rel="last" 反推总页数，只花一次请求。取不到返回 1。"""
    _, h = gh(path, with_headers=True)
    m = re.search(r'[?&]page=(\d+)>;\s*rel="last"', (h or {}).get('Link', '') or '')
    return int(m.group(1)) if m else 1

def curl(url, headers=None, timeout=40):
    cmd = ['curl', '-sL', '--max-time', str(timeout), url, '-H', 'User-Agent: ' + UA]
    for k, v in (headers or {}).items():
        cmd += ['-H', '%s: %s' % (k, v)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None

def latest_snapshot(before_date, tiers=('t3', 't1')):
    """找 before_date 之前最近一份基线快照，优先 t3（与 16:40 触发对齐），退回 t1。
    返回 (路径, 数据) 或 (None, None)。"""
    for t in tiers:
        best = None
        for f in glob.glob(p('data', 'trending', '*', 'snap_%s.json' % t)):
            d = os.path.basename(os.path.dirname(f))
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', d) or d >= before_date:
                continue
            if best is None or d > best[0]:
                best = (d, f)
        if best:
            return best[1], json.load(open(best[1]))
    return None, None

def baseline_ts(data):
    """快照里取基线时戳，t3 用 _ts_local_start（星标采集起点），t1 用 _ts_local。"""
    for k in ('_ts_local_start', '_ts_local'):
        if k in data:
            return datetime.datetime.fromisoformat(data[k])
    return None
