# 平台分发的沙箱包装器。用法：
#   source bin/sandbox.sh
#   sandbox_argv || exit 1          # 失败即拒绝运行，不降级
#   "${SANDBOX_ARGV[@]}" your-command ...
#
# macOS  : seatbelt，(deny file-read*) —— 访问返回 EPERM
# Linux  : bubblewrap，用空 tmpfs / /dev/null 盖住敏感路径 —— 访问看到的是空内容
# 两者语义略有差别（拒绝 vs 遮蔽），但效果一致：凭据读不出来。
#
# ⚠️ Linux 分支在 macOS 开发机上无法验证，首次在 Linux 上使用请先跑
#    bin/sandbox-selftest.sh 确认边界真的生效。

# sandbox_argv [engine]  —— engine 缺省 codex（目前只有 codex 用外层沙箱）
sandbox_argv() {
  SANDBOX_ARGV=()
  local engine="${1:-codex}"
  local here; here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  # shellcheck source=/dev/null
  . "$here/sandbox-paths.sh" || {
    echo "!!! 读不到 $here/sandbox-paths.sh，拒绝运行"; return 1; }
  # 清单必须真的有内容。source 失败或清单被清空时，下面会生成一份
  # (literal "") 什么都不拦的 profile —— 那比没有沙箱更糟，因为它看起来有。
  if ! declare -F sbx_deny_dirs_for >/dev/null \
     || [ "${#SBX_DENY_DIRS[@]}" -eq 0 ] || [ "${#SBX_RO_DIRS[@]}" -eq 0 ]; then
    echo "!!! sandbox-paths.sh 未正确载入（清单为空），拒绝运行"; return 1
  fi

  case "$(uname -s)" in
    Darwin)
      command -v sandbox-exec >/dev/null 2>&1 || {
        echo "!!! 找不到 sandbox-exec（macOS 应自带）"; return 1; }
      local prof="$ROOT/state/sandbox.sb"
      mkdir -p "$ROOT/state"
      {
        echo ";; 由 bin/sandbox.sh 从 bin/sandbox-paths.sh 生成，勿手改"
        echo "(version 1)"
        echo "(allow default)"
        echo "(deny file-read*"
        local p
        # 按引擎过滤：codex 整个进程跑在这层里，拒掉 ~/.codex 就登录不了
        while IFS= read -r p; do echo "  (subpath \"$p\")"; done < <(sbx_deny_dirs_for "$engine")
        for p in "${SBX_DENY_FILES[@]}"; do echo "  (literal \"$p\")"; done
        echo "  )"
        echo "(deny file-write*"
        for p in "${SBX_RO_DIRS[@]}";    do echo "  (subpath \"$p\")"; done
        echo "  )"
      } > "$prof" || return 1
      SANDBOX_ARGV=(sandbox-exec -f "$prof")
      ;;

    Linux)
      command -v bwrap >/dev/null 2>&1 || {
        echo "!!! 找不到 bwrap（bubblewrap）。分析层要处理外部不可信文本，"
        echo "!!! 没有沙箱就等于无防护，拒绝运行。请先安装：apt install bubblewrap"
        return 1; }
      # --dev-bind / / 保持整机可见，再逐个盖住敏感路径：
      # 目录盖空 tmpfs，文件盖 /dev/null，两者都让凭据读不出内容。
      SANDBOX_ARGV=(bwrap --dev-bind / / --die-with-parent)
      local p
      while IFS= read -r p; do [ -e "$p" ] && SANDBOX_ARGV+=(--tmpfs "$p"); done < <(sbx_deny_dirs_for "$engine")
      for p in "${SBX_DENY_FILES[@]}"; do [ -e "$p" ] && SANDBOX_ARGV+=(--ro-bind /dev/null "$p"); done
      for p in "${SBX_RO_DIRS[@]}";    do [ -e "$p" ] && SANDBOX_ARGV+=(--ro-bind "$p" "$p"); done
      SANDBOX_ARGV+=(--)
      ;;

    *)
      echo "!!! 不支持的平台: $(uname -s)。没有可用沙箱，拒绝运行。"
      return 1 ;;
  esac
}
