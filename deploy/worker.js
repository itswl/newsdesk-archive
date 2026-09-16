// 自有子域 → OCI 对象存储 的反向代理
//
// 存在的理由有两个：
//  1. 对象存储没有「默认文档」概念，访问 / 不会返回 index.html，必须在这里补。
//  2. 桶在 us-phoenix-1，亚洲直连慢；走 Cloudflare 边缘缓存后只有回源那次慢。
//
// 桶是 ObjectReadWithoutList：能按 URL 取对象，列不出清单。Worker 也不转发
// 列表请求，保持这个边界。

// 桶的坐标由 wrangler.toml 的 [vars] 注入，脚本里不写死具体环境
function origin(env) {
  return `https://objectstorage.${env.OCI_REGION}.oraclecloud.com`
       + `/n/${env.OCI_NAMESPACE}/b/${env.OCI_BUCKET}/o`;
}

// 站点每天更新四次，边缘缓存 5 分钟够用；index.html 更短一些，免得刚跑完还看到旧的
// status.json 是给外部监控轮询的，缓存必须短——缓存久了「陈旧」这件事本身
// 就被 CDN 藏起来了，监控看到的永远是 5 分钟前的健康状态
const TTL = { 'index.html': 120, 'feed.xml': 120, 'status.json': 30, 'archive.html': 300, _default: 300 };

export default {
  async fetch(request, env) {
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      return new Response('Method Not Allowed', { status: 405, headers: { Allow: 'GET, HEAD' } });
    }
    const url = new URL(request.url);
    let path = url.pathname;

    if (path === '/' || path === '') path = '/index.html';
    if (path.endsWith('/')) path += 'index.html';
    // 只允许取站点自身产出的文件，不给遍历到桶里别的前缀
    // 允许一层语言子目录（/en/2026-09-16.html）。只放行单层，不给目录遍历留口子。
    if (!/^\/(?:[A-Za-z]{2}(?:-[A-Za-z]{2,4})?\/)?[A-Za-z0-9._-]+\.(html|xml|json)$/.test(path)) {
      return new Response('Not Found', { status: 404 });
    }

    const name = path.slice(1);
    const ttl  = TTL[name.split('/').pop()] ?? TTL._default;
    const upstream = origin(env) + '/' + encodeURIComponent(name);

    const res = await fetch(upstream, {
      method: request.method,
      cf: { cacheTtl: ttl, cacheEverything: true },
    });

    if (!res.ok) {
      return new Response(res.status === 404 ? 'Not Found' : 'Upstream Error',
                          { status: res.status === 404 ? 404 : 502 });
    }

    const h = new Headers(res.headers);
    // 按扩展名给类型——之前无条件打 text/html，feed 发出去阅读器不认
    h.set('content-type',
      name.endsWith('.xml')  ? 'application/atom+xml; charset=utf-8' :
      name.endsWith('.json') ? 'application/json; charset=utf-8' :
                               'text/html; charset=utf-8');
    // 客户端缓存不能比边缘长，否则给 status.json 设的短 TTL 被浏览器/监控端
    // 的 max-age 抵消掉——监控看到的永远是一分钟前的健康状态。
    h.set('cache-control', `public, max-age=${Math.min(60, ttl)}, s-maxage=${ttl}`);
    h.set('x-content-type-options', 'nosniff');
    h.set('referrer-policy', 'no-referrer');
    h.delete('x-amz-version-id');          // 别把对象存储的内部标识透出去
    h.delete('opc-request-id');
    h.delete('opc-client-request-id');
    return new Response(res.body, { status: res.status, headers: h });
  },

  // ── 定时自查 ──────────────────────────────────────────────────────────
  // 由 wrangler.toml 的 [triggers] 驱动。这条是整套告警里唯一**不在那台
  // Mac 上**的环节，也是唯一能发现「机器关了三天」的环节：
  //   · bin/alert.py 覆盖「跑了但失败了」——但它要调度器活着才发得出来
  //   · 日终核对同理，进程没了就不会核对
  //   · status.json 让状态**可以**被外部监控，但可以被监控 ≠ 正在被监控
  // Worker 本来就站在站点前面，天然在机器之外，由它来轮询这条线才闭合。
  async scheduled(event, env, ctx) {
    ctx.waitUntil(selfCheck(env));
  },
};

// 站点多久没更新算出事。四个任务里相邻两档最长间隔 8.75 小时（21:45 → 次日
// 06:30），机器夜里合盖、次日中午才补跑也就 ~19 小时，所以 26 小时不会误报，
// 又能在「整整一天没动静」时叫出来。
const STALE_HOURS_DEFAULT = 26;

