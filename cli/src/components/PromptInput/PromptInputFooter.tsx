import React from 'react';
import { Box, Text } from 'ink';
import { colors } from '../../theme/colors.js';

export interface PromptInputFooterProps {
  inSlashMode?: boolean;
}

export const PromptInputFooter: React.FC<PromptInputFooterProps> = ({ inSlashMode }) => {
  return (
    <Box paddingX={1}>
      {inSlashMode ? (
        <Text color={colors.muted}>
          ↑↓ choose · enter run · tab fill · esc cancel · ctrl+c quit
        </Text>
      ) : (
        <Text color={colors.muted}>/ commands · enter send · ctrl+c quit</Text>
      )}
    </Box>
  );
};
