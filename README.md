# newsdesk

每日简报流水线。四路数据源各自定时采集，交给大模型分析成中文报告，再合成静态站。

不是「把网页丢给模型让它总结」——采集、跨期比对、核查口径都固化在脚本和 prompt 里，
模型只在确定的数据上做判断。采集与调度在本机跑；对外发布是可选的。

四档任务的时刻也不是随手定的：**间隔 5 小时 5 分**，让每个任务落进一个全新的模型
用量窗口、互不抢额度（见「四个任务」）。

## 快速开始

```bash
cp bin/config.example.conf bin/config.conf     # 填引擎与模型；其余留空即可
python3 -m venv .venv && .venv/bin/pip install markdown

bin/sandbox-selftest.sh          # 自检沙箱边界；未通过说明分析层能读到本机凭据
bin/run_task.sh douban           # 手跑一个任务验证链路
bin/schedulerctl.sh start        # 起调度器
```

**到这里就能用了**：报告在 `reports/<日期>/`，静态站在 `site/`，双击 `index.html`
就能看——页面自包含，不需要起服务器。想让别人也能访问、或想用阅读器订阅，才需要
看「发布」与「订阅」两节。

依赖：`curl`、Python 3、至少一个引擎（`claude` 或 `codex`）。`markdown` 必须装在
项目内 `.venv`（系统 Python 受 PEP 668 保护）。

**不需要 `gh`，也不需要 GitHub 令牌。** API 直接走 HTTPS，Trending 采集压到约 50 次
调用、装得进未认证的 60 次/小时预算；停更判断走 `commits.atom`（github.com，不计
API 配额），所以核心字段——星标、fork、授权、停更、年龄——在无令牌下全部保留。预算
不足时会跳过部分仓库与下载量指标，并在报告里说明缺口。填 `GITHUB_TOKEN` 则预算变
5000、深度不受限，但那是可选增强而非前提。

## 四个任务

| 默认时间 | 任务 | 数据源 | 采集脚本 |
|---|---|---|---|
| 06:30 | AI 简报 | 8 个 RSS/Atom feed + Hacker News | `bin/fetch_ainews.py` |
| 11:35 | 豆瓣热门电影 | `search_subjects` + `rexxar` 详情接口 | `bin/fetch_douban.py` |
| 16:40 | GitHub Trending | trending 页 + GitHub API 快照 | `bin/fetch_trending.py` |
| 21:45 | 摸摸鱼热榜 | `momoyu.cc/api/hot/list` | `bin/fetch_momoyu.py` |

时间表在 `bin/scheduler.py` 顶部。两处不要随意改：

- **相邻两档间隔 5 小时 5 分是有意的**。Claude 订阅的用量限额按 5 小时滚动窗口重置，
  这个间隔让每个任务落进全新窗口、互不抢额度，多出的 5 分钟防止卡在边界上。改成
  6 小时整看着齐整，实际会让相邻两档共用同一个窗口。
- **Trending 那一档的时刻**。单次触发让它的增量窗口天然对齐 24 小时，基线就是昨天
  同一时刻的快照。

## 怎么组织的

```
采集（纯脚本）    → data/<源>/<日期>/     原始数据落盘
跨期比对（纯脚本）→ history.json          今天 vs 前 7 天的差异
分析（模型，沙箱）→ reports/<日期>/       Markdown 报告
发布              → site/ → 对象存储 → CDN
```

**分层是为了让分析能脱离采集重跑**：改 prompt 可以拿历史数据回测，重试也不再消耗
接口配额。采集成功会打标记，同日重试跳过采集（`FORCE_FETCH=1` 强制重采）。

**所有联网都在采集层，模型只读本地文件。** 这样分析层才能在引擎之间互换——codex 走
自定义供应商时没有 web search，拿不到联网能力它会用 shell 去本地翻文件硬凑答案。

**跨期比对也在采集层算。** 只看当天会把「连续烧了四天」和「今天刚冒出来」当成一回事；
而「这条是不是新的」必须是机器判定，凭印象写出来的「今日增量」不可信。四个源分别算
判重、在榜天数、评价人数增量、名次轨迹。

## 配置

只有 `bin/config.conf` 一个文件（从 `config.example.conf` 复制，不进版本控制）：

| 项 | 说明 |
|---|---|
| `ENGINE` / `CLAUDE_MODEL` / `CODEX_MODEL` | 默认引擎与各自的模型 |
| `GITHUB_TOKEN` | **可选**。留空走未认证预算；填 `none` 显式不认证；留空且装了 `gh` 会借用它的令牌 |
| `BACKUP_BUCKET` / `PUBLISH_BUCKET` | 对象存储桶名，**留空即关闭备份与发布** |
| `SITE_URL` | 对外地址，用于生成 Atom feed（feed 里必须是绝对链接），**留空即不生成** |
| `CONTACT` | 页脚侵权联系邮箱，留空则不显示具体地址 |

