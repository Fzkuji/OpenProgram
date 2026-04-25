import React from 'react';
import { Box, Text, Static } from 'ink';
import type { UIMessage } from '../screens/REPL.js';
import { colors } from '../theme/colors.js';

export interface MessagesProps {
  /** Frozen committed messages — Ink prints them once and never re-renders. */
  committed: UIMessage[];
  /** The currently-streaming assistant message, if any. Re-renders every delta. */
  streaming?: UIMessage | null;
}

const roleLabel = (role: UIMessage['role'], tag?: string): { label: string; color: string } => {
  if (role === 'user') return { label: tag ? `User (${tag})` : 'User', color: colors.primary };
  if (role === 'assistant') return { label: 'Assistant', color: colors.success };
  return { label: 'System', color: colors.muted };
};

const Row: React.FC<{ message: UIMessage }> = ({ message }) => {
  const { label, color } = roleLabel(message.role, message.tag);
  return (
    <Box flexDirection="column" marginBottom={1} paddingX={1}>
      <Text bold color={color}>
        {label}
      </Text>
      <Text>{message.text}</Text>
    </Box>
  );
};

export const Messages: React.FC<MessagesProps> = ({ committed, streaming }) => {
  return (
    <>
      <Static items={committed}>{(m) => <Row key={m.id} message={m} />}</Static>
      {streaming ? <Row message={streaming} /> : null}
    </>
  );
};
