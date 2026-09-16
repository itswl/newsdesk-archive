#!/bin/bash
# 每日简报单任务执行器。
#   用法: run_task.sh <task> [engine]
#   task   : ai | trending | momoyu | douban
#   engine : claude | codex   （缺省取 bin/config.conf 里的 ENGINE）
#
# 三步：采集(脚本，沙箱外) → 分析(模型，沙箱内) → 发布(build_site)
#
# 为什么采集在沙箱外、分析在沙箱内：
#   采集要 gh 凭据和网络，跑的是本仓库自己的脚本，不读外部文本；
#   分析只读本地数据文件，却是唯一接触外部不可信内容（RSS 正文、仓库描述、
#   热榜标题）的环节——所以把它关进一个读不到凭据、也出不了网的沙箱。
set -uo pipefail

# 路径一律自行推导，不写死，换机器或别人 clone 都能直接跑
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${HOME:="$(cd ~ && pwd)"}"
export HOME
export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH:-/usr/bin:/bin:/usr/sbin:/sbin}"
cd "$ROOT" || exit 1

# 引擎与模型的唯一配置入口
CONF="$ROOT/bin/config.conf"
if [ ! -r "$CONF" ]; then
  echo "!!! 缺少 $CONF"
  echo "!!! 首次使用请先执行： cp bin/config.example.conf bin/config.conf"
  exit 1
fi
# shellcheck source=/dev/null
. "$CONF"

TASK="${1:-}"
ENGINE="${2:-$ENGINE}"
DATE=$(date +%F)
TS() { date "+%Y-%m-%d %H:%M:%S"; }
LOG="$ROOT/logs/${DATE}_${TASK}.log"
mkdir -p "$ROOT/logs" "$ROOT/state"
exec >>"$LOG" 2>&1

echo "════════ $(TS)  task=$TASK  engine=$ENGINE ════════"

# 两个引擎各自恰好一层沙箱，机制不同但边界等价：
#   claude —— 用它自带的沙箱（跨平台；bin/agent-settings.template.json 渲染后传入）
#   codex  —— 自带的不限制读，改用外层沙箱（macOS seatbelt / Linux bubblewrap，
#             由 bin/sandbox.sh 按平台分发，路径清单统一来自 bin/sandbox-paths.sh）
# 配置缺失或平台无可用沙箱，一律拒绝运行，不静默降级成无防护。
# shellcheck source=/dev/null
. "$ROOT/bin/sandbox.sh"
TPL="$ROOT/bin/agent-settings.template.json"
SETTINGS="$ROOT/state/agent-settings.json"

render_settings() {
  [ -r "$TPL" ] || { echo "!!! 权限模板缺失: $TPL，拒绝运行"; return 1; }
  # 拒读路径不在模板里写死——和 codex 的 seatbelt 共用 bin/sandbox-paths.sh。
  # 以前两边各一份手写清单，装了新工具只改一头，另一头默默落后。
  # shellcheck source=/dev/null
  . "$ROOT/bin/sandbox-paths.sh" || { echo "!!! 读不到 sandbox-paths.sh，拒绝运行"; return 1; }
  { sbx_deny_dirs_for claude; printf '%s\n' "${SBX_DENY_FILES[@]}"; } \
    | python3 "$ROOT/bin/render_settings.py" "$TPL" "$SETTINGS" "$HOME" "$ROOT" \
    || { echo "!!! 渲染权限文件失败，拒绝运行"; return 1; }
  python3 -c "import json,sys;json.load(open(sys.argv[1]))" "$SETTINGS" \
    || { echo "!!! 渲染后的 settings 不是合法 JSON，拒绝运行"; return 1; }
}

