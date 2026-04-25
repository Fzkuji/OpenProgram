import React from 'react';
import { Box, Text } from 'ink';
import type { SlashCommand } from '../../commands/registry.js';
import { colors } from '../../theme/colors.js';

export interface PromptInputHelpMenuProps {
  items: SlashCommand[];
  selectedIndex: number;
}

const MAX_VISIBLE = 8;

export const PromptInputHelpMenu: React.FC<PromptInputHelpMenuProps> = ({ items, selectedIndex }) => {
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
            <Box width={18}>
              <Text color={selected ? colors.primary : colors.text} bold={selected}>
                /{c.name}
              </Text>
            </Box>
            <Text color={selected ? colors.text : colors.muted}>{c.description}</Text>
          </Box>
        );
      })}
      {items.length > MAX_VISIBLE ? (
        <Text color={colors.muted}>
          {selectedIndex + 1}/{items.length} · ↑↓ to choose · enter / tab to insert
        </Text>
      ) : null}
    </Box>
  );
};
