import React, { createContext, useContext } from 'react';
import { Box, Text } from '../../runtime/index';
import { useColors } from '../../theme/ThemeProvider.js';
import type { ColorTheme } from '../../theme/themes.js';
import { useKeybindings } from '../../keybindings/KeybindingsContext.js';
import type { ToolCall } from '../Turn.js';
import { toolView, type Tone, type ViewLine } from './toolView.js';

/** Whether tool output shows whole (the expand key toggles it for the
 *  whole transcript, as Pi's `app.tools.expand` does). */
export const ToolExpansionContext = createContext(false);

const toneColor = (tone: Tone, colors: ColorTheme) => {
  switch (tone) {
    case 'error':
    case 'remove': return colors.error;
    case 'add': return colors.success;
    case 'active': return colors.tool.running;
    case 'text': return colors.text;
    default: return colors.muted;
  }
};

const Line: React.FC<{ line: ViewLine; first: boolean }> = ({ line, first }) => {
  const colors = useColors();
  const gutter = <Text color={colors.border}>{first ? '⎿ ' : '  '}</Text>;
  if (line.kind === 'text') {
    return (
      <Box>
        {gutter}
        {line.bold ? (
          <Text color={toneColor(line.tone, colors)} bold strikethrough={line.strike} wrap="truncate-end">
            {line.text || ' '}
          </Text>
        ) : (
          <Text color={toneColor(line.tone, colors)} strikethrough={line.strike} wrap="truncate-end">
            {line.text || ' '}
          </Text>
        )}
      </Box>
    );
  }
  const { row, numberWidth } = line;
  if (row.kind === 'gap') {
    return (
      <Box>
        {gutter}
        <Text color={colors.border}>{`${' '.repeat(numberWidth)}  ⋮`}</Text>
      </Box>
    );
  }
  const no = row.kind === 'remove' ? row.oldNo : row.newNo;
  const number = numberWidth ? `${no === undefined ? '' : String(no)}`.padStart(numberWidth) + ' ' : '';
  const sign = row.kind === 'add' ? '+' : row.kind === 'remove' ? '-' : ' ';
  const color = row.kind === 'add' ? colors.success : row.kind === 'remove' ? colors.error : colors.muted;
  return (
    <Box>
      {gutter}
      <Text color={colors.border}>{number}</Text>
      <Text color={color} wrap="truncate-end">{`${sign} ${row.text}`}</Text>
    </Box>
  );
};

export const ToolRow: React.FC<{ call: ToolCall }> = ({ call }) => {
  const colors = useColors();
  const expanded = useContext(ToolExpansionContext);
  const { label } = useKeybindings();
  const view = toolView(call, { expanded, expandKey: label('app.toolsExpand') });
  const glyph = call.status === 'running' ? '◌' : call.status === 'error' ? '✗' : '●';
  const glyphColor =
    call.status === 'running' ? colors.tool.running
    : call.status === 'error' ? colors.tool.error
    : colors.tool.done;
  return (
    <Box flexDirection="column" paddingLeft={2}>
      <Box>
        <Text color={glyphColor}>{glyph} </Text>
        <Text color={colors.text} bold>{view.name}</Text>
        {view.arg ? (
          <Text color={colors.muted} wrap="truncate-end">({view.arg.replace(/\s*\n\s*/g, ' ')})</Text>
        ) : null}
      </Box>
      {view.lines.length ? (
        <Box flexDirection="column" paddingLeft={2}>
          {view.lines.map((line, i) => <Line key={i} line={line} first={i === 0} />)}
        </Box>
      ) : null}
    </Box>
  );
};
