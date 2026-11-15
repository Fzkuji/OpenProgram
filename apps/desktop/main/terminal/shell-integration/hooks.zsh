# Appended hooks leave user functions and hook order intact.
[[ $PPID == $OP_SHELL_PARENT && -o interactive && -o rcs ]] || return
[[ -z ${_op_shell_root-} ]] || return
(preexec_functions+=(_op_shell_preexec); precmd_functions+=(_op_shell_precmd)) 2>/dev/null || return
_op_shell_root=$$
_op_shell_nonce=$OP_SHELL_NONCE
_op_shell_sequence=0
_op_shell_cycle=0
_op_shell_active=0
_op_shell_emit() {
  [[ $$ == $_op_shell_root && $ZSH_SUBSHELL == 0 ]] || return
  local hex=''
  if [[ $1 != start ]]; then
    hex=$(builtin printf '%s' "$PWD" | /usr/bin/od -An -tx1 | /usr/bin/tr -d ' \n')
    (( ${#hex} <= 32768 )) || return
  fi
  (( _op_shell_sequence += 1 ))
  builtin printf '\033]777;OpenProgram;1;%s;%s;%s;%s;%s;%s;%s\007' "$_op_shell_nonce" "$_op_shell_root" "$_op_shell_sequence" "$1" "$_op_shell_cycle" "$2" "$hex"
}
_op_shell_preexec() {
  [[ $$ == $_op_shell_root && $ZSH_SUBSHELL == 0 ]] || return
  (( _op_shell_cycle += 1 ))
  _op_shell_active=1
  _op_shell_emit start ''
}
_op_shell_precmd() {
  local result=$?
  [[ $$ == $_op_shell_root && $ZSH_SUBSHELL == 0 ]] || return $result
  if (( _op_shell_active )); then _op_shell_emit end "$result"; else _op_shell_emit cwd ''; fi
  _op_shell_active=0
  return $result
}
preexec_functions+=(_op_shell_preexec)
precmd_functions+=(_op_shell_precmd)
_op_shell_emit ready ''
unset OP_SHELL_NONCE OP_SHELL_PARENT OP_SHELL_DIR OP_SHELL_USER_ZDOTDIR OP_SHELL_USER_ZDOTDIR_SET
