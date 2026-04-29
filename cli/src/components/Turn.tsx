import React from 'react';
import { Box, Text } from '../runtime/index';
import { useColors } from '../theme/ThemeProvider.js';
import { renderMarkdown } from '../utils/markdown.js';

export type Role = 'user' | 'assistant' | 'system';

export interface ToolCall {
  id: string;
  tool: string;
  input?: string;
  result?: string;
  status: 'running' | 'done' | 'error';
}

/** A piece of an assistant turn — either a text segment or a tool call. */
export type TurnBlock =
  | { kind: 'text'; text: string }
  | { kind: 'tool'; call: ToolCall };

export interface Turn {
  id: string;
  role: Role;
  text: string;
  /**
   * Ordered sequence of text + tool blocks, matching the order they
   * arrived from the model. Renders interleaved so a tool call shows
   * exactly where the model emitted it instead of all tools clumping
   * after the text. Optional for back-compat with simple user / system
   * rows that only carry plain text.
   */
  blocks?: TurnBlock[];
  /** Legacy: flat list of tools (rendered after text). Used only when
   * `blocks` is not provided. */
  tools?: ToolCall[];
  tag?: string;
  /** While streaming, skip the markdown renderer (re-running it every
   * token gets expensive on long replies). */
  streaming?: boolean;
}

const ToolRow: React.FC<{ call: ToolCall }> = ({ call }) => {
  const colors = useColors();
  const arrow =
    call.status === 'running' ? '◌' : call.status === 'error' ? '✗' : '●';
  const color =
    call.status === 'running'
      ? colors.tool.running
      : call.status === 'error'
      ? colors.tool.error
      : colors.tool.done;
  return (
    <Box flexDirection="column" paddingLeft={2}>
      <Box>
        <Text color={color}>{arrow} </Text>
        <Text color={colors.text} bold>
          {call.tool}
        </Text>
        {call.input ? (
          <>
            <Text color={colors.muted}> · </Text>
            <Text color={colors.muted} wrap="truncate-end">
              {call.input}
            </Text>
          </>
        ) : null}
      </Box>
      {call.result ? (
        <Box paddingLeft={2}>
          <Text color={colors.border}>└ </Text>
          <Text color={colors.muted} wrap="truncate-end">
            {call.result.split('\n')[0] ?? ''}
            {call.result.includes('\n')
              ? `  (+${call.result.split('\n').length - 1} lines)`
              : ''}
          </Text>
        </Box>
      ) : null}
    </Box>
  );
};

const UserRow: React.FC<{ turn: Turn }> = ({ turn }) => {
  const colors = useColors();
  const lines = turn.text.split('\n');
  return (
    <Box marginBottom={1} flexDirection="column">
      {lines.map((line, i) => (
        <Box key={i} paddingX={1}>
          <Text backgroundColor={colors.user.bg} color={colors.user.fg}>
            {i === 0 ? '> ' : '  '}
            {line || ' '}
          </Text>
        </Box>
      ))}
    </Box>
  );
};

const TextSegment: React.FC<{ text: string; streaming?: boolean; showGlyph: boolean }>
  = ({ text, streaming, showGlyph }) => {
  const colors = useColors();
  const rendered = streaming ? text : renderMarkdown(text);
  const lines = rendered.split('\n');
  return (
    <Box paddingX={1} flexDirection="column">
      {lines.map((line, i) => {
        const isFirstLine = i === 0;
        return (
          <Box key={i}>
            {isFirstLine && showGlyph ? (
              <Text color={colors.assistant.glyph}>● </Text>
            ) : (
              <Text>  </Text>
            )}
            <Text>{line || ' '}</Text>
          </Box>
        );
      })}
    </Box>
  );
};

const AssistantRow: React.FC<{ turn: Turn }> = ({ turn }) => {
  // Pick blocks if present (new streaming pipeline). Fall back to the
  // legacy "text then tools" layout for older Turn shapes.
  const blocks: TurnBlock[] =
    turn.blocks && turn.blocks.length > 0
      ? turn.blocks
      : [
          ...(turn.text ? [{ kind: 'text' as const, text: turn.text }] : []),
          ...((turn.tools ?? []).map((t) => ({ kind: 'tool' as const, call: t }))),
        ];

  // Show the green leading ● only on the FIRST text block. Tool blocks
  // carry their own status glyph, so a redundant assistant glyph above
  // them just clutters the row. If there's no text at all (assistant
  // emitted only tool calls), no glyph is shown — the tool's own ◌/●
  // is enough to mark the assistant turn.
  const firstTextIndex = blocks.findIndex((b) => b.kind === 'text');

  return (
    <Box marginBottom={1} flexDirection="column">
      {blocks.map((block, i) => {
        if (block.kind === 'tool') {
          return <ToolRow key={`tool-${block.call.id}`} call={block.call} />;
        }
        return (
          <TextSegment
            key={`text-${i}`}
            text={block.text}
            streaming={turn.streaming}
            showGlyph={i === firstTextIndex}
          />
        );
      })}
    </Box>
  );
};

const SystemRow: React.FC<{ turn: Turn }> = ({ turn }) => {
  const colors = useColors();
  const lines = turn.text.split('\n');
  return (
    <Box marginBottom={1} paddingX={1} flexDirection="column">
      {lines.map((l, i) => (
        <Text key={i} color={colors.muted} italic>
          {l || ' '}
        </Text>
      ))}
    </Box>
  );
};

export const TurnRow: React.FC<{ turn: Turn }> = ({ turn }) => {
  if (turn.role === 'user') return <UserRow turn={turn} />;
  if (turn.role === 'assistant') return <AssistantRow turn={turn} />;
  return <SystemRow turn={turn} />;
};
