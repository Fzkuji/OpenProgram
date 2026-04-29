import React, {
  type MutableRefObject,
  type ReactNode,
  useCallback,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import {
  Box,
  NoSelect,
  ScrollBox,
  Text,
  type ScrollBoxHandle,
  useInput,
  useTerminalSize,
} from '../runtime/index.js';
import { useColors } from '../theme/ThemeProvider.js';

export type ScrollbarCell = 'thumb' | 'track' | 'empty';

const clamp = (value: number, min: number, max: number): number =>
  Math.min(max, Math.max(min, value));

export function computeScrollbarCells(
  viewportHeight: number,
  scrollHeight: number,
  scrollTop: number,
): ScrollbarCell[] {
  const height = Math.max(0, Math.floor(viewportHeight));
  if (height === 0) return [];

  const total = Math.max(height, Math.floor(scrollHeight));
  if (total <= height) return Array.from({ length: height }, () => 'empty');

  const maxScrollTop = Math.max(1, total - height);
  const thumbHeight = clamp(Math.round((height / total) * height), 1, height);
  const travel = Math.max(1, height - thumbHeight);
  const thumbTop = Math.round((clamp(scrollTop, 0, maxScrollTop) / maxScrollTop) * travel);

  return Array.from({ length: height }, (_, row) =>
    row >= thumbTop && row < thumbTop + thumbHeight ? 'thumb' : 'track',
  );
}

export interface TranscriptViewportProps {
  children: ReactNode;
  scrollRef?: MutableRefObject<ScrollBoxHandle | null>;
  stickyBottom?: boolean;
}

const getSnapshot = (scroll: ScrollBoxHandle | null): string => {
  if (!scroll) return '0:0:0';
  const top = Math.max(0, scroll.getScrollTop() + scroll.getPendingDelta());
  return `${scroll.getViewportHeight()}:${scroll.getScrollHeight()}:${top}`;
};

const useScrollSnapshot = (scroll: ScrollBoxHandle | null): string =>
  useSyncExternalStore(
    (listener) => scroll?.subscribe(listener) ?? (() => {}),
    () => getSnapshot(scroll),
    () => '0:0:0',
  );

const TranscriptScrollbar: React.FC<{
  scroll: ScrollBoxHandle | null;
}> = ({ scroll }) => {
  const colors = useColors();
  const snapshot = useScrollSnapshot(scroll);
  const [heightText, totalText, topText] = snapshot.split(':');
  const cells = computeScrollbarCells(
    Number(heightText) || 0,
    Number(totalText) || 0,
    Number(topText) || 0,
  );

  if (cells.length === 0) {
    return <NoSelect width={1} flexShrink={0} />;
  }

  return (
    <NoSelect width={1} flexShrink={0} marginLeft={1}>
      <Box flexDirection="column" flexShrink={0}>
        {cells.map((cell, index) => (
          <Text
            key={index}
            color={cell === 'thumb' ? colors.primary : colors.border}
          >
            {cell === 'empty' ? ' ' : cell === 'thumb' ? '┃' : '│'}
          </Text>
        ))}
      </Box>
    </NoSelect>
  );
};

export const TranscriptViewport: React.FC<TranscriptViewportProps> = ({
  children,
  scrollRef,
  stickyBottom = false,
}) => {
  const localRef = useRef<ScrollBoxHandle | null>(null);
  const [scrollHandle, setScrollHandle] = useState<ScrollBoxHandle | null>(null);
  const { rows } = useTerminalSize();

  const setRef = useCallback((handle: ScrollBoxHandle | null) => {
    localRef.current = handle;
    if (scrollRef) scrollRef.current = handle;
    setScrollHandle(handle);
  }, [scrollRef]);

  const runScroll = (
    apply: (scroll: ScrollBoxHandle) => void,
    event: { stopImmediatePropagation: () => void },
  ) => {
    const scroll = localRef.current;
    if (!scroll) return;
    apply(scroll);
    event.stopImmediatePropagation();
  };

  useInput((input, key, event) => {
    const scroll = localRef.current;
    if (!scroll) return;

    const viewportHeight = Math.max(1, scroll.getViewportHeight() || rows - 3);
    if (key.wheelUp) {
      runScroll((s) => s.scrollBy(-3), event);
      return;
    }
    if (key.wheelDown) {
      runScroll((s) => s.scrollBy(3), event);
      return;
    }
    if (key.pageUp) {
      runScroll((s) => s.scrollBy(-Math.max(1, viewportHeight - 2)), event);
      return;
    }
    if (key.pageDown) {
      runScroll((s) => s.scrollBy(Math.max(1, viewportHeight - 2)), event);
      return;
    }
    if (key.ctrl && input === 'u') {
      runScroll((s) => s.scrollBy(-Math.max(1, Math.floor(viewportHeight / 2))), event);
      return;
    }
    if (key.ctrl && input === 'd') {
      runScroll((s) => s.scrollBy(Math.max(1, Math.floor(viewportHeight / 2))), event);
      return;
    }
    if (key.home || (key.ctrl && input === 'g')) {
      runScroll((s) => s.scrollTo(0), event);
      return;
    }
    if (key.end || (key.ctrl && input === 'G')) {
      runScroll((s) => s.scrollToBottom(), event);
    }
  });

  return (
    <Box flexDirection="row" flexGrow={1} flexShrink={1}>
      <ScrollBox
        ref={setRef}
        flexDirection="column"
        flexGrow={1}
        flexShrink={1}
        stickyScroll={stickyBottom}
      >
        {children}
      </ScrollBox>
      <TranscriptScrollbar scroll={scrollHandle} />
    </Box>
  );
};
