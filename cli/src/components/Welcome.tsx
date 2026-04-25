import React from 'react';
import { Box, Text } from 'ink';
import { colors } from '../theme/colors.js';

export const Welcome: React.FC = () => {
  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={colors.primary}
      paddingX={2}
      paddingY={0}
      marginBottom={1}
    >
      <Text bold color={colors.primary}>
        OpenProgram
      </Text>
      <Text color={colors.muted}>
        Type a message and press enter, or <Text color={colors.primary}>/</Text> to see commands.
      </Text>
      <Text color={colors.muted}>
        <Text color={colors.primary}>ctrl+c</Text> quits ·{' '}
        <Text color={colors.primary}>/help</Text> for full help
      </Text>
    </Box>
  );
};
