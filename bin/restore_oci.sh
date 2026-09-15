#!/bin/bash
# 从 OCI 私有桶恢复产出。换机器、重装、或本地数据丢了的时候用。
#
#   git clone <repo> news && cd news
#   cp bin/config.example.conf bin/config.conf   # 填 BACKUP_BUCKET 等
#   bash bin/restore_oci.sh                      # 全量
#   bash bin/restore_oci.sh --days 14            # 只要最近 14 天
#
# 为什么值得有这个脚本：data/<源>/<日期>/ 是次日增量计算的基线，报告里
# 「新出现 / 连续第几天」全靠它。丢了就不是少几篇文章，是方法论历史断掉。
# 换机器如果只能靠一句「手动带过去」，那等于没有恢复路径。
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH:-/usr/bin:/bin}"
cd "$ROOT" || exit 1

DAYS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --days) DAYS="${2:-0}"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "未知参数: $1"; exit 2 ;;
  esac
done

[ -r "$ROOT/bin/config.conf" ] || {
  echo "!! 缺少 bin/config.conf"
  echo "   先执行： cp bin/config.example.conf bin/config.conf 再填 BACKUP_BUCKET"
  exit 1; }
# shellcheck source=/dev/null
. "$ROOT/bin/config.conf"
[ -n "${BACKUP_BUCKET:-}" ] || { echo "!! config.conf 里 BACKUP_BUCKET 为空，没有可恢复的来源"; exit 1; }
command -v oci >/dev/null 2>&1 || { echo "!! 找不到 oci CLI"; exit 1; }

NS="${BACKUP_NAMESPACE:-$(oci os ns get --query data --raw-output 2>/dev/null)}"
[ -n "$NS" ] || { echo "!! 取不到 namespace，检查 ~/.oci/config"; exit 1; }
oci os bucket get -ns "$NS" --name "$BACKUP_BUCKET" >/dev/null 2>&1 \
  || { echo "!! 桶 $BACKUP_BUCKET 不存在或无权访问"; exit 1; }

echo "从 oci://$BACKUP_BUCKET (namespace $NS) 恢复到 $ROOT"
echo

get() {  # get <对象前缀> <本地目录>
  mkdir -p "$2"
  # --overwrite 让重复执行是幂等的；不加会在已存在时交互式提问，脚本里会卡死
  if oci os object bulk-download -ns "$NS" -bn "$BACKUP_BUCKET" \
       --prefix "$1" --download-dir "$2" --overwrite >/dev/null 2>&1; then
    n=$(find "$2" -type f 2>/dev/null | wc -l | tr -d ' ')
    echo "  ✓ $1  ($n 个文件)"
  else
    echo "  ✗ $1  下载失败"
  fi
}

get "reports/" "$ROOT"
get "site/"    "$ROOT"
get "state/"   "$ROOT"

if [ "$DAYS" -gt 0 ]; then
  echo "  只取最近 $DAYS 天的 data/"
  for i in $(seq 0 $((DAYS - 1))); do
    # date 的参数 macOS 与 GNU 不同，两种都试
    d=$(date -v-${i}d +%F 2>/dev/null || date -d "-$i day" +%F 2>/dev/null)
    [ -n "$d" ] || continue
    for src in trending momoyu douban ainews; do
      oci os object bulk-download -ns "$NS" -bn "$BACKUP_BUCKET" \
        --prefix "data/$src/$d/" --download-dir "$ROOT" --overwrite >/dev/null 2>&1
    done
  done
  echo "  ✓ data/  ($(find "$ROOT/data" -type f 2>/dev/null | wc -l | tr -d ' ') 个文件)"
else
  get "data/" "$ROOT"
fi

echo
echo "恢复完成。接下来："
echo "  1. python3 -m venv .venv && .venv/bin/pip install markdown nh3"
echo "  2. bash bin/test.sh                 # 确认沙箱边界在新机器上真的生效"
echo "  3. .venv/bin/python bin/build_site.py   # 重建站点"
echo "  4. bash bin/schedulerctl.sh start   # 起调度器"
echo
echo "注意：state/sandbox.sb 与 state/agent-settings.json 是按本机路径渲染的，"
echo "      有意不备份，首次运行会自动重新生成。"
