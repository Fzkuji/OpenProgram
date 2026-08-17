/**
 * Surface components — bordered containers that look "like a UI box".
 *
 * <Card>   — light bordered rectangle, optional title row
 * <Panel>  — heavier outer frame; same API as Card but visually
 *            stronger. Used for dialogs that should look "primary".
 *
 * The visual difference: Card uses ``borderStyle="single"`` (default
 * theme border color), Panel uses ``borderStyle="round"`` and the
 * theme's primary color. Both apply paddingX=1, paddingY=0.
 *
 * For inline notices (errors, warnings, info), use <Alert> — same
 * frame plus a colored variant + icon prefix.
 */
import React, { type ReactNode } from 'react';
import { Box, Text, type Color } from '../runtime/index';
import { useColors } from '../theme/ThemeProvider.js';

export interface CardProps {
  children?: ReactNode;
  /** Optional title row inside the border. */
  title?: string;
  /** Force a fixed width. Default: shrink to content. */
  width?: number;
  /** flex shrink/grow if Card sits inside a flex parent. */
  flexShrink?: number;
  flexGrow?: number;
  /** Override border color. Defaults to theme.muted. */
  borderColor?: Color;
}

export const Card: React.FC<CardProps> = ({
  children, title, width, flexShrink, flexGrow, borderColor,
}) => {
  const colors = useColors();
  return (
    <Box
      flexDirection="column"
      borderStyle="single"
      borderColor={borderColor ?? colors.border}
      paddingX={1}
      paddingY={0}
      width={width}
      flexShrink={flexShrink}
      flexGrow={flexGrow}
    >
      {title ? <Text bold color={colors.text}>{title}</Text> : null}
      {children}
    </Box>
  );
};

export const Panel: React.FC<CardProps> = ({
  children, title, width, flexShrink, flexGrow, borderColor,
}) => {
  const colors = useColors();
  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={borderColor ?? colors.primary}
      paddingX={1}
      paddingY={0}
      width={width}
      flexShrink={flexShrink}
      flexGrow={flexGrow}
    >
      {title ? <Text bold color={colors.primary}>{title}</Text> : null}
      {children}
    </Box>
  );
};
