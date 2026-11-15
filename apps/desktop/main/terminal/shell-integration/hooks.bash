# Bash 3.2 compatibility: decline conflicts, never eval-chain foreign traps.
[ "$PPID" = "$OP_SHELL_PARENT" ] || return
[ -z "${_op_shell_decided-}" ] || return
_op_shell_decided=1
[ "$PROMPT_COMMAND" = "$OP_SHELL_PROMPT" ] || return
if ! (unset PROMPT_COMMAND) 2>/dev/null; then return; fi
if [ -n "$OP_SHELL_DEBUG" ] || shopt -q extdebug || [[ $- == *T* ]]; then
  unset PROMPT_COMMAND
  return
fi
_op_shell_root=$$
_op_shell_nonce=$OP_SHELL_NONCE
_op_shell_sequence=0
_op_shell_cycle=0
_op_shell_active=0
_op_shell_emit() {
  [ "$$" = "$_op_shell_root" ] && [ "$BASH_SUBSHELL" = 0 ] || return
  local hex=''
  if [ "$1" != start ]; then
    hex=$(builtin printf '%s' "$PWD" | /usr/bin/od -An -tx1 | /usr/bin/tr -d ' \n')
    [ "${#hex}" -le 32768 ] || return
  fi
  _op_shell_sequence=$((_op_shell_sequence+1))
  builtin printf '\033]777;OpenProgram;1;%s;%s;%s;%s;%s;%s;%s\007' "$_op_shell_nonce" "$_op_shell_root" "$_op_shell_sequence" "$1" "$_op_shell_cycle" "$2" "$hex"
}
_op_shell_debug() {
  [ "$$" = "$_op_shell_root" ] && [ "$BASH_SUBSHELL" = 0 ] || return
  if [ "$_op_shell_active" = 0 ]; then
    _op_shell_cycle=$((_op_shell_cycle+1)); _op_shell_active=1
    _op_shell_emit start ''
  fi
}
_op_shell_prompt() {
  local result=$?
  if [ "$_op_shell_active" = 1 ]; then _op_shell_emit end "$result"; else _op_shell_emit cwd ''; fi
  _op_shell_active=0
  return "$result"
}
export -n PROMPT_COMMAND
PROMPT_COMMAND=_op_shell_prompt
_op_shell_emit ready ''
unset OP_SHELL_NONCE OP_SHELL_DIR OP_SHELL_PROMPT OP_SHELL_PARENT OP_SHELL_DEBUG
trap '[[ $BASH_COMMAND == _op_shell_prompt ]] || _op_shell_debug' DEBUG
