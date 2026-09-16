# 受保护路径的唯一事实来源。三个消费方都从这里生成，不再各写各的：
#   - bin/sandbox.sh        → macOS seatbelt / Linux bubblewrap（codex 的外层沙箱）
#   - bin/run_task.sh       → 渲染 state/agent-settings.json（claude 自带沙箱）
#   - bin/sandbox-selftest.sh → 逐条实测边界
# 以前后两者各有一份手写清单，装了新工具就只改一头，另一头默默落后。
#
# 拒绝的是「明文躺在磁盘上、没有第二道门禁」的凭据。
# 有意排除、不要加进来的：
#   - 系统钥匙串 / Secret Service：加密且由系统按条目仲裁，引擎的登录态也存在
#     里面，屏蔽会直接把认证掐断（实测 claude 报 Not logged in）。
#     对它的约束由引擎侧的 Bash(security:*) 拒绝规则承担。
#
# 需要 $HOME 与 $ROOT 已定义。

SBX_DENY_DIRS=(
  # ── 云 / 基础设施凭据 ──
  "$HOME/.oci"
  "$HOME/.ssh"
  "$HOME/.aws"
  "$HOME/.gnupg"
  "$HOME/.docker"
  "$HOME/.kube"
  "$HOME/.config/gh"
  "$HOME/.config/gcloud"
  "$HOME/.config/rclone"
  "$HOME/.cloudflared"
  "$HOME/.terraform.d"
  # ── 其他 AI/agent 工具的凭据与会话 ──
  # 这一类最容易漏：装一个新 CLI 就多一份明文密钥，而清单是 denylist，不会自己长。
  # 尤其是会话记录——里面常有粘贴进去的密钥和内部信息，不比密钥文件安全。
  "$HOME/.claude"           # 会话 transcript + 凭据；claude 自身启动时在沙箱外读，不受影响
  "$HOME/.codex"            # codex 的外层沙箱会按引擎豁免，见 sbx_deny_dirs_for()
  "$HOME/.gemini"
  "$HOME/.cc-switch"
  "$HOME/.pi"
  "$HOME/.larkin"
  "$HOME/.cursor"
  "$HOME/.config/zed"
  # ── 浏览器 Cookie / 数据 ──
  "$HOME/Library/Cookies"
  "$HOME/Library/Application Support/Google"
  "$HOME/Library/Application Support/Firefox"
  "$HOME/Library/Messages"
  "$HOME/.config/google-chrome"
  "$HOME/.config/chromium"
  "$HOME/.mozilla"
  "$HOME/.local/share/keyrings"
  "$HOME/.password-store"
)

SBX_DENY_FILES=(
  "$HOME/.netrc"
  "$HOME/.npmrc"
  "$HOME/.pypirc"
  "$HOME/.git-credentials"
  "$HOME/.claude.json"      # 与 ~/.claude 同级但不在其下，按目录拒会漏掉它
  # ── 仓库内的凭据文件 ──
  # 分析层要能读仓库（data/ 在里面），所以 $ROOT 整体是放行的——凭据文件必须
  # 逐个拒掉。这几个都是分析层用不到的：run_task.sh 与 build_site.py 读它们
  # 都在沙箱外。
  #
  # ⚠️ 这不是多余的谨慎：分析层是唯一接触外部不可信文本的环节（RSS 正文、
  # 仓库描述、热榜标题），而它的产出会公开发布。一次提示注入把 config.conf
  # 里的令牌抄进报告，就直接上线了——沙箱禁网拦不住这条路径。
  "$ROOT/bin/config.conf"
  "$ROOT/bin/sandbox-paths.local.sh"
  "$ROOT/deploy/wrangler.toml"
  "$ROOT/deploy/.dev.vars"
)

# 分析层不得改写自己的执行脚本、prompt 与 git 历史
SBX_RO_DIRS=(
  "$ROOT/bin"
  "$ROOT/prompts"
  "$ROOT/.git"
)

# ── 本机特有路径 ────────────────────────────────────────────────────────
# denylist 的老问题：它只覆盖「写它那台机器」上已知的工具。换机器、装新 CLI，
# 清单就又落后一次。所以留一个不进版本控制的位置，机器特有的路径写在那里。
# 格式与上面一致，用 += 追加。见 bin/sandbox-paths.local.example.sh。
_sbx_local="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/sandbox-paths.local.sh"
# shellcheck source=/dev/null
[ -r "$_sbx_local" ] && . "$_sbx_local"

# 每个引擎自己的认证路径，必须从「它自己的」沙箱里排除，否则认证被掐断。
# codex 的整个进程跑在外层 seatbelt 里，读不到 ~/.codex 就登录不了；
# claude 不同——它的沙箱只约束模型执行的命令，它自己读配置在沙箱外，所以
# ~/.claude 对它照拒不误。
sbx_deny_dirs_for() {
  local engine="$1" p
  for p in "${SBX_DENY_DIRS[@]}"; do
    case "$engine:$p" in
      "codex:$HOME/.codex") continue ;;
    esac
    printf '%s\n' "$p"
  done
}
