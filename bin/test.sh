#!/bin/bash
# 跑全部测试。改过 bin/ 下任何东西之后跑一遍。
#
# 分两层：
#   单元测试  —— 纯函数，快，无网络无外部依赖，随时可跑
#   沙箱自检  —— 起真的 sandbox-exec 实测边界，慢，且结果依赖本机装了哪些工具
# 默认两层都跑；只想要快的那层用 --unit。
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY=python3

UNIT_ONLY=0
args=()
for x in "$@"; do
  case "$x" in --unit) UNIT_ONLY=1 ;; *) args+=("$x") ;; esac
done

echo "════ 单元测试 ════"
"$PY" -m unittest discover -s tests ${args+"${args[@]}"} || exit 1

[ "$UNIT_ONLY" = 1 ] && exit 0
echo
echo "════ 沙箱自检（实测边界）════"
bash "$ROOT/bin/sandbox-selftest.sh"
