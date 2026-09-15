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
const TTL = { 'index.html': 120, 'feed.xml': 120, 'archive.html': 300, _default: 300 };

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
    if (!/^\/[A-Za-z0-9._-]+\.(html|xml)$/.test(path)) {
      return new Response('Not Found', { status: 404 });
    }

    const name = path.slice(1);
    const ttl  = TTL[name] ?? TTL._default;
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
    h.set('content-type', name.endsWith('.xml')
      ? 'application/atom+xml; charset=utf-8'
      : 'text/html; charset=utf-8');
    h.set('cache-control', `public, max-age=60, s-maxage=${ttl}`);
    h.set('x-content-type-options', 'nosniff');
    h.set('referrer-policy', 'no-referrer');
    h.delete('x-amz-version-id');          // 别把对象存储的内部标识透出去
    h.delete('opc-request-id');
    h.delete('opc-client-request-id');
    return new Response(res.body, { status: res.status, headers: h });
  },
};
