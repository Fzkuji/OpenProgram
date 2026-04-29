import React, {
  type MutableRefObject,
  type ReactNode,
  useCallback,
  useLayoutEffect,
  useRef,
} from 'react';
import {
  Box,
  ScrollBox,
  type ScrollBoxHandle,
  useStdin,
  useTerminalSize,
} from '../runtime/index.js';

export interface TranscriptViewportProps {
  children: ReactNode;
  scrollRef?: MutableRefObject<ScrollBoxHandle | null>;
  stickyBottom?: boolean;
}

type InputEventLike = {
  input: string;
  key: {
    wheelUp: boolean;
    wheelDown: boolean;
    pageUp: boolean;
    pageDown: boolean;
    home: boolean;
    end: boolean;
    ctrl: boolean;
  };
  stopImmediatePropagation: () => void;
};

export const TranscriptViewport: React.FC<TranscriptViewportProps> = ({
  children,
  scrollRef,
  stickyBottom = false,
}) => {
  const localRef = useRef<ScrollBoxHandle | null>(null);
  const { inputEmitter, setRawMode } = useStdin();
  const { rows } = useTerminalSize();

  const setRef = useCallback((handle: ScrollBoxHandle | null) => {
    localRef.current = handle;
    if (scrollRef) scrollRef.current = handle;
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

  const handleScrollInput = useCallback((event: InputEventLike) => {
    const { input, key } = event;
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
  }, [rows]);

  useLayoutEffect(() => {
    setRawMode(true);
    return () => setRawMode(false);
  }, [setRawMode]);

  useLayoutEffect(() => {
    inputEmitter.prependListener('input', handleScrollInput);
    return () => {
      inputEmitter.removeListener('input', handleScrollInput);
    };
  }, [handleScrollInput, inputEmitter]);

  return (
    <Box flexDirection="column" flexGrow={1} flexShrink={1}>
      <ScrollBox
        ref={setRef}
        flexDirection="column"
        flexGrow={1}
        flexShrink={1}
        stickyScroll={stickyBottom}
      >
        {children}
      </ScrollBox>
    </Box>
  );
};
