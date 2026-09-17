# newsdesk（Python 版 · 已归档）

每日简报流水线。四路数据源各自定时采集，交给大模型分析成报告，再合成静态站。

> **这个仓库不再更新。** 继任者是 [itswl/newsdesk](https://github.com/itswl/newsdesk)
> （Go + TypeScript，功能相同）。归档点是 tag `python-reference`：140 个测试全过、
> 四路采集实跑正常、三语站点与 feed 线上可访问。
>
> 这里的东西仍然有用：`prompts/` 是四个任务的分析契约，`tests/` 是行为规格，
> `AGENTS.md` 记着每条约束背后的原因。换语言换到了什么、没换到什么，写在
> [最后一节](#为什么换语言以及换到了什么)。

不是「把网页丢给模型让它总结」——采集、跨期比对、核查口径都固化在脚本和 prompt 里，
模型只在确定的数据上做判断。采集与调度在本机跑，对外发布是可选的。

## 快速开始

```bash
cp bin/config.example.conf bin/config.conf     # 填引擎与模型，其余留空即可
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

bin/test.sh                  # 单测 + 沙箱边界实测。没通过说明分析层能读到本机凭据
bin/run_task.sh douban       # 手跑一个任务验证链路
bin/schedulerctl.sh start    # 起调度器
```

到这里就能用了：报告在 `reports/<日期>/`，静态站在 `site/`，双击 `index.html` 就能看，
页面自包含、不用起服务器。

依赖：`curl`、Python 3、至少一个引擎（`claude` 或 `codex`）。Python 包装在项目内
`.venv`（系统 Python 受 PEP 668 保护）。**`nh3` 装不上会直接拒绝生成站点**——报告正文
含第三方文本，不清洗就发布等于存储型 XSS。

**不需要 `gh`，也不需要 GitHub 令牌。** Trending 采集压到约 50 次调用，装得进未认证的
60 次/小时预算；停更判断走 `commits.atom`（不计 API 配额）。填 `GITHUB_TOKEN` 只是把
预算放宽到 5000，是增强不是前提。

## 四个任务

| 时间 | 任务 | 数据源 | 采集脚本 |
|---|---|---|---|
| 06:30 | AI 简报 | 15 个源：厂商公告 / 科技媒体 / 论文 / 社区 | `bin/fetch_ainews.py` |
| 11:35 | 豆瓣热门电影 | `search_subjects` + `rexxar` 详情 | `bin/fetch_douban.py` |
| 16:40 | GitHub Trending | trending 页 + GitHub API 快照 | `bin/fetch_trending.py` |
| 21:45 | 摸摸鱼热榜 | `momoyu.cc/api/hot/list` | `bin/fetch_momoyu.py` |

时间表在 `bin/scheduler.py` 顶部，两处不要随意改：

- **相邻两档间隔 5 小时 5 分**。订阅的用量限额按 5 小时滚动窗口重置，隔满一个窗口
  才不互抢额度，多出的 5 分钟防止卡边界。约束是**不小于**一个窗口——比 5 小时窄会
  共用窗口，比它宽是安全的。`validate_schedule()` 启动时核对，不自洽就拒绝启动。
- **Trending 那一档的时刻**。单次触发让增量窗口天然对齐 24 小时，基线是昨天同一时刻
  的快照。

## 怎么组织的

```
采集（纯脚本）     → data/<源>/<日期>/    原始数据落盘
跨期比对（纯脚本） → history.json         今天 vs 前 7 天的差异
分析（模型，沙箱） → reports/<日期>/      Markdown 报告
发布               → site/ → 对象存储 → CDN
```

**分层是为了让分析能脱离采集重跑**：改 prompt 可以拿历史数据回测，重试也不再消耗接口
配额。采集成功打标记，同日重试跳过采集（`FORCE_FETCH=1` 强制重采）。

**所有联网都在采集层，模型只读本地文件。** 这是双引擎可互换的前提——codex 走自定义
供应商时没有 web search，拿不到联网能力它会用 shell 去本地翻文件硬凑答案。

**跨期比对也在采集层算。** 只看当天会把「连续烧了四天」和「今天刚冒出来」当成一回事；
「这条是不是新的」必须是机器判定，凭印象写的「今日增量」不可信。

## 配置

只有 `bin/config.conf` 一个文件（从 `config.example.conf` 复制，不进版本控制）：

| 项 | 说明 |
|---|---|
| `ENGINE` / `CLAUDE_MODEL` / `CODEX_MODEL` | 默认引擎与各自的模型 |
| `ENGINE_FALLBACK` | 配额耗尽时的兜底：`codex` / `api` / 留空 |
| `FALLBACK_BASE_URL` / `FALLBACK_AUTH_TOKEN` / `FALLBACK_MODEL` | `ENGINE_FALLBACK=api` 时用；英文翻译也走这套 |
| `GITHUB_TOKEN` | 可选。留空走未认证预算；填 `none` 显式不认证 |
| `BACKUP_BUCKET` / `PUBLISH_BUCKET` | 对象存储桶名，**留空即关闭备份与发布** |
| `SITE_URL` | 对外地址，用于生成 Atom feed，留空即不生成 |
| `PUBLIC_DAYS` | 对外只保留最近多少天，留空/0 = 不限制 |
| `SITE_LANGS` | 发布哪些语言（逗号分隔，第一个放根目录），留空 = 只发简体 |
| `CONTACT` | 页脚侵权联系邮箱 |
| `ALERT_WEBHOOK` / `ALERT_PAYLOAD` | **建议配**，留空则失败无人知道 |

## 常用命令

```bash
bin/run_task.sh <task> [engine]        # 手跑一个任务，引擎可单次覆盖
bin/schedulerctl.sh start|stop|status  # 调度器
bin/schedulerctl.sh plan               # 看未来 24 小时触发计划
bin/test.sh [--unit]                   # 全部测试；--unit 只跑纯函数那层（<1 秒）
.venv/bin/python bin/langs.py --explain  # 实际会发布哪些语言、被剔除的为什么
bin/translate.py [日期] [--all]        # 补翻英文
bin/restore_oci.sh [--days N]          # 换机器时从私有桶拉回产出
bin/difftest.sh out|dir <A> -- <B>     # 对拍两个实现的输出是否逐字节一致
```

调度器是纯 stdlib 常驻进程，不依赖 launchd 或 cron。关键行为是**睡眠补跑**：每 30 秒
判定「今天这个任务该跑了吗、跑过了吗」——合盖睡过某一档，唤醒后立即补跑。所以任务可能
在非整点跑完，那是补跑不是出错。补跑截止点是下一档开始前 30 分钟，超过记 `skipped`。

## 跑不动的时候

有三类失败**重试帮不上忙**，共同点是「等 10 分钟再来一次必然还是失败」：

| 类别 | 典型报错 | 该做的事 |
|---|---|---|
| 配额耗尽 | `usage limit` / `429` | 等窗口重置，或换额度更宽的引擎 |
| 余额不足 | `Insufficient Balance` / `402` | 去供应商后台充值，靠等不会好 |
| 凭据失效 | `invalid api key` / `401` | 换 key |

三类都跳过常规重试，配了 `ENGINE_FALLBACK` 就立刻换引擎（不同供应商、不同配额池），
没有就放弃当档、下一档照常。**分开认是为了告警能说清该做什么**——余额不足被报成配额
耗尽的话，提示会是「等窗口重置」，而那是永远等不到的。

`ENGINE_FALLBACK=api` 走第三方 Anthropic 兼容端点（按量付费，不受订阅窗口约束），
但它有自己的失败方式——是另一个独立的失败源，不是保证。

## 失败时怎么知道

四层，前两层在本机、后两层不在：

| 层 | 覆盖 |
|---|---|
| `bin/alert.py` | 任务终局失败、错过补跑、调度循环连续异常、翻译失败 |
| 日终核对 | 漏跑——它不产生任何失败记录，只有主动核对才看得见 |
| Worker 定时自查 | **整机不在**。每 6 小时拉 `status.json`，超过 26 小时没新产出就告警 |
| Workers Logs | 告警通道自己坏了 |

**第三层不能省**：前两层都跑在那台机器上，机器一关它们一起沉默。`status.json` 里的
时间戳带时区并另给 epoch——Worker 跑在 UTC，不带偏移量的 ISO 会差整整一个时区，
监控要么不响、要么系统性晚 8 小时，而且是无声的。

判定成功不能只看 HTTP 状态码：飞书/Lark、钉钉、企业微信**错误也返回 200**，失败写在
body 的 `code` 里。

## 安全边界

分析层是唯一接触外部不可信文本的环节——RSS 正文、仓库描述、热榜标题都是别人能写的
内容，每天无人值守地喂给模型。所以：

- **跑在沙箱里**：读不到任何明文凭据、出不了网，`bin/` 与 `prompts/` 只读。
  **不使用 `--dangerously-skip-permissions`**。
- **不继承交互式会话的 MCP server**（`--strict-mcp-config`）。MCP server 是独立进程、
  不在沙箱里，`allowedDomains: []` 对它无效——少了这条，「分析层出不了网」就是假的。
- **发布前清洗 HTML**：python-markdown 默认原样放行 HTML，一条构造过的标题就能在公开
  页面上执行脚本。`nh3` 装不上直接拒绝生成站点。
- **发布前扫凭据**：沙箱禁网挡得住「把令牌发出去」，挡不住「把令牌写进报告」——而报告
  第二天就在公开站点上。`bin/leakcheck.py` 命中即中止发布并告警。

两个引擎各自恰好一层沙箱：claude 用自带的，codex 自带的**只限制写不限制读**，改用外层
seatbelt / bubblewrap。**不要套两层**——嵌套会让 Bash 整个失效，而模型会靠 Read/Write
绕过去把任务做完，日志上看不出异常。换平台或改过路径清单后跑 `bin/sandbox-selftest.sh`。

## 对外发布（可选）

只在本机看就跳过这节——桶名留空，备份与发布自动跳过，`deploy/` 也可以无视。

产出同步到对象存储（私有桶存全量，公开桶只放站点且**可读不可列表**），前面挂一层
Cloudflare Worker 补默认文档、做边缘缓存、按扩展名给 content-type。见 `deploy/README.md`。

**保留期**：`PUBLIC_DAYS` 限制对外可见天数，本地与私有桶始终全量。生成两份而不是
「生成一份只传一部分」——后者的页面仍会链到没上传的日子。光少生成也不够：公开桶列不出
清单，但 `2026-09-02.html` 这种地址是能猜的，所以发布时会**下线超窗对象**。

**三语**：`SITE_LANGS=zh-CN,zh-TW,en` 产出 `/`、`/zh-TW/`、`/en/`。三者代价差两个量级，
实现方式也不同——繁体由 OpenCC `s2twp` 构建时现转（台湾用词，0.02 秒/篇，不落盘），
英文由 `bin/translate.py` 预先翻（约 10 万 token/天，走按量付费那套配置）。
**「列进配置」不等于「开启」**：`en` 还要 `FALLBACK_*` 齐备，`zh-TW` 还要装了 opencc，
否则会安静地发布一个空英文站或一个跟简中逐字节相同的「繁体」站。

## 订阅

配了 `SITE_URL` 就会生成 Atom 1.0 feed，**每种语言各一份，互相独立**。把地址贴进任意
阅读器即可（Reeder、NetNewsWire、Feedly、Inoreader、Miniflux、FreshRSS 都行）：

| 语言 | 订阅地址 | 在阅读器里显示为 |
|---|---|---|
| 简体 | `https://news.wetalk.eu.org/feed.xml` | 每日简报 · 简体 |
| 繁體 | `https://news.wetalk.eu.org/zh-TW/feed.xml` | 每日簡報 · 繁體 |
| English | `https://news.wetalk.eu.org/en/feed.xml` | Daily Briefing · English |

**三种都要**就把三条地址分别添加一次——没有「合并订阅」这回事，Atom 一个地址就是一份
源。三份的条目是同一批报告的不同语言版本，`id` 各不相同，所以阅读器不会把它们判成重复，
每篇会各收到三条。只想看一种语言就只加那一条。

自建站点把域名换成你的 `SITE_URL`；只发简体时就只有第一条，标题里也不带「· 简体」
的尾巴。

**自动发现**：多数阅读器直接填站点首页就能找到 feed。页面 `<head>` 里声明了**全部三份**
（当前语言那份在前，另外两份带 `hreflang`），所以填哪个语言的首页都能一次看到三个选项，
阅读器通常默认订到当前语言那一份。

几个设计取向：

- **一篇报告一条，不是一天打包成一条。** 只关心 Trending 的人不会被另外三份淹掉。
- **全文输出**，不是摘要加链接——在阅读器里直接看完，不用回跳。
- **覆盖范围跟 `PUBLIC_DAYS` 走**，不另设固定条数。写死条数的话，满负荷时 feed 会比
  站点窄，而且不报错。7 天全文约 500 KB。
- **条目 `id` 稳定不变**——变了阅读器会把旧内容当新的重推一遍。早间草稿被下午的定稿
  覆盖时 `id` 不变、`updated` 变新，阅读器标记为「更新」而不是新条目。
- 页面超期下线后，阅读器里**已抓下的全文仍在**，只是其中的链接会指向失效页。这是
  保留期的固有结果，不是 bug。

## 目录

```
bin/  prompts/  deploy/  tests/            仓库的全部内容，只放代码
data/ reports/ site/ site-public/ logs/ state/    运行时生成，不进版本控制
```

`bin/config.conf` 与 `deploy/wrangler.toml` 含环境特有值，仓库里只有 `*.example.*` 模板。
`data/trending/<日期>/snap_t3.json` 是次日增量的基线，换机器用 `bin/restore_oci.sh` 拉回
（含 `state/`，所以当天跑过的任务不会重跑）。`state/sandbox.sb` 与 `agent-settings.json`
有意不备份——按本机路径渲染，换机器会自动重新生成。

## 为什么换语言，以及换到了什么

这套东西在 Python 上跑得好好的，重写不是因为它不行。下面按价值递减排，第三条是负的。

### 一、真正由语言带来的：依赖归零

这是唯一一条「不换语言就拿不到」的收益。Python 版要一个 `.venv`，里面装着
`markdown`、`nh3`、`opencc`，外面还要一个 `oci` CLI——那是个一百多兆的 Python 包，
而这条流水线只用到它三个动作（传对象、删对象、列清单）。

这不是洁癖，是实测踩过的故障形态：

- `opencc` 漏装 → 繁体语言被**静默**剔掉，站点照常发布，少一个语言没人知道
  （所以后来 `requirements.txt` 才专门补了它，见 commit `78189f3`）
- `nh3` 装不上 → 只能靠 `render.py` 直接 `SystemExit` 兜着，否则就是未清洗的 HTML
  发到公开站点上

Go 版这两类故障不存在：OpenCC 词表 `go:embed` 进二进制，Markdown 渲染器自己写（安全
性来自构造而不是事后清洗），OCI 请求签名一百行标准库搞定。分发就是拷一个文件，
换机器不用装任何东西。

### 二、大部分收益其实来自「重写」，不是「换语言」

重写时真正修掉的东西，在 Python 里同样能修：

| 问题 | 性质 |
|---|---|
| 配置有 6 个 `_conf()` 加 3 处 shell `source`，重复键语义还相反（一个取第一个、一个取最后一个）——换网关时因此读到过两个不同端点 | 设计问题 |
| 时间表散在三个文件，改时刻要靠正则从 `scheduler.py` 里把它抠出来 | 设计问题 |
| 安全测试用「正文里出现 `onerror=` 就算漏」做判据，而转义后的纯文本包含这个子串却完全无害 | 测试假阳 |

重写的价值在于它强制你把每条决定重新说一遍，说不出理由的就删了。但这是**重构的红利**，
记在语言头上不诚实。

### 三、代价：新 bug、成熟度归零、行数翻倍

重写引入了三个 Python 版一个都没有的 bug，其中最有代表性的一个：新仓库的 `.gitignore`
里写的是 `site/` 而不是 `/site/`，不带前导斜杠会匹配**任意层级**的同名目录——整个站点
生成器包（`internal/site/`）**从第一次提交起就没进过版本控制**。`git status` 干干净净，
本地构建照常通过，只有真的 clone 一份才发现编译不过。

另外两个是采集目录按源数据日期而非运行日期分（午夜后会分叉），以及状态文件两边名字
不一致。都属于「重抄一遍时手滑」，跟语言无关，但确实是重写的固有成本。

规模也没省：

| | 实现 | 测试 |
|---|---|---|
| Python + Worker JS | 4274 行 | 1217 行 |
| Go + TypeScript | 7747 行 | 2302 行 |

成熟度更是直接归零。这个版本连续跑了几周，边界都磨过；新版本上线第一天只跑了两档。

**性能基本不用提**：模型调用占墙钟时间的 96%，建站从几秒变成 0.5 秒，对体感是零。

### 什么时候值得换，什么时候不值得

纯按工程 ROI 算，「在 Python 里做一次同样彻底的重构 + 把 `oci` CLI 换成直接签请求」
能拿到八成收益，风险小得多。所以别因为「Go 更快」或者「静态类型更好」就动手——
对这条流水线，前者没有意义（模型调用占墙钟 96%），后者的收益也没大到值一次重写。

换语言真正划算的情形只有一种：**依赖链本身就是故障源，而这东西要长期无人值守地跑。**

这条流水线正好符合。每天四次、没人盯着，而 `opencc` 漏装少一个语言、`nh3` 缺失让
清洗环节失效，都是**不报错**的失败——发现它们靠的是有人碰巧去看了一眼。把运行时
依赖压到零，等于把一整类「三个月后换台机器就跑不起来」的故障整体删掉，而这类故障
恰恰是无人值守场景里最贵的。

不符合这个条件的项目，照着做只会得到上面第三条：新 bug、成熟度归零、行数翻倍。

## 以它为准

要把某个模块改写成别的语言时，`python-reference` 这个 tag 是基准。这个项目适合逐模块
移植，因为两样东西都现成：

- **测试即规格**。`tests/` 下 140 个用例覆盖了纯函数的行为边界（XSS 载荷、补跑的
  跨日跨年、配额三分类、判重归一化、feed 与站点的覆盖一致性），不用自己猜什么叫对。
- **产出是确定性的**。同样的 `data/` 与参数，`build_site.py` 两次运行逐字节相同，
  所以「新实现正确」可以被证明。用 `bin/difftest.sh` 对拍：

```bash
bin/difftest.sh out .venv/bin/python bin/langs.py -- ./langs-go
bin/difftest.sh dir "python bin/build_site.py --days 7 --out /tmp/ref" /tmp/ref \
                 -- "./sitegen --days 7 --out /tmp/new" /tmp/new
```

耦合度也扫过：`langs.py` `render.py` `i18n.py` `history.py` `leakcheck.py` `alert.py`
`prune.py` `render_settings.py` `translate.py` 都是零本地依赖，可以单独换掉。
`build_site.py` 依赖 `i18n` 与 `render`，`scheduler.py` 依赖 `alert`，采集脚本依赖
`common`——按依赖顺序移植即可。

有一处移植风险提前知道：`i18n.py` 的简→繁靠 `opencc` 的 `s2twp`，值钱的是**词汇**
转换（软件→軟體、内存→記憶體、缓存→快取）而不是换字。别的语言的移植版词表质量参差，
换之前先单独验这一条。

## 更多

- `AGENTS.md`——改动前必须知道的约束，以及方法论上踩过的坑（`stars today` 是滞后值、
  `pushed_at` 会掩盖停更、豆瓣 `rating.count` 与 `comment_count` 之别等）。
  `CLAUDE.md` 软链到它，两个引擎读同一份。
- `deploy/README.md`——Cloudflare Worker 的部署与排查。
- `prompts/`——四个任务的分析契约，方法论的坑最终都落在这里。

## 许可

代码以 MIT 发布，见 `LICENSE`。

站点产出不在此列：报告中引用的标题、摘要、简介等材料版权归原作者或原平台所有，本站
仅作非商业性聚合与评述、并保留原文链接。如认为内容侵犯了您的权益，按页脚联系方式反馈
即删。
