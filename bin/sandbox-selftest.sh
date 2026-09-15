#!/bin/bash
# 沙箱自检：验证边界真的生效，而不是配置写了但没起作用。
# 换平台、换机器、改过 sandbox-paths.sh 或装了新的带凭据的 CLI 之后都该跑。
#
# 两个引擎的隔离机制不同，所以分两段查：
#   codex  —— 外层 seatbelt/bubblewrap，用 test 布尔探针实测（真的读不到）
#   claude —— 它自带的沙箱，配置由 render_settings.py 生成，这里静态核对
#             生成结果是否覆盖了清单里的每一条（防止同源了却漏渲染）
#
# 只用 test -r / wc -c 探测布尔值，不读取也不输出任何文件内容。
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${HOME:="$(cd ~ && pwd)"}"
export ROOT HOME
cd "$ROOT" || exit 1
. "$ROOT/bin/sandbox.sh"
. "$ROOT/bin/sandbox-paths.sh"

ENGINE_UNDER_TEST="${1:-codex}"
echo "平台: $(uname -s)   外层沙箱引擎: $ENGINE_UNDER_TEST"
sandbox_argv "$ENGINE_UNDER_TEST" || { echo "✗ 沙箱不可用"; exit 1; }
echo "后端: ${SANDBOX_ARGV[0]}"
echo

fail=0; n_checked=0; n_skip=0

# ── 1. 外层沙箱：敏感路径应当读不到内容 ────────────────────────────────
echo "── 凭据隔离（实测，$ENGINE_UNDER_TEST 的外层沙箱）──"
exempt=$(comm -13 <(sbx_deny_dirs_for "$ENGINE_UNDER_TEST" | sort) \
                  <(printf '%s\n' "${SBX_DENY_DIRS[@]}" | sort))
while IFS= read -r p; do
  [ -n "$p" ] || continue
  [ -e "$p" ] || { n_skip=$((n_skip+1)); continue; }
  n_checked=$((n_checked+1))
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
done < <( { sbx_deny_dirs_for "$ENGINE_UNDER_TEST"; printf '%s\n' "${SBX_DENY_FILES[@]}"; } )
echo "  （本机不存在、已跳过 $n_skip 条；实测 $n_checked 条）"
if [ -n "$exempt" ]; then
  echo "  ⓘ 有意豁免（$ENGINE_UNDER_TEST 自己的认证，屏蔽会掐断登录）："
  printf '      %s\n' $exempt
fi

# ── 2. claude 那一层：核对生成的权限文件覆盖了同一份清单 ────────────────
echo "── 权限文件覆盖（静态，claude 自带沙箱）──"
gen="$ROOT/state/selftest-agent-settings.json"
if { sbx_deny_dirs_for claude; printf '%s\n' "${SBX_DENY_FILES[@]}"; } \
   | python3 "$ROOT/bin/render_settings.py" \
       "$ROOT/bin/agent-settings.template.json" "$gen" "$HOME" "$ROOT" >/dev/null 2>&1; then
  miss=$( { sbx_deny_dirs_for claude; printf '%s\n' "${SBX_DENY_FILES[@]}"; } | python3 -c '
import json, sys, os
cfg = json.load(open(sys.argv[1]))
deny = set(cfg["sandbox"]["filesystem"]["denyRead"])
rules = " ".join(cfg["permissions"]["deny"])
missing = [p for p in (l.strip() for l in sys.stdin) if p and (p not in deny or p not in rules)]
print("\n".join(missing))
sb = cfg["sandbox"]
bad = []
if not sb.get("enabled"): bad.append("sandbox.enabled 不是 true")
if not sb.get("failIfUnavailable"): bad.append("failIfUnavailable 不是 true —— 沙箱起不来会静默降级")
if sb.get("allowUnsandboxedCommands"): bad.append("allowUnsandboxedCommands 是 true —— 防护可被参数绕过")
if sb["network"].get("allowedDomains"): bad.append("网络白名单非空 —— 分析层不该出网")
if not sb["network"].get("strictAllowlist"): bad.append("strictAllowlist 不是 true")
for b in bad: print("!" + b)
' "$gen")
  bad=$(printf '%s\n' "$miss" | grep '^!' | sed 's/^!//')
  miss=$(printf '%s\n' "$miss" | grep -v '^!' | grep -v '^$')
  if [ -n "$miss" ]; then
    echo "  ✗ 清单里有路径没进权限文件："; printf '      %s\n' $miss; fail=1
  else
    echo "  ✓ 清单全部落进 denyRead 与 Read() 拒绝规则"
  fi
  if [ -n "$bad" ]; then
    printf '  ✗ %s\n' "$bad"; fail=1
  else
    echo "  ✓ fail-closed 开关完好（沙箱启用/不可用即拒/禁绕过/不出网）"
  fi
  rm -f "$gen"
else
  echo "  ✗ 权限文件渲染失败"; fail=1
fi

# ── 3. 项目数据应当可读可写 ────────────────────────────────────────────
echo "── 项目可用性 ──"
"${SANDBOX_ARGV[@]}" /bin/sh -c "test -r '$ROOT/README.md'" \
  && echo "  ✓ 项目文件可读" || { echo "  ✗ 项目文件读不到"; fail=1; }
"${SANDBOX_ARGV[@]}" /bin/sh -c "mkdir -p '$ROOT/reports' && touch '$ROOT/reports/.sbtest' && rm -f '$ROOT/reports/.sbtest'" 2>/dev/null \
  && echo "  ✓ reports/ 可写" || { echo "  ✗ reports/ 不可写"; fail=1; }
"${SANDBOX_ARGV[@]}" /bin/sh -c "command -v python3 >/dev/null && python3 -c 'pass'" 2>/dev/null \
  && echo "  ✓ 能执行命令（python3）" || { echo "  ✗ 命令执行受阻——可能是沙箱套娃"; fail=1; }

# ── 4. 自身脚本应当不可改写 ────────────────────────────────────────────
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
  || echo "有检查未通过：分析层能读到本应隔离的凭据，或防护开关被改弱"
exit $fail
