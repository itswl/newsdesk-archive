#!/bin/bash
# 调度器控制。用法: schedulerctl.sh start|stop|restart|status|plan|log [行数]|once <task> [claude|codex]
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH:-/usr/bin:/bin:/usr/sbin:/sbin}"
PIDF="$ROOT/state/scheduler.pid"
OUT="$ROOT/logs/scheduler.out"
cd "$ROOT" || exit 1

case "${1:-status}" in
  start)
    if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
      echo "已在运行 (pid $(cat "$PIDF"))"; exit 0
    fi
    mkdir -p logs state
    nohup python3 bin/scheduler.py >>"$OUT" 2>&1 &
    sleep 1
    [ -f "$PIDF" ] && echo "已启动 (pid $(cat "$PIDF"))" || { echo "启动失败，看 $OUT"; tail -5 "$OUT"; exit 1; }
    ;;
  stop)
    if [ -f "$PIDF" ]; then
      PID=$(cat "$PIDF"); kill "$PID" 2>/dev/null && echo "已发送停止信号 (pid $PID)"
      for _ in $(seq 20); do kill -0 "$PID" 2>/dev/null || break; sleep 1; done
      kill -0 "$PID" 2>/dev/null && { echo "未在 20s 内退出，强杀"; kill -9 "$PID"; rm -f "$PIDF"; }
      echo "已停止"
    else echo "未在运行"; fi
    ;;
  restart) "$0" stop; "$0" start ;;
  status)  python3 bin/scheduler.py --status ;;
  plan)    python3 bin/scheduler.py --dry-run ;;
  log)     tail -n "${2:-40}" -f "$ROOT/logs/scheduler.log" ;;
  once)    python3 bin/scheduler.py --once "${2:?用法: schedulerctl.sh once <task> [claude|codex]}" ${3:+--engine "$3"} ;;
  *) echo "用法: $0 start|stop|restart|status|plan|log [行数]|once <task> [claude|codex]"; exit 2 ;;
esac
