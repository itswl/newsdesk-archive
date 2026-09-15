#!/bin/bash
# 沙箱自检：验证边界真的生效，而不是配置写了但没起作用。
# 换平台、换机器、改过 sandbox-paths.sh 之后都该跑一次。
#
# 只用 test -r / test -w 探测布尔值，不读取也不输出任何文件内容。
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${HOME:="$(cd ~ && pwd)"}"
cd "$ROOT" || exit 1
. "$ROOT/bin/sandbox.sh"
. "$ROOT/bin/sandbox-paths.sh"

echo "平台: $(uname -s)"
sandbox_argv || { echo "✗ 沙箱不可用"; exit 1; }
echo "后端: ${SANDBOX_ARGV[0]}"
echo

fail=0

# 1. 敏感路径应当读不到内容
echo "── 凭据隔离 ──"
for p in "${SBX_DENY_DIRS[@]}" "${SBX_DENY_FILES[@]}"; do
  [ -e "$p" ] || continue          # 本机不存在的跳过
  out=$("${SANDBOX_ARGV[@]}" /bin/sh -c "
    if [ -d '$p' ]; then
      n=\$(ls -A '$p' 2>/dev/null | wc -l | tr -d ' '); [ \"\$n\" = 0 ] && echo EMPTY || echo LEAK
    else
      s=\$(wc -c < '$p' 2>/dev/null | tr -d ' '); [ -z \"\$s\" ] || [ \"\$s\" = 0 ] && echo EMPTY || echo LEAK
    fi" 2>/dev/null)
  case "$out" in
    EMPTY|"") printf "  ✓ %s\n" "$p" ;;
    *)        printf "  ✗ 仍可读出内容: %s\n" "$p"; fail=1 ;;
  esac
done

# 2. 项目数据应当可读可写
echo "── 项目可用性 ──"
"${SANDBOX_ARGV[@]}" /bin/sh -c "test -r '$ROOT/README.md'" \
  && echo "  ✓ 项目文件可读" || { echo "  ✗ 项目文件读不到"; fail=1; }
"${SANDBOX_ARGV[@]}" /bin/sh -c "mkdir -p '$ROOT/reports' && touch '$ROOT/reports/.sbtest' && rm -f '$ROOT/reports/.sbtest'" 2>/dev/null \
  && echo "  ✓ reports/ 可写" || { echo "  ✗ reports/ 不可写"; fail=1; }
"${SANDBOX_ARGV[@]}" /bin/sh -c "command -v python3 >/dev/null && python3 -c 'pass'" 2>/dev/null \
  && echo "  ✓ 能执行命令（python3）" || { echo "  ✗ 命令执行受阻——可能是沙箱套娃"; fail=1; }

# 3. 自身脚本应当不可改写
echo "── 防自我改写 ──"
for p in "${SBX_RO_DIRS[@]}"; do
  [ -d "$p" ] || continue
  if "${SANDBOX_ARGV[@]}" /bin/sh -c "touch '$p/.sbtest' 2>/dev/null"; then
    rm -f "$p/.sbtest"; printf "  ✗ 可写: %s\n" "$p"; fail=1
  else
    printf "  ✓ 只读: %s\n" "$p"
  fi
done

echo
[ $fail -eq 0 ] && echo "全部通过" \
  || echo "有检查未通过：分析层能读到本应隔离的凭据，或无法正常执行命令"
exit $fail
