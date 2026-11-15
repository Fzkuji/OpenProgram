# Forward only this native startup phase, restoring the user ZDOTDIR state.
if [[ $OP_SHELL_USER_ZDOTDIR_SET == 1 ]]; then ZDOTDIR=$OP_SHELL_USER_ZDOTDIR; else unset ZDOTDIR; fi
[[ -r ${ZDOTDIR-$HOME}/.zshenv ]] && source "${ZDOTDIR-$HOME}/.zshenv"
OP_SHELL_USER_ZDOTDIR_SET=${+ZDOTDIR}
OP_SHELL_USER_ZDOTDIR=${ZDOTDIR-}
[[ $PPID == $OP_SHELL_PARENT && -o rcs ]] && ZDOTDIR=$OP_SHELL_DIR
