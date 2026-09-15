# newsdesk

每日简报流水线。四个任务各自定时触发，采集脚本抓数据落盘，模型读本地文件写报告，再合成静态页。

## 改动前必须知道的

**不依赖 `gh` CLI。** GitHub API 直接走 HTTPS（`bin/common.py` 的 `gh()`），令牌
从 `bin/config.conf` 的 `GITHUB_TOKEN` 取，留空时才尝试借用本机 `gh` 的令牌。
不要退回 `subprocess.run(['gh', ...])`——单次 trending 采集约 190 次调用，
那样就是 190 次进程创建。

**所有联网都在采集层。** 分析层（模型）跑在沙箱里，读不到任何明文凭据，也出不了网。新增数据源时先确认能用 `curl` 拿到，不要依赖模型的联网工具 —— codex 走的自定义供应商根本没有 web search，实测加了 `tools.web_search=true` 之后事件流里该事件为 0，模型会转而用 shell 去本地翻文件硬凑答案。

**不能给引擎套两层沙箱。** claude 和 codex 的沙箱都是 macOS seatbelt，嵌套会让 Bash 整个失效（`sandbox_apply: Operation not permitted`）。危险之处是模型会靠 Read/Write 工具绕过去把任务做完，日志上看不出异常。`bin/sandbox-selftest.sh` 里「能执行命令」那一项就是为这个失效模式设的——套娃时它会失败。

**沙箱是 fail-closed 的。** `agent-settings.template.json` 里 `failIfUnavailable: true`
表示沙箱起不来就整个任务不跑，不静默降级成无防护；`allowUnsandboxedCommands: false`
让 `dangerouslyDisableSandbox` 参数失效。改这两项等于把防护关掉。

**钥匙串不在拒绝名单里，这是有意的。** 它是加密的、访问由系统按条目仲裁，而且两个
引擎的登录态都存在里面——拒掉会直接把认证掐断（实测 claude 报 `Not logged in`）。
拒的是明文躺在磁盘、没有第二道门禁的凭据目录。对钥匙串的约束由 `Bash(security:*)`
拒绝规则承担。

**配置只有 `bin/config.conf` 一个文件。** 引擎、模型、备份桶都在里面，`run_task.sh` 和 `scheduler.py` 读同一份。别再往脚本里塞常量。

**不写绝对路径。** 脚本自行推导仓库根目录，`$HOME` 从环境取。沙箱配置用 seatbelt 参数或 `{{HOME}}`/`{{ROOT}}` 模板注入。

## 方法论上的坑（写进 prompt 的，别丢）

- **GitHub 页面的 `stars today` 是滞后值**，约为 24 小时前的速率。只能用于排序和判断进出榜，绝不能当增量引用。加速中的项目被它低估，减速中的被高估。新进项目既往 24/24 例均被低估，所以只能判定「真实速率不低于页面值」，不给点估计。
- **`pushed_at` 会掩盖真实停更**。fork 推送、tag、CI 都会刷新它。判断活跃度必须读默认分支的 HEAD 提交时间，两者差值大的要单列。
- **release 下载量作为使用度代理有边界**。走 Docker / PyPI / HuggingFace 分发的项目下载数为 0 不代表无人使用。
- **豆瓣「评价数」是 `rating.count`，不是 `comment_count`**。后者是短评数，只有前者的三到六成，量级接近极易抄错。
- **摸摸鱼的掘金源经常滞后 24 小时以上**，报告结尾必须带各源时间戳表并标出过期的。
- **只报增量**。聚合站常把几天前的事重新推送，落笔前逐条核实首发日期，早于今天的移入背景并注明。

## 时间点

**相邻两档间隔 5 小时 5 分是有意的**：Claude 订阅的用量限额按 5 小时滚动窗口重置，这个间隔让每个任务落进全新窗口、互不抢额度，多出的 5 分钟防止卡在边界上。别把它「整理」成 6 小时整。

**补跑截止点必须小于两档间隔**，否则一个睡过头的任务会补跑到下一档已经开始之后，两个任务挤进同一个用量窗口——正是这套间隔要避免的事。所以 `scheduler.py` 里不是写死小时数，而是算「下一档开始前 `CATCHUP_MARGIN_MIN` 分钟」，跟着 `SCHEDULE` 自动变。

`16:40`（Trending）和 `21:45`（摸摸鱼）是按历史产出时刻定的，**不要轻易改**。16:40 单次触发让 Trending 窗口天然对齐 24 小时 —— 基线是昨天同一时刻的快照。

## Atom feed

`site/feed.xml` 由 `build_site.py` 生成，一篇报告一条、全文、保留最近 20 条。

正文用 **CDATA** 而不是 XML 转义——转义会把每个 `<` 变成 `&lt;`，体积近乎翻倍（实测 387 KB → 298 KB）。CDATA 内部只有 `]]>` 需要处理，代码里已拆成两段。

条目 `id` 是页面锚点 URL，**改了 id 阅读器会把旧内容当新的重推一遍**，动它之前想清楚。

Worker 那层按扩展名给 content-type，别退回无条件的 `text/html`——feed 打错类型阅读器不认。

## 数据的去向

`data/`、`reports/`、`site/` 都不进版本控制（仓库只放代码）。每天同步到 OCI 对象存储：私有桶存全量备份，公开桶只放 `site/`（`ObjectReadWithoutList`，可读不可列表），前面挂 Cloudflare Worker 对外提供域名（见 `deploy/`）。桶名、域名等环境特有值都在 `bin/config.conf` 与 `deploy/wrangler.toml`，这两个文件不进版本控制，仓库里只有 `*.example.*` 模板。**公开桶里有豆瓣简介、各平台热榜标题等第三方文本，改动发布范围前先想清楚。**`data/trending/<日期>/snap_t3.json` 是次日增量的基线，别删最新一份 —— `bin/prune.py` 对此有保护。