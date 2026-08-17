/**
 * <Alert> — inline notice with variant styling.
 *
 * Used for: error / warning / info / success messages that should
 * sit visually distinct from regular transcript content but stay
 * inline (not a modal, not a transient toast).
 *
 *     <Alert variant="error">QR login failed: connection refused</Alert>
 *     <Alert variant="success" title="Bound!">…</Alert>
 *
 * Frame mirrors <Card>: bordered Box. Border color and icon prefix
 * vary by variant.
 */
import React, { type ReactNode } from 'react';
import { Box, Text } from '../runtime/index';
import { useColors } from '../theme/ThemeProvider.js';

export type AlertVariant = 'info' | 'success' | 'warning' | 'error';

export interface AlertProps {
  variant?: AlertVariant;
  /** Optional title row above the body. */
  title?: string;
  /** Width override; defaults to "fill the parent flex container". */
  width?: number;
  children?: ReactNode;
}

const ICONS: Record<AlertVariant, string> = {
  info: 'ℹ',
  success: '✓',
  warning: '⚠',
  error: '✗',
};

export const Alert: React.FC<AlertProps> = ({
  variant = 'info', title, width, children,
}) => {
  const colors = useColors();
  const color =
    variant === 'error'   ? colors.error :
    variant === 'warning' ? colors.warning :
    variant === 'success' ? colors.success :
                            colors.primary;
  return (
    <Box
      flexDirection="row"
      borderStyle="single"
      borderColor={color}
      paddingX={1}
      width={width}
    >
      <Box marginRight={1}>
        <Text color={color}>{ICONS[variant]}</Text>
      </Box>
      <Box flexDirection="column" flexGrow={1}>
        {title ? <Text bold color={color}>{title}</Text> : null}
        {typeof children === 'string'
          ? <Text color={colors.text}>{children}</Text>
          : children}
      </Box>
    </Box>
  );
};
