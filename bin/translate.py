#!/usr/bin/env python3
"""把当天的中文报告翻成英文，产出 reports/<日期>/<名字>.en.md。

只做英文。繁中不经过这里——它在 build_site 渲染时由 OpenCC 现转（24 KB 报告
0.02 秒），存一份反而多一处会过期的副本。

**直接调 API，不走 agentic 循环**：翻译是纯转换，不需要工具、不需要沙箱，
一次 messages 调用比让模型带着工具跑一圈便宜也可控得多。

用 config.conf 里 ENGINE_FALLBACK=api 那套 FALLBACK_* 配置——按量付费，
不吃订阅的 5 小时用量窗口。翻译每天要跑，不该跟分析抢窗口。

    translate.py [日期]        缺省今天
    translate.py --all         补翻所有还没有英文版的日子
    translate.py --force       已有英文版也重翻
"""
import json, os, re, sys, time, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, 'reports')
MAX_TOKENS = 16000
TIMEOUT = 300
# 超过这个长度就按二级标题切块翻。实测 23k 字符的 trending 报告一次翻会撞上
# 输出上限被截断——而截断是静默的，文件看着正常只是后半截没了。
CHUNK_CHARS = 5000
RETRIES = 2

SYSTEM = (
    "You translate Chinese tech-briefing reports into English.\n"
    "Rules:\n"
    "1. Output ONLY the translated Markdown. No preamble, no explanation, no code fence around the whole thing.\n"
    "2. Preserve the Markdown structure exactly: heading levels, table rows and column counts, "
    "list markers, bold/italic, blockquotes, horizontal rules.\n"
    "3. Do NOT translate: URLs, repository names (owner/repo), package names, code inside backticks, "
    "product names, people's names, model identifiers.\n"
    "4. Keep all numbers, dates and units exactly as they are.\n"
    "5. Translate table headers and cell text, but keep the alignment row (|---|:-:|) unchanged.\n"
    "6. Chinese platform names: keep a recognisable English form with the original in parentheses on "
    "first use, e.g. Douban (豆瓣), Zhihu (知乎), Hupu (虎扑).\n"
    "7. Write natural, concise English. This is a professional briefing, not a literal gloss."
)


