/**
 * <ScrollView> — application-side scroll container.
 *
 * Thin wrapper over the local runtime's <ScrollBox> that:
 *
 *   - defaults to ``flexGrow=1, flexShrink=1, flexDirection="column"``
 *     so it fills available height inside <Shell> automatically
 *   - binds PageUp / PageDown / Home / End / Ctrl-U / Ctrl-D so
 *     keyboard scroll works without each screen wiring it
 *   - sticky-to-bottom is opt-in via ``stickyBottom`` prop. When on,
 *     content growing past the viewport keeps the user pinned at the
 *     latest line (chat/transcript pattern). User can break sticky
 *     by scrolling up; submitting a new message re-pins.
 *
 * Why a thin wrapper instead of using ScrollBox directly: every
 * screen needs the same defaults + same keyboard bindings. Re-
 * implementing those per screen leaks bugs (different PgUp jump
 * sizes, different Home behavior). One wrapper, one source of
 * truth.
 *
 * Limitations:
 *   - mouse wheel scroll only works when <Shell mouseTracking>; we
 *     leave that off by default. Keyboard scroll is the contract.
 *   - children must not be flex-only siblings of the ScrollView in
 *     a row container — ScrollBox doesn't propagate wheel/key events
 *     to row peers. Stick to column layouts for the screen frame.
 */
import React, { type ReactNode, useRef } from 'react';
import {
  ScrollBox,
  type ScrollBoxHandle,
  useInput,
} from '../runtime/index';

export interface ScrollViewProps {
  children: ReactNode;
  /** Pin scroll position to bottom on content growth. Useful for
   * chat transcripts. User scroll up breaks the pin; submitting a
   * new message restores it. Default: false (Home position). */
  stickyBottom?: boolean;
  /** flex-grow value — defaults to 1 so the view fills height. Set
   * to 0 if you want the view to shrink to content height instead. */
  flexGrow?: number;
  /** Disable the built-in keyboard bindings. Use when the parent
   * screen needs PgUp/PgDn for its own purpose (e.g. paginated
   * picker) and the scroll view should stay passive. */
  disableKeys?: boolean;
}

export const ScrollView: React.FC<ScrollViewProps> = ({
  children,
  stickyBottom = false,
  flexGrow = 1,
  disableKeys = false,
}) => {
  const ref = useRef<ScrollBoxHandle | null>(null);

  useInput((input, key) => {
    if (disableKeys) return;
    const s = ref.current;
    if (!s) return;
    const vh = s.getViewportHeight();
    if (key.wheelUp) {
      s.scrollBy(-3);
      return;
    }
    if (key.wheelDown) {
      s.scrollBy(3);
      return;
    }
    if (key.pageUp) {
      s.scrollBy(-Math.max(1, vh - 2));
      return;
    }
    if (key.pageDown) {
      s.scrollBy(Math.max(1, vh - 2));
      return;
    }
    // Ctrl-U / Ctrl-D — half-page scroll, less-style. Standard among
    // less, tmux, vim — covers users with no PgUp key.
    if (key.ctrl && input === 'u') {
      s.scrollBy(-Math.max(1, Math.floor(vh / 2)));
      return;
    }
    if (key.ctrl && input === 'd') {
      s.scrollBy(Math.max(1, Math.floor(vh / 2)));
      return;
    }
    // Home / End — go to start / latest. Some terminals don't have
    // dedicated Home/End keys; gg / G (vim style) is added by REPL
    // when convenient.
    if (key.ctrl && input === 'g') {
      s.scrollTo(0);
      return;
    }
    if (key.ctrl && input === 'G') {
      s.scrollToBottom();
      return;
    }
  });

  return (
    <ScrollBox
      ref={ref}
      flexDirection="column"
      flexGrow={flexGrow}
      flexShrink={1}
      stickyScroll={stickyBottom}
    >
      {children}
    </ScrollBox>
  );
};
