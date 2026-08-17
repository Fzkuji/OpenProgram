/**
 * <Shell> — root component for OpenProgram TUI screens.
 *
 * Two render modes:
 *
 *  - ``mode="inline"`` (default) — main-buffer flow. The Shell renders
 *    a small dynamic strip (input + status + any in-flight modals).
 *    Use only for screens that intentionally rely on the terminal's
 *    native scrollback outside the React tree.
 *
 *  - ``mode="alt"`` — alt-screen flow. Wraps in <AlternateScreen> and
 *    pins height to terminal rows so flexbox children can fill the
 *    viewport. Use for persistent full-screen interfaces such as the
 *    chat REPL, demo, and future browser/transcript views.
 *
 * Both modes provide ToastProvider + ModalProvider + esc handler so
 * picker / form code is mode-agnostic.
 */
import React, { type ReactNode } from 'react';
import { AlternateScreen, Box, useInput, useTerminalSize } from '../runtime/index';
import { ModalProvider, useModal } from './ModalProvider.js';
import { ToastProvider } from './ToastProvider.js';

export interface ShellProps {
  children: ReactNode;
  /** Enable SGR mouse tracking (wheel + click). Default off. Some
   * older terminals (Apple Terminal) and SSH-via-tmux setups don't
   * play nicely. Off keeps the keyboard-only path bulletproof. */
  mouseTracking?: boolean;
  /** Render mode — see component doc. Default ``"inline"``. */
  mode?: 'inline' | 'alt';
}

/**
 * Listens for esc and pops the top-of-stack modal. Lives inside the
 * ModalProvider so a fresh handler runs whenever the stack changes
 * (no stale closures over an empty stack).
 *
 * We deliberately do NOT swallow esc when the stack is empty — the
 * legacy PromptInput / pickers still consume their own esc for
 * inline cancel actions.
 */
const ModalEscHandler: React.FC = () => {
  const modal = useModal();
  useInput((_input, key) => {
    if (key.escape && modal.stack.length > 0) {
      modal.pop();
    }
  });
  return null;
};

/**
 * Alt-screen body — fixed height, full width. Children flex inside
 * the rows×cols box so ScrollView and Co. have a height to grow into.
 */
const AltShellInner: React.FC<{ children: ReactNode }> = ({ children }) => {
  const { columns, rows } = useTerminalSize();
  return (
    <Box flexDirection="column" width={columns} height={rows}>
      {children}
    </Box>
  );
};

/**
 * Inline body — dynamic height. ink redraws this strip in place via
 * cursor-up; no fixed height because we want the strip to be exactly
 * as tall as its current children (1 line when idle, more when a
 * picker / form is open). Width is unconstrained so wrap honors the
 * terminal width itself.
 */
const InlineShellInner: React.FC<{ children: ReactNode }> = ({ children }) => (
  <Box flexDirection="column">
    {children}
  </Box>
);

export const Shell: React.FC<ShellProps> = ({
  children,
  mouseTracking = false,
  mode = 'inline',
}) => {
  if (mode === 'alt') {
    return (
      <AlternateScreen mouseTracking={mouseTracking}>
        <ToastProvider>
          <ModalProvider>
            <ModalEscHandler />
            <AltShellInner>{children}</AltShellInner>
          </ModalProvider>
        </ToastProvider>
      </AlternateScreen>
    );
  }
  return (
    <ToastProvider>
      <ModalProvider>
        <ModalEscHandler />
        <InlineShellInner>{children}</InlineShellInner>
      </ModalProvider>
    </ToastProvider>
  );
};
