/**
 * Layout primitives — saving "I keep writing the same Box" cycles.
 *
 * <Stack>  — vertical flex with gap between children
 * <Row>    — horizontal flex with gap between children
 * <Spacer> — re-export from ink for symmetry; pushes neighbors apart
 * <Center> — center children both axes
 *
 * The kit's primary unit is "row" (one terminal cell tall). gap=1
 * means one blank cell between children, like CSS gap. Use these
 * instead of literal margin/padding props on bare Box — keeps
 * spacing consistent across screens.
 */
import React, { type ReactNode, Children, isValidElement, cloneElement } from 'react';
import { Box, Spacer as InkSpacer } from '../runtime/index';

export interface StackRowProps {
  children?: ReactNode;
  /** Cells between adjacent children. Default 0. */
  gap?: number;
  /** Set when the container should grow (e.g. inside a Shell flex
   *  parent that wants Stack to fill height). */
  flexGrow?: number;
  flexShrink?: number;
  paddingX?: number;
  paddingY?: number;
}

/**
 * Vertical stack. ``gap`` is implemented by inserting a spacer Box
 * between children rather than margin on each child — that way the
 * first/last child don't get extra space.
 */
export const Stack: React.FC<StackRowProps> = ({
  children, gap = 0, flexGrow, flexShrink, paddingX, paddingY,
}) => {
  const arr = Children.toArray(children).filter(Boolean);
  return (
    <Box
      flexDirection="column"
      flexGrow={flexGrow}
      flexShrink={flexShrink}
      paddingX={paddingX}
      paddingY={paddingY}
    >
      {arr.map((c, i) => (
        <React.Fragment key={isValidElement(c) ? (c.key ?? i) : i}>
          {c}
          {gap > 0 && i < arr.length - 1 ? <Box height={gap} /> : null}
        </React.Fragment>
      ))}
    </Box>
  );
};

export const Row: React.FC<StackRowProps> = ({
  children, gap = 0, flexGrow, flexShrink, paddingX, paddingY,
}) => {
  const arr = Children.toArray(children).filter(Boolean);
  return (
    <Box
      flexDirection="row"
      flexGrow={flexGrow}
      flexShrink={flexShrink}
      paddingX={paddingX}
      paddingY={paddingY}
    >
      {arr.map((c, i) => (
        <React.Fragment key={isValidElement(c) ? (c.key ?? i) : i}>
          {c}
          {gap > 0 && i < arr.length - 1 ? <Box width={gap} /> : null}
        </React.Fragment>
      ))}
    </Box>
  );
};

export { InkSpacer as Spacer };

export interface CenterProps {
  children?: ReactNode;
  /** Center along main axis only. Default: both. */
  axis?: 'both' | 'horizontal' | 'vertical';
  flexGrow?: number;
}

export const Center: React.FC<CenterProps> = ({ children, axis = 'both', flexGrow }) => (
  <Box
    flexDirection="column"
    flexGrow={flexGrow}
    alignItems={axis === 'both' || axis === 'horizontal' ? 'center' : 'stretch'}
    justifyContent={axis === 'both' || axis === 'vertical' ? 'center' : 'flex-start'}
  >
    {children}
  </Box>
);
