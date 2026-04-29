import React, { useEffect, useState, useMemo } from 'react';
import { Box, Text, useInput } from '../../runtime/index';
import { PromptInputHelpMenu } from './PromptInputHelpMenu.js';
import { FileMenu } from './FileMenu.js';
import { SLASH_COMMANDS, SlashCommand } from '../../commands/registry.js';
import { fileCompletions, findAtToken, FileMatch } from '../../utils/fileCompletions.js';
import { usePanelWidth, useTerminalWidth } from '../../utils/useTerminalWidth.js';
import { useColors } from '../../theme/ThemeProvider.js';
import { stringWidth } from '../../runtime/ink/stringWidth.js';

export interface PromptInputProps {
  onSubmit: (text: string) => void;
  busy?: boolean;
  onSlashModeChange?: (slashMode: boolean) => void;
  /** Called when the user hits esc while busy — REPL sends a stop. */
  onCancel?: () => void;
  /** Past submissions for ↑/↓ recall (newest last). */
  history?: string[];
  /** Text injected by an external picker, then consumed into local input state. */
  initialDraft?: string;
  onDraftApplied?: () => void;
  /** Open cross-session context search. Receives the current draft. */
  onContextSearch?: (draft: string) => void;
}

const filterCommands = (filter: string): SlashCommand[] => {
  const needle = filter.replace(/^\//, '').toLowerCase();
  if (!needle) return SLASH_COMMANDS;
  return SLASH_COMMANDS.filter((c) => c.name.toLowerCase().includes(needle));
};

const visibleInput = (text: string): string => text.replace(/\n/g, '↵ ');

const clamp = (n: number, min: number, max: number): number =>
  Math.min(max, Math.max(min, n));

const sliceCells = (text: string, start: number, end: number): string => {
  if (end <= start) return '';
  let position = 0;
  let out = '';

  for (const char of text) {
    const width = Math.max(0, stringWidth(char));
    const next = position + width;
    if (next <= start) {
      position = next;
      continue;
    }
    if (position >= end) break;
    if (next > end) break;
    out += char;
    position = next;
  }

  return out;
};

interface InputViewport {
  prefix: boolean;
  before: string;
  cursor: string;
  after: string;
  suffix: boolean;
}

const buildInputViewport = (
  value: string,
  cursor: number,
  maxColumns: number,
): InputViewport => {
  const before = visibleInput(value.slice(0, cursor));
  const rawCursor = visibleInput(value.slice(cursor, cursor + 1));
  const cursorText = rawCursor.slice(0, 1) || ' ';
  const after = visibleInput(value.slice(cursor + 1));
  const cursorCol = stringWidth(before);
  const cursorWidth = Math.max(1, stringWidth(cursorText));
  const afterStart = cursorCol + cursorWidth;
  const totalWidth = afterStart + stringWidth(after);
  const columns = Math.max(1, maxColumns);

  if (totalWidth <= columns) {
    return { prefix: false, before, cursor: cursorText, after, suffix: false };
  }

  let markerColumns = 2;
  let start = 0;
  let end = columns;

  for (let i = 0; i < 4; i++) {
    const contentColumns = Math.max(cursorWidth, columns - markerColumns);
    start = clamp(
      cursorCol - Math.floor(contentColumns * 0.75),
      0,
      Math.max(0, totalWidth - contentColumns),
    );
    end = Math.min(totalWidth, start + contentColumns);
    const nextMarkerColumns = (start > 0 ? 1 : 0) + (end < totalWidth ? 1 : 0);
    if (nextMarkerColumns === markerColumns) break;
    markerColumns = nextMarkerColumns;
  }

  return {
    prefix: start > 0,
    before: sliceCells(before, start, cursorCol),
    cursor: cursorText,
    after: sliceCells(after, Math.max(0, start - afterStart), Math.max(0, end - afterStart)),
    suffix: end < totalWidth,
  };
};

export const PromptInput: React.FC<PromptInputProps> = ({
  onSubmit,
  busy,
  onSlashModeChange,
  onCancel,
  history,
  initialDraft,
  onDraftApplied,
  onContextSearch,
}) => {
  const colors = useColors();
  const [value, setValue] = useState('');
  const [cursor, setCursor] = useState(0);
  const [menuIndex, setMenuIndex] = useState(0);
  // -1 means we're not browsing history. 0..history.length-1 picks an entry.
  const [historyIndex, setHistoryIndex] = useState<number>(-1);
  const width = usePanelWidth();
  const cols = useTerminalWidth();
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

  useEffect(() => {
    if (initialDraft === undefined) return;
    setValue(initialDraft);
    setCursor(initialDraft.length);
    setHistoryIndex(-1);
    onDraftApplied?.();
  }, [initialDraft, onDraftApplied]);

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

    // Ctrl-R opens saved-context search. The old input-history search
    // remains covered by ↑/↓ recall and autosuggest.
    if (key.ctrl && input === 'r') {
      onContextSearch?.(value);
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

  const lineCount = value.length > 0 ? value.split('\n').length : 1;
  const rightHint =
    busy ? 'esc stop'
    : inFileMode ? 'tab insert'
    : inSlashMode ? 'tab fill'
    : suggestion ? '→ accept'
    : lineCount > 1 ? `${lineCount} lines`
    : 'enter';
  const showRightHint = cols >= 38;
  const placeholder =
    busy ? 'waiting for response'
    : 'message, /command, @file';
  const borderColor =
    busy ? colors.warning
    : inFileMode || inSlashMode ? colors.accent
    : colors.primary;
  const rightHintWidth = showRightHint ? stringWidth(rightHint) + 3 : 0;
  const inputAreaWidth = Math.max(8, cols - rightHintWidth - 8);
  const valueViewportWidth = Math.max(1, inputAreaWidth - 2);
  const inputViewport = buildInputViewport(value, cursor, valueViewportWidth);
  const inputViewportCells =
    (inputViewport.prefix ? 1 : 0) +
    stringWidth(inputViewport.before) +
    stringWidth(inputViewport.cursor) +
    stringWidth(inputViewport.after) +
    (inputViewport.suffix ? 1 : 0);
  const suggestionCells = Math.max(0, valueViewportWidth - inputViewportCells);
  const suggestionPreview = (() => {
    if (!suggestion || suggestionCells <= 1) return '';
    const visible = visibleInput(suggestion);
    if (stringWidth(visible) <= suggestionCells) return visible;
    return `${sliceCells(visible, 0, suggestionCells - 1)}…`;
  })();

  return (
    <Box flexDirection="column" width={width} flexShrink={0}>
      {inFileMode ? (
        <FileMenu items={fileMatches} selectedIndex={fileIndex} />
      ) : inSlashMode ? (
        <PromptInputHelpMenu items={matches} selectedIndex={menuIndex} />
      ) : null}
      <Box
        borderStyle="round"
        borderColor={borderColor}
        paddingX={1}
        justifyContent="space-between"
        flexShrink={0}
      >
        <Box flexShrink={0} width={inputAreaWidth}>
          <Text color={colors.primary}>{'> '}</Text>
          {value.length === 0 ? (
            <>
              <Text inverse>{' '}</Text>
              <Text color={colors.muted} wrap="truncate-end">{placeholder}</Text>
            </>
          ) : (
            <>
              {inputViewport.prefix ? <Text color={colors.border}>…</Text> : null}
              <Text>{inputViewport.before}</Text>
              <Text inverse>{inputViewport.cursor}</Text>
              <Text>{inputViewport.after}</Text>
              {inputViewport.suffix ? <Text color={colors.border}>…</Text> : null}
              {suggestionPreview ? (
                <Text color={colors.muted}>
                  {suggestionPreview}
                </Text>
              ) : null}
            </>
          )}
        </Box>
        {showRightHint ? (
          <Box flexShrink={0} marginLeft={2}>
            <Text color={busy ? colors.warning : colors.muted}>
              {rightHint}
            </Text>
          </Box>
        ) : null}
      </Box>
    </Box>
  );
};
