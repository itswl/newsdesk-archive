# 部署到 Cloudflare（可选）

> **只在本机看简报的话，整个目录可以忽略。** 静态站生成在 `site/` 下，双击
> `site/index.html` 就能看，页面自包含、不依赖任何外部资源，也不需要起服务器。
>
> 这一套只解决一个问题：**让本机之外的人也能访问**。

Worker 把一个自有子域反代到 OCI 对象存储，解决两件事：对象存储没有默认文档
（访问 `/` 不返回 index.html），以及桶在单一区域、跨洲直连慢。

域名、namespace、桶名都在 `wrangler.toml` 里配（从 `wrangler.example.toml`
复制，真实文件不进版本控制）。

## 部署

需要一个自定义 API Token，权限：

```
Account │ Workers Scripts │ Edit    ← 上传脚本
Zone    │ Workers Routes  │ Edit    ← 绑目标子域
```

（诊断阶段还用到 `Zone WAF:Read` 和 `Zone Settings:Read`，日常部署不需要。）

```bash
export CLOUDFLARE_API_TOKEN=...
export CLOUDFLARE_ACCOUNT_ID=...
npx wrangler deploy --config deploy/wrangler.toml
```

**不要把令牌写进任何文件。** 只走环境变量，wrangler 也不会落盘。

## 这个子域原本是坏的（记录一下排查结论）

接手时该子域返回 308 重定向到它自己，无限循环。

排查发现**不是重定向规则** —— 该 zone 没有任何自定义 Ruleset，只有 Cloudflare
内置的三条。真正原因是两个配置叠加：

1. 该 zone 有一条泛解析 `*.example.com` 指向某台源站，目标子域没有独立 DNS
   记录，走的就是这条泛解析
2. zone 的 SSL 模式是 **flexible** —— Cloudflare 用 HTTP 回源，而那台源站
   把 HTTP 跳 HTTPS，于是 浏览器→CF(https)→源站(http)→跳 https→… 成环

**没有改 SSL 模式**：它是 zone 级设置，同 zone 下其它子域也在用，改成 Full
可能把它们弄坏。Worker 路由在回源之前就接管请求，这个子域
根本不走源站，所以不需要动它。同理也没有给目标子域加 DNS 记录——Worker 路由
对已代理的泛解析子域直接生效。

部署后前几十秒会出现 200 与 308 混杂，那是边缘节点路由同步的过渡态，会自行一致。

## Worker 做了什么

- `/` 与 `/xxx/` 补成 `index.html`（对象存储没有默认文档概念）
- 路径白名单只放行 `.html` 与 `.xml`，不给遍历到桶里别的前缀
- 按扩展名给 content-type：`.xml` 是 `application/atom+xml`，其余 `text/html`。
  原先无条件打 `text/html`，feed 发出去阅读器不认
- 剥掉 `opc-request-id`、`x-amz-*` 等对象存储内部标识
- 边缘缓存：index 120 秒、其余 300 秒（站点一天更新四次）

改缓存时长直接改 `worker.js` 顶部的 `TTL`，改完重新 deploy。

站点内容由 `bin/backup_oci.sh` 推到对象存储，Worker 只读不写。

## 定时自查怎么验

`scheduled` 每 6 小时（UTC）拉一次 `status.json`，超过 `STALE_ALERT_HOURS` 没有新
产出就推告警。**等它自然触发是验证不了的**：站点新鲜时它会正确地保持沉默，你看到的
「没收到消息」和「整条链路是坏的」长得一模一样。

要真验，强制一次陈旧。`--remote` 让它跑在边缘、能读到真的 `ALERT_WEBHOOK` secret，
`--var` 只对这次会话生效、不用部署：

```bash
export CLOUDFLARE_API_TOKEN=<令牌>
npx wrangler dev --remote --test-scheduled --var STALE_ALERT_HOURS:0 &
sleep 20 && curl "http://localhost:8787/__scheduled"    # 会真的往群里发一条
kill %1
```

看它被拒绝的话（飞书/Lark 错误也返回 200，失败写在 body 的 `code` 里），
`npx wrangler tail` 能看到 Worker 打的日志。

改过 `evaluate()` 之后跑 `TZ=UTC node deploy/worker.test.mjs`。**必须带 `TZ=UTC`**：
Worker 就跑在 UTC，而在本机时区下，时区相关的那几条恰好会「通过」，测不出问题。
`bin/test.sh` 已经带上了这个变量。
