# 受保护路径的唯一事实来源。macOS(seatbelt) 与 Linux(bubblewrap) 两个后端
# 都从这份清单生成，避免两边各写一份然后慢慢漂移。
#
# 拒绝的是「明文躺在磁盘上、没有第二道门禁」的凭据文件。
# 不在清单里的两类，是有意排除的：
#   - 系统钥匙串 / Secret Service：加密且由系统按条目仲裁，而且引擎的登录态
#     存在里面，屏蔽会直接把认证掐断（实测 claude 报 Not logged in）。
#   - 引擎自身的配置（~/.codex/config.toml 等）：那是它自己的认证。
# 这两类改由引擎侧的 Bash(security:*) 之类的拒绝规则约束。
#
# 需要 $HOME 与 $ROOT 已定义。

SBX_DENY_DIRS=(
  # 跨平台
  "$HOME/.oci"
  "$HOME/.ssh"
  "$HOME/.aws"
  "$HOME/.gnupg"
  "$HOME/.docker"
  "$HOME/.kube"
  "$HOME/.config/gh"
  "$HOME/.config/gcloud"
  # macOS
  "$HOME/Library/Cookies"
  "$HOME/Library/Application Support/Google"
  "$HOME/Library/Application Support/Firefox"
  "$HOME/Library/Messages"
  # Linux
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
)

# 分析层不得改写自己的执行脚本、prompt 与 git 历史
SBX_RO_DIRS=(
  "$ROOT/bin"
  "$ROOT/prompts"
  "$ROOT/.git"
)
