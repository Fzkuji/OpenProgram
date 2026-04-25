import React from 'react';
import { Box, Text } from 'ink';
import { FileMatch } from '../../utils/fileCompletions.js';
import { colors } from '../../theme/colors.js';

export interface FileMenuProps {
  items: FileMatch[];
  selectedIndex: number;
}

const MAX_VISIBLE = 8;

export const FileMenu: React.FC<FileMenuProps> = ({ items, selectedIndex }) => {
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
            <Text color={selected ? colors.primary : colors.text} bold={selected} wrap="truncate-end">
              {m.path}
              {m.isDir ? '/' : ''}
            </Text>
          </Box>
        );
      })}
      {items.length > MAX_VISIBLE ? (
        <Text color={colors.muted}>
          {selectedIndex + 1}/{items.length} · ↑↓ choose · enter / tab insert · esc cancel
        </Text>
      ) : null}
    </Box>
  );
};
