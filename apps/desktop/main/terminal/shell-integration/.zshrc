# Forward only this native startup phase, restoring the user ZDOTDIR state.
if [[ $OP_SHELL_USER_ZDOTDIR_SET == 1 ]]; then ZDOTDIR=$OP_SHELL_USER_ZDOTDIR; else unset ZDOTDIR; fi
# /etc/zshrc already ran with ZDOTDIR pointing here; keep history out of this temporary directory.
[[ $HISTFILE == $OP_SHELL_DIR/.zsh_history ]] && HISTFILE=${ZDOTDIR:-$HOME}/.zsh_history
[[ -r ${ZDOTDIR-$HOME}/.zshrc ]] && source "${ZDOTDIR-$HOME}/.zshrc"
OP_SHELL_USER_ZDOTDIR_SET=${+ZDOTDIR}
OP_SHELL_USER_ZDOTDIR=${ZDOTDIR-}
[[ $PPID == $OP_SHELL_PARENT && -o rcs ]] && ZDOTDIR=$OP_SHELL_DIR