run_model() {
  local prompt_file="$1"
  local prompt; prompt="$(cat "$prompt_file")"
  # prompt 里写 <今天>，这里换成真实日期。让模型自己推日期，算错的后果是
  # 读到不存在的目录（任务失败）或读到昨天的数据（静默产出错误报告）。
  prompt="${prompt//<今天>/$DATE}"
  echo "--- $(TS) $ENGINE 分析中 ($(basename "$prompt_file")) · 沙箱内 ---"
  case "$ENGINE" in
    claude)
      render_settings || return 1
      # 用 Claude Code 自带的沙箱，不再外套 seatbelt —— 它自己的沙箱也是
      # seatbelt，套娃会让 Bash 整个失效（sandbox_apply: Operation not
      # permitted，连 echo 都跑不了），而模型会靠 Read/Write 绕过去把任务做完，
      # 日志上看不出异常。
      # 不用 --dangerously-skip-permissions：那会绕过所有权限检查。
      claude -p --model "$CLAUDE_MODEL" \
        --settings "$SETTINGS" \
        --permission-prompts none \
        "$prompt" </dev/null
      ;;
    codex)
      # 没有可用沙箱就拒绝：codex 自带的 workspace-write 不限制读，
      # 无外层沙箱等于无防护
      sandbox_argv || return 1
      # 自带那层关掉，避免与外层沙箱套娃；隔离由外层提供
      "${SANDBOX_ARGV[@]}" \
        codex exec -m "$CODEX_MODEL" -C "$ROOT" \
          --dangerously-bypass-approvals-and-sandbox --color never \
          "$prompt" </dev/null
      ;;
    *)
      echo "!!! 未知引擎: $ENGINE（可选 claude | codex）"; return 2 ;;
  esac
  local rc=$?
  [ $rc -ne 0 ] && echo "!!! $(TS) $ENGINE 失败 (exit $rc)"
  return $rc
}

# 采集成功后打标记。调度器重试的是整个任务，但失败多半发生在分析阶段——
# 没必要把采集重来一遍：trending 那是 1.5 分钟、约 200 次 gh 调用，还啃速率配额。
FETCH_MARK="$ROOT/state/fetched-${TASK}-${DATE}.ok"
fetch() {
  if [ -f "$FETCH_MARK" ] && [ "${FORCE_FETCH:-0}" != 1 ]; then
    echo "--- $(TS) 采集已完成过（$(basename "$FETCH_MARK")），跳过。强制重采: FORCE_FETCH=1 ---"
    return 0
  fi
  echo "--- $(TS) 采集（沙箱外，需凭据与网络）---"
  "$@" || { echo "!!! 采集失败"; exit 1; }
  : > "$FETCH_MARK"
}

# 跨期比对：把「今天 vs 前 14 天」的差异算成确定性的量，供分析层直接引用。
# 不算的话模型只能凭印象判断「这条是不是新的」，「只报增量」就无从核验。
hist() { echo "--- $(TS) 跨期比对 ---"; python3 bin/history.py "$1" "$DATE" || echo "!! 跨期比对失败，本期按单日分析"; }

case "$TASK" in
  ai)       fetch python3 bin/fetch_ainews.py --hours 36;        hist ainews;   run_model prompts/ai-news.md  || exit 1 ;;
  trending) fetch python3 bin/fetch_trending.py --tier t3;       hist trending; run_model prompts/trending.md || exit 1 ;;
  momoyu)   fetch python3 bin/fetch_momoyu.py;                   hist momoyu;   run_model prompts/momoyu.md   || exit 1 ;;
  douban)   fetch python3 bin/fetch_douban.py --tag 热门 --n 10;  hist douban;   run_model prompts/douban.md   || exit 1 ;;
  *) echo "用法: run_task.sh <ai|trending|momoyu|douban> [claude|codex]"; exit 2 ;;
esac

echo "--- $(TS) 重建站点 ---"
# 两份产出，边界是有意的：
#   site/         全量历史，给本地看、给私有桶备份
#   site-public/  只留最近 PUBLIC_DAYS 天，这份才对外发布
# 不做成一份然后「只上传一部分」——那样页面里的日期下拉和归档页仍会链到
# 没上传的日子，点进去 404。范围要在生成时就定下来。
"$ROOT/.venv/bin/python" bin/build_site.py
if [ "${PUBLIC_DAYS:-0}" -gt 0 ] 2>/dev/null; then
  "$ROOT/.venv/bin/python" bin/build_site.py --days "$PUBLIC_DAYS" --out site-public
fi

echo "--- $(TS) 清理超期数据 ---"
python3 bin/prune.py

# 备份在沙箱外跑：需要 ~/.oci 凭据，而分析层沙箱正是要拒掉那个目录
echo "--- $(TS) 备份到对象存储 ---"
bash bin/backup_oci.sh "$DATE"

echo "════════ $(TS)  完成 ════════"
