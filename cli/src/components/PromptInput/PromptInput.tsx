import React, { useEffect, useState, useMemo } from 'react';
import { Box, Text, useInput } from 'ink';
import { PromptInputHelpMenu } from './PromptInputHelpMenu.js';
import { PromptInputFooter } from './PromptInputFooter.js';
import { SLASH_COMMANDS, SlashCommand } from '../../commands/registry.js';
import { colors } from '../../theme/colors.js';

export interface PromptInputProps {
  onSubmit: (text: string) => void;
  busy?: boolean;
}

const filterCommands = (filter: string): SlashCommand[] => {
  const needle = filter.replace(/^\//, '').toLowerCase();
  if (!needle) return SLASH_COMMANDS;
  return SLASH_COMMANDS.filter((c) => c.name.toLowerCase().includes(needle));
};

export const PromptInput: React.FC<PromptInputProps> = ({ onSubmit, busy }) => {
  const [value, setValue] = useState('');
  const [cursor, setCursor] = useState(0);
  const [menuIndex, setMenuIndex] = useState(0);

  const inSlashMode = value.startsWith('/');
  const matches = useMemo(() => (inSlashMode ? filterCommands(value) : []), [value, inSlashMode]);

  useEffect(() => {
    if (menuIndex >= matches.length) setMenuIndex(0);
  }, [matches.length, menuIndex]);

  const submitText = (text: string) => {
    if (busy || !text.trim()) return;
    setValue('');
    setCursor(0);
    setMenuIndex(0);
    onSubmit(text);
  };

  useInput((input, key) => {
    if (busy) return;

    // Slash-menu navigation has priority when active.
    if (inSlashMode && matches.length > 0) {
      if (key.upArrow) {
        setMenuIndex((i) => (i - 1 + matches.length) % matches.length);
        return;
      }
      if (key.downArrow) {
        setMenuIndex((i) => (i + 1) % matches.length);
        return;
      }
      if (key.tab) {
        const cmd = matches[menuIndex]!;
        const next = `/${cmd.name} `;
        setValue(next);
        setCursor(next.length);
        return;
      }
      if (key.return) {
        const cmd = matches[menuIndex]!;
        // If the user has only typed `/foo` (no trailing space/args), running
        // the command means submitting `/foo`. If they've typed `/foo bar`,
        // submit the whole line.
        const trimmed = value.trim();
        const exactMatch = trimmed === `/${cmd.name}` || trimmed.startsWith(`/${cmd.name} `);
        const toSend = exactMatch ? value : `/${cmd.name}`;
        submitText(toSend);
        return;
      }
    }

    if (key.return) {
      submitText(value);
      return;
    }
    if (key.escape) {
      setValue('');
      setCursor(0);
      setMenuIndex(0);
      return;
    }
    if (key.leftArrow) {
      setCursor((c) => Math.max(0, c - 1));
      return;
    }
    if (key.rightArrow) {
      setCursor((c) => Math.min(value.length, c + 1));
      return;
    }
    if (key.backspace || key.delete) {
      if (cursor === 0) return;
      setValue((v) => v.slice(0, cursor - 1) + v.slice(cursor));
      setCursor((c) => Math.max(0, c - 1));
      return;
    }
    // Plain character insert. Filter out control chars.
    if (input && !key.ctrl && !key.meta) {
      setValue((v) => v.slice(0, cursor) + input + v.slice(cursor));
      setCursor((c) => c + input.length);
    }
  });

  // Render input with a visible cursor caret at `cursor`.
  const before = value.slice(0, cursor);
  const at = value.slice(cursor, cursor + 1);
  const after = value.slice(cursor + 1);

  return (
    <Box flexDirection="column">
      {inSlashMode ? (
        <PromptInputHelpMenu items={matches} selectedIndex={menuIndex} />
      ) : null}
      <Box
        borderStyle="round"
        borderColor={busy ? colors.warning : colors.primary}
        paddingX={1}
      >
        <Text color={colors.primary}>{'> '}</Text>
        <Text>{before}</Text>
        <Text inverse>{at || ' '}</Text>
        <Text>{after}</Text>
      </Box>
      <PromptInputFooter inSlashMode={inSlashMode && matches.length > 0} />
    </Box>
  );
};
