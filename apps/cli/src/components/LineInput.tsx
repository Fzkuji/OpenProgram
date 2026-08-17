import React, { useState } from 'react';
import { Box, Text, useInput } from '../runtime/index';
import { useColors } from '../theme/ThemeProvider.js';
import { usePanelWidth } from '../utils/useTerminalWidth.js';

export interface LineInputProps {
  /** Label shown above the input box. */
  label: string;
  /** Optional helper line under the label. */
  hint?: string;
  /** When true, each typed char renders as `•`. For tokens / passwords. */
  mask?: boolean;
  /** Initial value. */
  initial?: string;
  /** Called on Enter with the final value. */
  onSubmit: (value: string) => void;
  /** Called on Esc. */
  onCancel: () => void;
}

const printableInput = (input: string): string =>
  input.replace(/[\u0000-\u001f\u007f]/g, '');

const dropLastCodePoint = (value: string): string =>
  Array.from(value).slice(0, -1).join('');

/**
 * Single-line text input. Used by the channel-account register flow
 * (`/channel` → register → account_id → token).
 */
export const LineInput: React.FC<LineInputProps> = ({
  label, hint, mask, initial, onSubmit, onCancel,
}) => {
  const colors = useColors();
  const width = usePanelWidth();
  const [value, setValue] = useState<string>(initial ?? '');

  useInput((input, key) => {
    if (key.escape) {
      onCancel();
      return;
    }
    if (key.return) {
      onSubmit(value);
      return;
    }
    if (key.backspace || key.delete) {
      setValue(dropLastCodePoint);
      return;
    }
    if (input && !key.ctrl && !key.meta) {
      const text = printableInput(input);
      if (text) setValue((v) => v + text);
    }
  });

  const display = mask ? '•'.repeat(Array.from(value).length) : value;

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={colors.primary}
      paddingX={1}
      marginBottom={1}
      width={width}
    >
      <Text bold color={colors.primary}>{label}</Text>
      {hint ? <Text color={colors.muted}>{hint}</Text> : null}
      <Box>
        <Text color={colors.primary}>{'> '}</Text>
        <Text color={colors.text}>{display}</Text>
        <Text color={colors.primary}>█</Text>
      </Box>
      <Text color={colors.muted}>enter submit · esc cancel</Text>
    </Box>
  );
};
