import React from 'react';
import { Box, Text } from '../../runtime/index';
import { FileMatch } from '../../utils/fileCompletions.js';
import { useColors } from '../../theme/ThemeProvider.js';
import { usePanelWidth } from '../../utils/useTerminalWidth.js';

export interface FileMenuProps {
  items: FileMatch[];
  selectedIndex: number;
}

const MAX_VISIBLE = 8;

export const FileMenu: React.FC<FileMenuProps> = ({ items, selectedIndex }) => {
  const colors = useColors();
  const width = usePanelWidth();
  const pathWidth = Math.max(10, width - 4);
  const footer = width >= 70
    ? `${selectedIndex + 1}/${items.length} · ↑↓ choose · enter/tab insert · esc cancel`
    : `${selectedIndex + 1}/${items.length} · enter/tab`;
  if (items.length === 0) {
    return (
      <Box paddingX={1}>
        <Text color={colors.muted}>(no matching files)</Text>
      </Box>
    );
  }

  const half = Math.floor(MAX_VISIBLE / 2);
  let start = Math.max(0, selectedIndex - half);
  const end = Math.min(items.length, start + MAX_VISIBLE);
  if (end - start < MAX_VISIBLE) start = Math.max(0, end - MAX_VISIBLE);
  const visible = items.slice(start, end);

  return (
    <Box flexDirection="column" paddingX={1}>
      {visible.map((m, i) => {
        const idx = start + i;
        const selected = idx === selectedIndex;
        return (
          <Box key={m.path}>
            <Text color={selected ? colors.primary : colors.border}>
              {selected ? '▌ ' : '  '}
            </Text>
            <Box width={pathWidth}>
              <Text color={selected ? colors.primary : colors.text} bold={selected} wrap="truncate-end">
                {m.path}
                {m.isDir ? '/' : ''}
              </Text>
            </Box>
          </Box>
        );
      })}
      {items.length > MAX_VISIBLE ? (
        <Text color={colors.muted}>{footer}</Text>
      ) : null}
    </Box>
  );
};
