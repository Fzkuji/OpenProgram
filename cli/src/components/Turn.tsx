import React from 'react';
import { Box, Text } from 'ink';
import { colors } from '../theme/colors.js';
import { renderMarkdown } from '../utils/markdown.js';

export type Role = 'user' | 'assistant' | 'system';

export interface ToolCall {
  id: string;
  tool: string;
  input?: string;
  result?: string;
  status: 'running' | 'done' | 'error';
}

export interface Turn {
  id: string;
  role: Role;
  text: string;
  /** Inline tool calls between text segments. Order is preserved. */
  tools?: ToolCall[];
  tag?: string;
}

const ToolRow: React.FC<{ call: ToolCall }> = ({ call }) => {
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
  // User message: gray background block, leading `>` glyph. Each visual
  // line is its own <Text> so newlines split correctly inside the block.
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

const AssistantRow: React.FC<{ turn: Turn }> = ({ turn }) => {
  const rendered = turn.text ? renderMarkdown(turn.text) : '';
  const lines = rendered.split('\n');
  return (
    <Box marginBottom={1} flexDirection="column">
      <Box paddingX={1} flexDirection="column">
        {lines.length > 0 && lines[0] ? (
          <Box>
            <Text color={colors.assistant.glyph}>● </Text>
            <Text>{lines[0]}</Text>
          </Box>
        ) : (
          <Box>
            <Text color={colors.assistant.glyph}>● </Text>
          </Box>
        )}
        {lines.slice(1).map((l, i) => (
          <Box key={i} paddingLeft={2}>
            <Text>{l || ' '}</Text>
          </Box>
        ))}
      </Box>
      {turn.tools && turn.tools.length > 0 ? (
        <Box flexDirection="column" marginTop={0}>
          {turn.tools.map((t) => (
            <ToolRow key={t.id} call={t} />
          ))}
        </Box>
      ) : null}
    </Box>
  );
};

const SystemRow: React.FC<{ turn: Turn }> = ({ turn }) => {
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