export async function evaluate(text, nowMs, thresholdHours) {
  let doc;
  try {
    doc = JSON.parse(text);
  } catch {
    return { bad: true, title: '❌ newsdesk 状态文件读不出来',
             body: `status.json 不是合法 JSON，站点可能只发布了一半。\n收到：${text.slice(0, 200)}` };
  }
  // epoch 优先：它没有时区歧义。退回 ISO 时依赖字段自带偏移量——不带偏移量的
  // ISO 会被 Worker（跑在 UTC）当成 UTC，北京时间算出来差整整 8 小时。
  const ts = doc.generated_epoch ? doc.generated_epoch * 1000 : Date.parse(doc.generated);
  if (!ts || Number.isNaN(ts)) {
    return { bad: true, title: '❌ newsdesk 状态文件没有可用时间戳',
             body: 'generated_epoch 与 generated 都取不到，无法判断新旧。' };
  }
  const age = (nowMs - ts) / 3.6e6;
  if (age <= thresholdHours) return { bad: false, age };

  const tasks = Object.entries(doc.tasks || {})
    .map(([k, v]) => `  ${k}: ${v.status}${v.finished ? ' ' + v.finished : ''}`).join('\n');
  return {
    bad: true, age,
    title: `❌ newsdesk 已停更 ${age.toFixed(1)} 小时`,
    body: `站点最后一次更新是 ${doc.generated}（${doc.date}），已超过 ${thresholdHours} 小时没有新产出。\n`
        + `常见原因：那台机器关机或长时间睡眠、调度器进程没了。\n\n最后一次已知的任务状态：\n${tasks}`,
  };
}

async function selfCheck(env) {
  const hook = env.ALERT_WEBHOOK;
  if (!hook) return;                       // 没配就安静跳过，和 bin/alert.py 一致

  const threshold = Number(env.STALE_ALERT_HOURS || STALE_HOURS_DEFAULT);
  let verdict;
  try {
    // cacheTtl: 0 —— 必须绕开自己的边缘缓存，否则读到的是刚才那份，
    // 「陈旧」这件事正好被自己藏住了
    const res = await fetch(origin(env) + '/status.json', { cf: { cacheTtl: 0 } });
    verdict = res.ok
      ? await evaluate(await res.text(), Date.now(), threshold)
      : { bad: true, title: `❌ newsdesk 取不到状态文件 (HTTP ${res.status})`,
          body: '对象存储没有返回 status.json。桶被改动、被删、或权限变了。' };
  } catch (e) {
    verdict = { bad: true, title: '❌ newsdesk 自查请求失败',
                body: String(e && e.message || e) };
  }
  // 成功也记一行：开了 observability 之后，这是唯一能在后台确认「cron 确实在跑」
  // 的凭据。只在出事时才有日志的话，「一直没日志」和「一切正常」长得一模一样。
  console.log(JSON.stringify({
    check: 'newsdesk', stale: verdict.bad, threshold,
    age_hours: verdict.age !== undefined ? Number(verdict.age.toFixed(2)) : null,
  }));
  if (verdict.bad) await notify(env, verdict.title, verdict.body);
}

// 日志会被 Cloudflare 收走存 3 天。ALERT_WEBHOOK 是能往群里发消息的凭据，
// 异常信息里一旦带上它就等于把凭据写进了日志库，所以统一抹掉再打印。
function redact(text, env) {
  const hook = env.ALERT_WEBHOOK;
  return hook ? String(text).split(hook).join('<ALERT_WEBHOOK>') : String(text);
}

async function notify(env, title, text) {
  const body = `${title}\n${text}`;
  const tpl = env.ALERT_PAYLOAD;
  // {{TEXT}} 落在 JSON 字符串内部，必须按 JSON 规则转义——正文里一个换行或
  // 引号就能把 payload 撑坏。和 bin/alert.py 同一个约定。
  const payload = tpl
    ? tpl.replace('{{TEXT}}', JSON.stringify(body).slice(1, -1))
         .replace('{{TITLE}}', JSON.stringify(title).slice(1, -1))
    : JSON.stringify({ title, text: body });
  try {
    const r = await fetch(env.ALERT_WEBHOOK, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: payload,
    });
    // 光看状态码不够：飞书/Lark、钉钉、企业微信都是错误也返回 200，失败写在
    // body 的 code/errcode 里。只看状态码的话通道断了也一直以为发出去了。
    const txt = await r.text();
    if (!r.ok) return console.log('告警 HTTP 失败', r.status, redact(txt.slice(0, 200), env));
    try {
      const d = JSON.parse(txt);
      const code = d.code ?? d.errcode ?? d.StatusCode;
      if (code !== undefined && Number(code) !== 0) console.log('告警被服务端拒绝', redact(txt.slice(0, 200), env));
    } catch { /* 非 JSON（Slack 返回纯文本 ok）按成功处理 */ }
  } catch (e) {
    console.log('告警发送异常', redact(e && e.message || e, env));   // 自查失败不能反过来影响站点
  }
}
