#!/bin/bash
# 对拍：跑两个实现，比对输出是否完全一致。
#
# 拿这个项目练 Go / TS 时，这是让重写变得可验证的那一步——不是「写完跑跑看」，
# 而是「新实现和 Python 版在同一份输入上产出逐字节相同的结果」。
#
#   difftest.sh out  <参考命令> -- <新实现命令>      比 stdout
#   difftest.sh dir  <参考命令> <目录A> -- <新实现命令> <目录B>   比产出目录
#
# 例：
#   bin/difftest.sh out .venv/bin/python bin/langs.py -- ./langs-go
#   bin/difftest.sh dir \
#     ".venv/bin/python bin/build_site.py --days 7 --out /tmp/ref" /tmp/ref -- \
#     "./sitegen --days 7 --out /tmp/new" /tmp/new
#
# 退出码 0 = 一致。不一致时打印前若干处差异并返回 1。
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

MODE="${1:-}"; shift || true
[ -n "$MODE" ] || { sed -n '2,18p' "$0"; exit 2; }

# 按 -- 切成两半
LEFT=(); RIGHT=(); seen=0
for a in "$@"; do
  if [ "$a" = "--" ]; then seen=1; continue; fi
  if [ "$seen" = 0 ]; then LEFT+=("$a"); else RIGHT+=("$a"); fi
done
[ "$seen" = 1 ] || { echo "!! 缺少 -- 分隔符"; exit 2; }

case "$MODE" in
  out)
    a=$(mktemp); b=$(mktemp)
    "${LEFT[@]}"  >"$a" 2>/dev/null; ra=$?
    "${RIGHT[@]}" >"$b" 2>/dev/null; rb=$?
    [ "$ra" = "$rb" ] || echo "!! 退出码不同：参考 $ra，新实现 $rb"
    if diff -q "$a" "$b" >/dev/null; then
      echo "✓ stdout 完全一致（$(wc -l <"$a" | tr -d ' ') 行）"
      rm -f "$a" "$b"; exit 0
    fi
    echo "✗ stdout 有差异："
    diff "$a" "$b" | head -20 | sed 's/^/    /'
    rm -f "$a" "$b"; exit 1 ;;

  dir)
    # 最后一个参数是目录，前面的是命令。
    # 不用 ${arr[-1]}：macOS 自带的是 bash 3.2，负数下标会报 bad array subscript。
    na=$(( ${#LEFT[@]}  - 1 )); da="${LEFT[$na]}";  unset "LEFT[$na]"
    nb=$(( ${#RIGHT[@]} - 1 )); db="${RIGHT[$nb]}"; unset "RIGHT[$nb]"
    rm -rf "$da" "$db"
    eval "${LEFT[*]}"  >/dev/null 2>&1
    eval "${RIGHT[*]}" >/dev/null 2>&1
    [ -d "$da" ] || { echo "!! 参考实现没产出 $da"; exit 1; }
    [ -d "$db" ] || { echo "!! 新实现没产出 $db"; exit 1; }
    # 先比文件清单，再比内容——清单不同时逐个比内容只会刷屏
    fa=$(cd "$da" && find . -type f | sort); fb=$(cd "$db" && find . -type f | sort)
    if [ "$fa" != "$fb" ]; then
      echo "✗ 文件清单不同："
      diff <(echo "$fa") <(echo "$fb") | head -20 | sed 's/^/    /'
      exit 1
    fi
    bad=0
    while IFS= read -r f; do
      if ! cmp -s "$da/$f" "$db/$f"; then
        bad=$((bad+1))
        [ "$bad" -le 3 ] && {
          echo "✗ $f 内容不同，前 6 行差异："
          diff "$da/$f" "$db/$f" | head -6 | sed 's/^/      /'
        }
      fi
    done <<< "$fa"
    n=$(echo "$fa" | wc -l | tr -d ' ')
    if [ "$bad" = 0 ]; then echo "✓ $n 个文件逐字节一致"; exit 0; fi
    echo "✗ $n 个文件中 $bad 个不同"; exit 1 ;;

  *) echo "!! 未知模式 $MODE（可选 out | dir）"; exit 2 ;;
esac
