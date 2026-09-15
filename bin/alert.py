#!/usr/bin/env python3
"""告警发送。纯 stdlib，通用 webhook，不绑定任何具体服务。

为什么需要：这条流水线的失败闭环原本只写在报告和站点状态条里，
而那**只有已经打开页面的人**才看得见。整条停摆（机器没开、调度器挂了、
沙箱自检不过导致任务拒绝运行）不会有任何人知道——最安全的失败方式
同时也是最沉默的。

配置（bin/config.conf，留空则整个告警静默跳过）：
    ALERT_WEBHOOK=https://...
    ALERT_PAYLOAD={"msgtype":"text","text":{"content":"{{TEXT}}"}}   # 可选

两种形状自动区分：
  - URL 里含 {{TEXT}}  → GET，文本 urlencode 进 URL（Bark / ntfy 这类）
  - 否则               → POST JSON。默认体 {"title":..,"text":..}；
                         飞书/钉钉/Slack 体不同，用 ALERT_PAYLOAD 自定义。

⚠️ 告警失败绝不能反过来弄坏流水线，所以这里所有异常都吞掉，只在日志留一行。
"""
import json, os, sys, urllib.parse, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT = 10


def _conf(key, default=''):
    try:
        for line in open(os.path.join(ROOT, 'bin', 'config.conf'), encoding='utf-8'):
            line = line.split('#', 1)[0].strip()
            if line.startswith(key + '='):
                return line.split('=', 1)[1].strip().strip('\'"')
    except OSError:
        pass
    return default


def send(title, text):
    """返回 True=已发出 / False=发送失败 / None=未配置。"""
    url = _conf('ALERT_WEBHOOK')
    if not url:
        return None
    body = '%s\n%s' % (title, text) if title else text
    try:
        if '{{TEXT}}' in url:
            req = urllib.request.Request(
                url.replace('{{TEXT}}', urllib.parse.quote(body, safe=''))
                   .replace('{{TITLE}}', urllib.parse.quote(title, safe='')),
                method='GET')
        else:
            tpl = _conf('ALERT_PAYLOAD')
            if tpl:
                # 模板里 {{TEXT}} 出现在 JSON 字符串内部，必须按 JSON 规则转义，
                # 否则正文里一个换行或引号就能把 payload 撑坏。
                payload = (tpl.replace('{{TEXT}}', json.dumps(body)[1:-1])
                              .replace('{{TITLE}}', json.dumps(title)[1:-1])).encode()
            else:
                payload = json.dumps({'title': title, 'text': body},
                                     ensure_ascii=False).encode()
            req = urllib.request.Request(url, data=payload, method='POST',
                                         headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            if not 200 <= r.status < 300:
                return False
            return _body_ok(r.read(4096))
    except Exception as e:                      # noqa: BLE001 —— 见文件头
        print('!! 告警发送失败（不影响流水线）: %s' % e, file=sys.stderr)
        return False


def _body_ok(raw):
    """光看 HTTP 状态码不够。

    飞书/Lark、钉钉、企业微信都是**错误也返回 200**，把失败写在 body 的
    code/errcode 里——实测给 Lark 发一个形状不对的 payload，拿到的是
    `HTTP 200 + {"code":19002,"msg":"params error, msg_type need"}`。
    只看状态码的话，告警通道早就断了却一直报「已发送」，而告警静默失效
    正是这套东西存在的理由要防的那件事。

    非 JSON 的响应（Slack 返回纯文本 ok）一律按成功处理，不瞎猜。
    """
    try:
        d = json.loads(raw.decode('utf-8', 'replace'))
    except (ValueError, AttributeError):
        return True
    if not isinstance(d, dict):
        return True
    for key in ('code', 'errcode', 'StatusCode'):
        if key in d:
            try:
                if int(d[key]) != 0:
                    print('!! 告警被服务端拒绝: %s' % raw.decode('utf-8', 'replace')[:200],
                          file=sys.stderr)
                    return False
            except (TypeError, ValueError):
                pass
    return True


if __name__ == '__main__':
    t = sys.argv[1] if len(sys.argv) > 1 else 'newsdesk'
    m = sys.argv[2] if len(sys.argv) > 2 else sys.stdin.read()
    r = send(t, m)
    print({True: '已发送', False: '发送失败', None: '未配置 ALERT_WEBHOOK，跳过'}[r])
