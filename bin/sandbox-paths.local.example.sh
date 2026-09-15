# 本机特有的敏感路径。复制成 sandbox-paths.local.sh（不进版本控制）后按需增删。
#
#   cp bin/sandbox-paths.local.example.sh bin/sandbox-paths.local.sh
#
# 加完跑 bin/sandbox-selftest.sh 确认真的拦住了。
# 怎么找：下面这条列出 HOME 下 3 层内所有仅属主可读的疑似凭据文件——
#   find ~ -maxdepth 3 \( -name '*.json' -o -name '*.toml' -o -name '*.yml' \
#     -o -name '*credential*' -o -name '*.pem' -o -name '*.key' \) \
#     -type f -perm -u+r ! -perm -o+r ! -perm -g+r 2>/dev/null

SBX_DENY_DIRS+=(
  # "$HOME/.config/some-tool"
)

SBX_DENY_FILES+=(
  # "$HOME/.some-tool-token"
)
