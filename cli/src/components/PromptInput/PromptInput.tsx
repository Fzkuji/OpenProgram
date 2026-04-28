import React, { useEffect, useState, useMemo } from 'react';
import { Box, Text, useInput } from '@openprogram/ink';
import { PromptInputHelpMenu } from './PromptInputHelpMenu.js';
import { FileMenu } from './FileMenu.js';
import { SLASH_COMMANDS, SlashCommand } from '../../commands/registry.js';
import { fileCompletions, findAtToken, FileMatch } from '../../utils/fileCompletions.js';
import { usePanelWidth } from '../../utils/useTerminalWidth.js';
import { useColors } from '../../theme/ThemeProvider.js';

export interface PromptInputProps {
  onSubmit: (text: string) => void;
  busy?: boolean;
  onSlashModeChange?: (slashMode: boolean) => void;
  /** Called when the user hits esc while busy — REPL sends a stop. */
  onCancel?: () => void;
  /** Past submissions for ↑/↓ recall (newest last). */
  history?: string[];
}

const filterCommands = (filter: string): SlashCommand[] => {
  const needle = filter.replace(/^\//, '').toLowerCase();
  if (!needle) return SLASH_COMMANDS;
  return SLASH_COMMANDS.filter((c) => c.name.toLowerCase().includes(needle));
};

const bestSearchMatch = (history: string[], term: string): string | null => {
  if (history.length === 0) return null;
  const t = term.toLowerCase();
  if (!t) return history[history.length - 1] ?? null;
  for (let i = history.length - 1; i >= 0; i--) {
    if ((history[i] ?? '').toLowerCase().includes(t)) return history[i] ?? null;
  }
  return null;
};

export const PromptInput: React.FC<PromptInputProps> = ({
  onSubmit,
  busy,
  onSlashModeChange,
  onCancel,
  history,
}) => {
  const colors = useColors();
  const [value, setValue] = useState('');
  const [cursor, setCursor] = useState(0);
  const [menuIndex, setMenuIndex] = useState(0);
  // -1 means we're not browsing history. 0..history.length-1 picks an entry.
  const [historyIndex, setHistoryIndex] = useState<number>(-1);
  const width = usePanelWidth();
  // ctrl+r reverse search state.
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');

  const inSlashMode = value.startsWith('/');
  const matches = useMemo(() => (inSlashMode ? filterCommands(value) : []), [value, inSlashMode]);

  // Fish-shell style autosuggest: when the current input is a prefix of
  // a past submission, show the rest in dim gray after the cursor.
  // → / End / ctrl-e accepts. New keystrokes that don't match the
  // suggestion silently update / drop it.
  const suggestion = useMemo<string | null>(() => {
    if (!value || !history || history.length === 0) return null;
    if (cursor !== value.length) return null;
    if (inSlashMode) return null; // slash menu has its own popup
    for (let i = history.length - 1; i >= 0; i--) {
      const h = history[i] ?? '';
      if (h !== value && h.startsWith(value)) return h.slice(value.length);
    }
    return null;
  }, [value, cursor, history, inSlashMode]);

  // Detect an "@partial" token before the cursor — when present we open
  // the file completion menu and drive it with ↑↓/tab/enter.
  const atToken = useMemo(() => findAtToken(value, cursor), [value, cursor]);
  const fileMatches = useMemo<FileMatch[]>(() => {
    if (!atToken) return [];
    try {
      return fileCompletions(atToken.partial);
    } catch {
      return [];
    }
  }, [atToken]);
  const [fileIndex, setFileIndex] = useState(0);
  useEffect(() => {
    if (fileIndex >= fileMatches.length) setFileIndex(0);
  }, [fileMatches.length, fileIndex]);
  const inFileMode = atToken !== null && fileMatches.length > 0;

  useEffect(() => {
    if (menuIndex >= matches.length) setMenuIndex(0);
  }, [matches.length, menuIndex]);

  useEffect(() => {
    onSlashModeChange?.(inSlashMode && matches.length > 0);
  }, [inSlashMode, matches.length, onSlashModeChange]);

  const submitText = (text: string) => {
    if (busy || !text.trim()) return;
    setValue('');
    setCursor(0);
    setMenuIndex(0);
    setHistoryIndex(-1);
    onSubmit(text);
  };

  useInput((input, key) => {
    // While the agent is busy, esc cancels the in-flight turn.
    if (busy) {
      if (key.escape) onCancel?.();
      return;
    }

    // Reverse search has its own little modal — handles its own keys.
    if (searchOpen) {
      if (key.escape) {
        setSearchOpen(false);
        setSearchTerm('');
        return;
      }
      if (key.return) {
        // Pull the current best match into the input and close search.
        const match = bestSearchMatch(history ?? [], searchTerm);
        if (match) {
          setValue(match);
          setCursor(match.length);
        }
        setSearchOpen(false);
        setSearchTerm('');
        return;
      }
      if (key.backspace || key.delete) {
        setSearchTerm((s) => s.slice(0, -1));
        return;
      }
      if (key.ctrl && input === 'r') {
        // Walk to the next older match (basic — we keep the term, the
        // helper just returns the most recent for now).
        return;
      }
      if (input && !key.ctrl && !key.meta) {
        setSearchTerm((s) => s + input);
      }
      return;
    }

    // ctrl+r enters reverse search mode.
    if (key.ctrl && input === 'r') {
      setSearchOpen(true);
      setSearchTerm('');
      return;
    }

    // File-completion navigation: when an @partial is at the cursor and
    // we have matches, ↑↓/tab/enter drive the file menu.
    if (inFileMode && atToken) {
      if (key.upArrow) {
        setFileIndex((i) => (i - 1 + fileMatches.length) % fileMatches.length);
        return;
      }
      if (key.downArrow) {
        setFileIndex((i) => (i + 1) % fileMatches.length);
        return;
      }
      if (key.tab || key.return) {
        const pick = fileMatches[fileIndex]!;
        const before = value.slice(0, atToken.start);
        const after = value.slice(cursor);
        const insertText = `@${pick.path}${pick.isDir ? '/' : ''} `;
        const next = before + insertText + after;
        setValue(next);
        setCursor(before.length + insertText.length);
        return;
      }
      if (key.escape) {
        // Drop the @partial so the menu closes.
        const before = value.slice(0, atToken.start);
        const after = value.slice(cursor);
        setValue(before + after);
        setCursor(before.length);
        return;
      }
    }

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
      // alt+enter inserts a newline; plain enter submits.
      if (key.meta) {
        setValue((v) => v.slice(0, cursor) + '\n' + v.slice(cursor));
        setCursor((c) => c + 1);
        return;
      }
      submitText(value);
      return;
    }
    if (key.escape) {
      setValue('');
      setCursor(0);
      setMenuIndex(0);
      setHistoryIndex(-1);
      return;
    }
    // History recall: ↑ on an empty/inactive line walks backwards through
    // past submissions, ↓ walks forward toward the live input.
    if (key.upArrow && history && history.length > 0) {
      const next = historyIndex < 0 ? history.length - 1 : Math.max(0, historyIndex - 1);
      setHistoryIndex(next);
      const v = history[next] ?? '';
      setValue(v);
      setCursor(v.length);
      return;
    }
    if (key.downArrow && history && historyIndex >= 0) {
      const next = historyIndex + 1;
      if (next >= history.length) {
        setHistoryIndex(-1);
        setValue('');
        setCursor(0);
      } else {
        setHistoryIndex(next);
        const v = history[next] ?? '';
        setValue(v);
        setCursor(v.length);
      }
      return;
    }
    if (key.leftArrow) {
      setCursor((c) => Math.max(0, c - 1));
      return;
    }
    if (key.rightArrow) {
      // At end-of-line with a suggestion, → accepts the rest.
      if (cursor === value.length && suggestion) {
        const next = value + suggestion;
        setValue(next);
        setCursor(next.length);
        setHistoryIndex(-1);
        return;
      }
      setCursor((c) => Math.min(value.length, c + 1));
      return;
    }
    // ctrl+E accepts the autosuggest (mnemonic: end-of-line / accept).
    if (key.ctrl && input === 'e' && suggestion) {
      const next = value + suggestion;
      setValue(next);
      setCursor(next.length);
      setHistoryIndex(-1);
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
      setHistoryIndex(-1);
      setValue((v) => v.slice(0, cursor) + input + v.slice(cursor));
      setCursor((c) => c + input.length);
    }
  });

  // Render input with a visible cursor caret at `cursor`.
  const before = value.slice(0, cursor);
  const at = value.slice(cursor, cursor + 1);
  const after = value.slice(cursor + 1);

  return (
    <Box flexDirection="column" width={width}>
      {searchOpen ? (
        <Box paddingX={1}>
          <Text color={colors.warning}>(reverse-i-search)</Text>
          <Text color={colors.muted}>: </Text>
          <Text>{searchTerm}</Text>
          <Text color={colors.border}>  → </Text>
          <Text color={colors.muted}>
            {bestSearchMatch(history ?? [], searchTerm) ?? '(no match)'}
          </Text>
        </Box>
      ) : inFileMode ? (
        <FileMenu items={fileMatches} selectedIndex={fileIndex} />
      ) : inSlashMode ? (
        <PromptInputHelpMenu items={matches} selectedIndex={menuIndex} />
      ) : null}
      <Box
        borderStyle="round"
        borderColor={busy ? colors.warning : colors.primary}
        // paddingX={2} matches Welcome's paddingX so ``↵ enter`` lands
        // in the same column as Welcome's ``agent · model`` (both right
        // edges at width-3). Was 1 before; visually misaligned by 1 col.
        paddingX={2}
        justifyContent="space-between"
      >
        <Box flexShrink={1}>
          <Text color={colors.primary}>{'> '}</Text>
          {value.length === 0 ? (
            // Empty state: gray placeholder hint with the cursor sitting at
            // the very start. ↵ glyph still rendered on the right.
            <>
              <Text inverse>{' '}</Text>
              <Text color={colors.muted}>type / for commands</Text>
            </>
          ) : (
            <>
              <Text>{before}</Text>
              <Text inverse>{at || ' '}</Text>
              <Text>{after}</Text>
              {suggestion ? (
                <Text color={colors.muted}>
                  {suggestion}
                </Text>
              ) : null}
            </>
          )}
        </Box>
        <Box flexShrink={0} marginLeft={2}>
          <Text color={colors.muted}>
            ↵ enter
          </Text>
        </Box>
      </Box>
    </Box>
  );
};