## 常用命令

```bash
bin/run_task.sh <task> [engine]        # 手跑一个任务，引擎可单次覆盖
bin/schedulerctl.sh start|stop|status  # 调度器
bin/schedulerctl.sh plan               # 看未来 24 小时触发计划
bin/schedulerctl.sh once ai codex      # 通过调度器跑一次并指定引擎
bin/sandbox-selftest.sh                # 沙箱边界自检
```

调度器是纯 stdlib 常驻进程，不依赖 launchd 或 cron。关键行为是**睡眠补跑**：不是
「到点触发」，而是每 30 秒判定「今天这个任务该跑了吗、跑过了吗」——合盖睡过某一档，
唤醒后会立即补跑。所以你可能看到任务在非整点跑完，那是补跑不是出错。

补跑的截止点是**下一档开始前 30 分钟**，跟着时间表自动算（白天三档各约 4.6 小时，
夜间那档约 8.2 小时），超过就当天记 `skipped`。这个截止点必须小于两档间隔，否则
补跑会推进下一档的用量窗口——见下一节。

## 安全边界

分析层是唯一接触外部不可信文本的环节——RSS 正文、仓库描述、热榜标题都是别人能写的
内容，每天无人值守地喂给模型。所以它跑在沙箱里：**读不到任何明文凭据，也出不了网**，
`bin/` 与 `prompts/` 对它只读。**不使用 `--dangerously-skip-permissions`。**

两个引擎各自恰好一层沙箱：claude 用自带的，codex 自带的**只限制写不限制读**，改用外层
seatbelt（macOS）或 bubblewrap（Linux）。**不要套两层**——嵌套会让 Bash 整个失效，而
模型可能靠 Read/Write 绕过去把任务做完，日志上看不出异常。换平台或改过路径清单后，
自检能告诉你边界还在不在。

受保护路径在 `bin/sandbox-paths.sh`，两个平台后端从同一份生成。细节见 `AGENTS.md`。

## 发布（可选）

只在本机看就跳过这节——桶名留空，备份与发布自动跳过，`deploy/` 也可以无视。

想让别人访问：产出同步到对象存储（私有桶存全量备份，公开桶只放 `site/` 且设为**可读
不可列表**），前面挂一层 Cloudflare Worker 补上对象存储缺失的默认文档并做边缘缓存。
Worker 按扩展名给 content-type——feed 必须是 `application/atom+xml`，打成 `text/html`
阅读器不认。配置见 `deploy/README.md`。

备份必须在沙箱外跑——它需要云厂商凭据，而分析层沙箱正是要拒掉那个目录。

## 订阅

配了 `SITE_URL` 就会同时生成 `site/feed.xml`（Atom 1.0），订阅地址是
`<SITE_URL>/feed.xml`。页面 `<head>` 里有 `rel="alternate"`，多数阅读器填站点
首页就能自动发现。

几个设计取向：

- **一篇报告一条，不是一天打包成一条。** 只关心 Trending 的人不会被另外三份淹掉，
  标题也能具体到「GitHub Trending · 2026-09-15」。
- **全文输出**，不是摘要加链接——在阅读器里直接看完，不用回跳。
- **保留最近 20 条**（约五天）。单篇渲染后平均 17 KB，全文 feed 约 300 KB；正文用
  CDATA 而不是 XML 转义，否则每个 `<` 变成 `&lt;`，体积近乎翻倍。
- 条目 `id` 用页面锚点 URL，稳定不变——变了阅读器会把旧内容当新的重推一遍。

草稿版报告也会进 feed。下午定稿覆盖同一路报告时 `id` 不变、`updated` 变新，阅读器
会把它标记为更新而不是新条目。

## 目录

```
bin/  prompts/  deploy/          仓库的全部内容，只放代码
data/ reports/ site/ logs/ state/ attic/    运行时生成，不进版本控制
```

`bin/config.conf` 与 `deploy/wrangler.toml` 含环境特有值，同样不进版本控制——仓库里
只有对应的 `*.example.*` 模板。`data/trending/<日期>/snap_t3.json` 是次日增量的基线，
换机器要手动带过去。

## 更多

- `AGENTS.md`——改动前必须知道的约束，以及方法论上踩过的坑（`stars today` 是滞后值、
  `pushed_at` 会掩盖停更、豆瓣 `rating.count` 与 `comment_count` 之别等）。
  `CLAUDE.md` 软链到它，两个引擎读同一份。
- `deploy/README.md`——Cloudflare Worker 的部署与排查记录。
- `prompts/`——四个任务的分析契约，方法论的坑最终都落在这里。
