#!/bin/bash
# 把产出备份到 OCI 对象存储（私有桶）。由 run_task.sh 末尾调用。
#
# 必须在沙箱外跑：需要 ~/.oci 凭据，而分析层的沙箱正是要拒掉那个目录。
#
# 为什么值得做：data/trending/<日期>/snap_t3.json 是次日增量计算的基线，
# 自从 data/ 移出版本控制后就只存在于本机，机器坏了方法论历史就断了。
# 对象存储 20GB 额度且不占那快满的 200GB 块存储配额，是这里唯一合适的落点。
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH:-/usr/bin:/bin}"
cd "$ROOT" || exit 1
# 没配过就直接跳过——备份与发布本来就是可选的，不该拦住本地使用
[ -r "$ROOT/bin/config.conf" ] || { echo "未配置 bin/config.conf，跳过备份与发布"; exit 0; }
# shellcheck source=/dev/null
. "$ROOT/bin/config.conf"

[ -n "${BACKUP_BUCKET:-}" ] || { echo "备份未启用（config.conf 里 BACKUP_BUCKET 为空）"; exit 0; }
command -v oci >/dev/null 2>&1 || { echo "!! 找不到 oci CLI，跳过备份"; exit 0; }

NS="${BACKUP_NAMESPACE:-$(oci os ns get --query data --raw-output 2>/dev/null)}"
[ -n "$NS" ] || { echo "!! 取不到 object storage namespace，跳过备份"; exit 0; }

TENANCY=$(awk -F= '/^\[DEFAULT\]/{f=1;next}/^\[/{f=0}f&&$1~/^ *tenancy *$/{gsub(/ /,"",$2);print $2}' "$HOME/.oci/config" 2>/dev/null)

# 桶不存在就建（私有）。幂等：已存在时 create 报错，忽略即可。
if ! oci os bucket get -ns "$NS" --name "$BACKUP_BUCKET" >/dev/null 2>&1; then
  echo "创建私有桶 $BACKUP_BUCKET"
  oci os bucket create -ns "$NS" --name "$BACKUP_BUCKET" \
    --compartment-id "$TENANCY" --public-access-type NoPublicAccess >/dev/null 2>&1 \
    || { echo "!! 建桶失败，跳过备份"; exit 0; }
fi

DATE="${1:-$(date +%F)}"
up() {  # up <桶> <本地目录> <对象前缀>
  [ -d "$2" ] || return 0
  oci os object bulk-upload -ns "$NS" -bn "$1" \
    --src-dir "$2" --object-prefix "$3" --overwrite --no-multipart \
    --content-type auto >/dev/null 2>&1 \
    && echo "  ✓ ${3:-/}" || echo "  ✗ ${3:-/} 上传失败"
}

echo "备份 -> oci://$BACKUP_BUCKET (namespace $NS)"
up "$BACKUP_BUCKET" "$ROOT/reports"           "reports/"
up "$BACKUP_BUCKET" "$ROOT/site"              "site/"
for src in trending momoyu douban ainews; do
  up "$BACKUP_BUCKET" "$ROOT/data/$src/$DATE" "data/$src/$DATE/"
done

# state/ 也要备：调度器的「今天哪些跑过」状态在这里，不带上的话换机器恢复后
# 当天的任务会全部重跑一遍（浪费一整个用量窗口），而且 fetched-*.ok 标记丢了
# 连采集都会重来。以前漏掉它，恢复步骤只能靠一句「手动带过去」。
# 只挑需要的，不整目录传：sandbox.sb 与 agent-settings.json 是按本机路径渲染的，
# 换机器必须重新生成，带过去反而是错的。
if [ -d "$ROOT/state" ]; then
  tmp="$ROOT/state/.backup"; rm -rf "$tmp"; mkdir -p "$tmp"
  for f in scheduler.json health.json; do
    [ -f "$ROOT/state/$f" ] && cp "$ROOT/state/$f" "$tmp/"
  done
  cp "$ROOT"/state/fetched-*.ok "$tmp/" 2>/dev/null
  up "$BACKUP_BUCKET" "$tmp" "state/"
  rm -rf "$tmp"
fi

# 公开发布：只传 site/，桶是 ObjectReadWithoutList——能按 URL 取文件，
# 但列不出清单。注意对象存储没有「默认文档」概念，桶根路径不会自动返回
# index.html，链接必须写到 /o/index.html。
if [ -n "${PUBLISH_BUCKET:-}" ]; then
  # 对外发布的是哪一份：配了 PUBLIC_DAYS 就用限窗的 site-public/，否则全量 site/
  PUBDIR="$ROOT/site"
  if [ "${PUBLIC_DAYS:-0}" -gt 0 ] 2>/dev/null && [ -d "$ROOT/site-public" ]; then
    PUBDIR="$ROOT/site-public"
    echo "发布 -> oci://$PUBLISH_BUCKET （公开只读，只放最近 $PUBLIC_DAYS 天）"
  else
    echo "发布 -> oci://$PUBLISH_BUCKET （公开只读，全部历史）"
  fi
  up "$PUBLISH_BUCKET" "$PUBDIR" ""

  # 删掉超窗的旧页面。少生成页面是不够的：桶是 ObjectReadWithoutList，别人列不出
  # 清单，但 2026-09-02.html 这种地址是能猜的，不删就等于「不给链接但仍可访问」。
  #
  # 三道保险，因为这是对公开桶做删除：
  #   1. 只认 YYYY-MM-DD.html 这一种名字，index/archive/feed/status 永远不动
  #   2. 本地那份必须真的有日期页，否则视为构建失败，一个都不删
  #   3. 删之前逐条打印
  prune_public() {
    local keep n_keep
    keep=$(find "$PUBDIR" -maxdepth 1 -name '????-??-??.html' -exec basename {} \; 2>/dev/null | sort)
    n_keep=$(printf '%s\n' "$keep" | grep -c '^[0-9]' || true)
    if [ "$n_keep" -eq 0 ]; then
      echo "  !! 本地没有任何日期页，疑似构建失败，跳过清理（不动公开桶）"; return 0
    fi
    local remote
    remote=$(oci os object list -ns "$NS" -bn "$PUBLISH_BUCKET" --all 2>/dev/null \
      | python3 -c "
import json,re,sys
try: d=json.load(sys.stdin)['data']
except Exception: sys.exit(0)
for o in d:
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.html', o['name']): print(o['name'])
" | sort)
    local gone
    gone=$(comm -13 <(printf '%s\n' "$keep") <(printf '%s\n' "$remote"))
    [ -z "$gone" ] && { echo "  ✓ 无超窗页面需要清理（线上 $n_keep 天）"; return 0; }
    local n=0
    while IFS= read -r obj; do
      [ -n "$obj" ] || continue
      oci os object delete -ns "$NS" -bn "$PUBLISH_BUCKET" --name "$obj" --force >/dev/null 2>&1 \
        && { echo "  − 已下线 $obj"; n=$((n+1)); } || echo "  ! 删除失败 $obj"
    done <<< "$gone"
    echo "  ✓ 清理完成：下线 $n 个超窗页面，线上保留 $n_keep 天"
  }
  prune_public
  echo "  https://objectstorage.$(oci iam region-subscription list --query 'data[0]."region-name"' --raw-output 2>/dev/null).oraclecloud.com/n/$NS/b/$PUBLISH_BUCKET/o/index.html"
fi
