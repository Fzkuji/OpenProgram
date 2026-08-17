import React from 'react';
import { Box, Text } from '../../runtime/index';
import type { SlashCommand } from '../../commands/registry.js';
import { useColors } from '../../theme/ThemeProvider.js';
import { usePanelWidth } from '../../utils/useTerminalWidth.js';

export interface PromptInputHelpMenuProps {
  items: SlashCommand[];
  selectedIndex: number;
}

const MAX_VISIBLE = 8;

export const PromptInputHelpMenu: React.FC<PromptInputHelpMenuProps> = ({ items, selectedIndex }) => {
  const colors = useColors();
  const width = usePanelWidth();
  const labelWidth = Math.max(10, Math.min(20, Math.floor(width * 0.28)));
  const descWidth = Math.max(10, width - labelWidth - 7);
  const footer = width >= 70
    ? `${selectedIndex + 1}/${items.length} · ↑↓ choose · enter run · tab fill`
    : `${selectedIndex + 1}/${items.length} · enter/tab`;
  if (items.length === 0) {
    return (
      <Box paddingX={1}>
        <Text color={colors.muted}>(no matching commands)</Text>
      </Box>
    );
  }

  // Window the visible items around selectedIndex so long lists scroll.
  const half = Math.floor(MAX_VISIBLE / 2);
  let start = Math.max(0, selectedIndex - half);
  const end = Math.min(items.length, start + MAX_VISIBLE);
  if (end - start < MAX_VISIBLE) start = Math.max(0, end - MAX_VISIBLE);
  const visible = items.slice(start, end);

  return (
    <Box flexDirection="column" paddingX={1}>
      {visible.map((c, i) => {
        const idx = start + i;
        const selected = idx === selectedIndex;
        const arrow = selected ? '▌' : ' ';
        return (
          <Box key={c.name}>
            <Text color={selected ? colors.primary : colors.border}>{arrow} </Text>
            <Box width={labelWidth}>
              <Text color={selected ? colors.primary : colors.text} bold={selected}>
                /{c.name}
              </Text>
            </Box>
            <Box width={descWidth}>
              <Text color={selected ? colors.text : colors.muted} wrap="truncate-end">
                {c.description}
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