def conf(key, default=''):
    try:
        for line in open(os.path.join(ROOT, 'bin', 'config.conf'), encoding='utf-8'):
            line = line.split('#', 1)[0].strip()
            if line.startswith(key + '='):
                return line.split('=', 1)[1].strip().strip('\'"')
    except OSError:
        pass
    return default


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止跟随重定向。

    urllib 跟随跨主机 302 时会把**所有**请求头原样带上——实测 x-api-key 与
    Authorization 都会被转发给新主机。也就是说端点一旦被改成或被劫持成重定向，
    令牌就跟着送出去了。头名在这件事上没区别，唯一可靠的做法是不跟随。

    正常的 API 端点不会重定向，所以这里遇到 3xx 直接当错误报出来。
    """
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code,
            'API 端点返回了重定向（-> %s）。为避免令牌被转发给其他主机，'
            '这里不跟随重定向。请检查 FALLBACK_BASE_URL。' % newurl, headers, fp)


_opener = urllib.request.build_opener(_NoRedirect)


def call(text):
    base, tok, model = (conf('FALLBACK_BASE_URL'), conf('FALLBACK_AUTH_TOKEN'),
                        conf('FALLBACK_MODEL'))
    if not (base and tok and model):
        sys.exit('!! 翻译需要 config.conf 里的 FALLBACK_BASE_URL / FALLBACK_AUTH_TOKEN / '
                 'FALLBACK_MODEL（与 ENGINE_FALLBACK=api 共用同一套）')
    # 明文 HTTP 会让令牌在链路上裸奔。这条不给开关——想用 http 就得先想清楚为什么。
    if not base.lower().startswith('https://'):
        sys.exit('!! FALLBACK_BASE_URL 必须是 https://，当前是 %s。'
                 '明文 HTTP 会把 API 令牌暴露在链路上。' % base.split('://')[0])
    req = urllib.request.Request(
        base.rstrip('/') + '/v1/messages',
        data=json.dumps({'model': model, 'max_tokens': MAX_TOKENS, 'system': SYSTEM,
                         'messages': [{'role': 'user', 'content': text}]}).encode(),
        # x-api-key 是 Anthropic 接口的标准头。它与 Authorization: Bearer 在 TLS 下
        # 等价，重定向转发的行为也一样（都会转发），所以防护靠上面的 _NoRedirect，
        # 不靠换头名。
        headers={'content-type': 'application/json', 'x-api-key': tok,
                 'anthropic-version': '2023-06-01'})
    with _opener.open(req, timeout=TIMEOUT) as r:
        d = json.load(r)
    out = ''.join(c.get('text', '') for c in d.get('content', []))
    # 截断是静默失败里最难发现的一种：文件看着正常，只是后半截没了
    if d.get('stop_reason') == 'max_tokens':
        raise RuntimeError('输出被 max_tokens=%d 截断，译文不完整' % MAX_TOKENS)
    if not out.strip():
        raise RuntimeError('返回空内容: %s' % json.dumps(d)[:200])
    return out, d.get('usage', {})


def chunks(md):
    """按二级标题切块。切点在 `## ` 之前，所以拼回去是无损的（直接 ''.join）。

    单次翻整篇会撞输出上限：实测 23k 字符的 trending 报告被 max_tokens 截断。
    切块之后每块都远低于上限，而且某一块失败只影响那一段，不是整篇作废。
    """
    if len(md) <= CHUNK_CHARS:
        return [md]
    # 三级切分，逐级变细。单个二级小节本身就超限时必须继续往下切——
    # 只按 ## 切的话那一节会原样送出去，照样被截断（实测 9.4k 字符的一节就会）。
    for pattern in (r'(?m)^(?=## )', r'(?m)^(?=### )', r'\n\n(?=\S)'):
        parts = re.split(pattern, md)
        if len(parts) < 2:
            continue
        # 段落切分会吃掉分隔符，补回去才能无损拼接
        if pattern.startswith('\\n'):
            parts = [p if i == 0 else '\n\n' + p for i, p in enumerate(parts)]
        out, buf = [], ''
        for part in parts:
            if buf and len(buf) + len(part) > CHUNK_CHARS:
                out.append(buf); buf = part
            else:
                buf += part
        if buf:
            out.append(buf)
        if all(len(c) <= CHUNK_CHARS for c in out):
            return out
        md_parts = out                        # 这一级还有超长块，换更细的切法
    return md_parts if 'md_parts' in dir() else [md]


def shape(md):
    """结构指纹。翻译把表格列数或标题层级改了，页面会当场崩——但不会报错。"""
    return {
        'tables': len(re.findall(r'^\|', md, re.M)),
        'links': len(re.findall(r'\]\(https?://', md)),
        'headings': len(re.findall(r'^#{1,6} ', md, re.M)),
        'fences': md.count('```'),
    }


def translate_file(src, force=False):
    dst = src[:-3] + '.en.md'
    if not force and os.path.exists(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
        return 'skip', 0, ''
    text = open(src, encoding='utf-8').read()
    t0 = time.time()
    pieces = chunks(text)
    done = []
    for i, piece in enumerate(pieces, 1):
        last = None
        for attempt in range(1, RETRIES + 1):
            try:
                seg, _usage = call(piece)
                done.append(seg)
                break
            except Exception as e:           # noqa: BLE001
                last = e
                if attempt < RETRIES:
                    time.sleep(3 * attempt)  # 空返回、瞬时 5xx 多半重试一次就好
        else:
            raise RuntimeError('第 %d/%d 块翻译失败: %s' % (i, len(pieces), last))
    out = '\n\n'.join(x.strip('\n') for x in done) + '\n'
    a, b = shape(text), shape(out)
    warn = ''
    # 链接与代码围栏必须一字不差；表格与标题允许 ±1（译文偶尔会合并一个空行）
    for k in ('links', 'fences'):
        if a[k] != b[k]:
            warn += ' %s %d→%d' % (k, a[k], b[k])
    for k in ('tables', 'headings'):
        if abs(a[k] - b[k]) > 1:
            warn += ' %s %d→%d' % (k, a[k], b[k])
    tmp = dst + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(out if out.endswith('\n') else out + '\n')
    os.replace(tmp, dst)
    return 'ok', time.time() - t0, warn


def days_with_reports():
    if not os.path.isdir(REPORTS):
        return []
    return sorted(d for d in os.listdir(REPORTS) if re.fullmatch(r'\d{4}-\d{2}-\d{2}', d))


def main():
    args = sys.argv[1:]
    force = '--force' in args
    do_all = '--all' in args
    dates = [a for a in args if re.fullmatch(r'\d{4}-\d{2}-\d{2}', a)]
    if do_all:
        dates = days_with_reports()
    elif not dates:
        dates = [time.strftime('%Y-%m-%d')]

    total_fail = 0
    for date in dates:
        d = os.path.join(REPORTS, date)
        if not os.path.isdir(d):
            continue
        srcs = [os.path.join(d, f) for f in sorted(os.listdir(d))
                if f.endswith('.md') and not f.endswith('.en.md')]
        for src in srcs:
            name = os.path.relpath(src, ROOT)
            try:
                st, dt, warn = translate_file(src, force)
            except Exception as e:                      # noqa: BLE001
                total_fail += 1
                print('  ✗ %s  %s' % (name, str(e)[:120]))
                continue
            if st == 'skip':
                print('  · %s  已是最新，跳过' % name)
            else:
                print('  ✓ %s  %.0fs%s' % (name, dt, ('  ⚠ 结构对不上:' + warn) if warn else ''))
    # 翻译失败不该拖垮整条流水线：中文站照常发布，英文那份缺了而已
    return 1 if total_fail else 0


if __name__ == '__main__':
    sys.exit(main())
