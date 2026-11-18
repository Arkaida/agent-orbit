# Source this file from ~/.zshrc; the project can be installed anywhere.
typeset -g CODEX_REMOTE_CLI="${${(%):-%x}:A:h}/agent-orbit"
ao() {
  local program="$CODEX_REMOTE_CLI"
  case "${1-}" in
    '') /bin/sh "$program" watch ;;
    local) shift; /bin/sh "$program" --local watch "$@" ;;
    l|list) shift; /bin/sh "$program" list "$@" ;;
    w|watch) shift; /bin/sh "$program" watch "$@" ;;
    p|panel) shift; /bin/sh "$program" panel "$@" ;;
    s|show) shift; /bin/sh "$program" show "$@" ;;
    a|attach) shift; /bin/sh "$program" attach "$@" ;;
    +) shift; /bin/sh "$program" follow add "$@" ;;
    -) shift; /bin/sh "$program" follow remove "$@" ;;
    f) shift; /bin/sh "$program" follow list "$@" ;;
    -h|--help|help)
      printf 'ao             持续监控关注的会话\nao p           打开桌面悬浮窗\nao local       监控本机 Codex\nao l           查看全部会话\nao + 会话名    加入关注（可同时添加多个）\nao - 会话名    取消关注\nao f           查看关注列表\nao s 会话名    查看画面\nao 会话名      进入会话\n'
      ;;
    *) /bin/sh "$program" attach "$@" ;;
  esac
}

# Compatibility with existing shell setups.
cr() { ao "$@"; }
