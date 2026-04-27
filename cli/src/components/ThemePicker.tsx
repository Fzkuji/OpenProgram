import React, { useEffect, useState } from 'react';
import { Box, Text, useInput } from 'ink';
import { useColors, usePreviewTheme, useThemeSetting } from '../theme/ThemeProvider.js';
import { THEME_SETTINGS, THEME_LABELS, ThemeSetting } from '../theme/themes.js';
import { usePanelWidth } from '../utils/useTerminalWidth.js';

export interface ThemePickerProps {
  /** Called after the user confirms a choice (preview already saved). */
  onDone: (setting: ThemeSetting) => void;
  /** Called when the user cancels (preview already reverted). */
  onCancel: () => void;
}

/**
 * Live-preview theme selector. Arrow keys repaint the entire UI in the
 * highlighted theme; enter saves; esc reverts to the previously saved one.
 *
 * Modeled after Claude Code's ThemePicker — the value of "preview while
 * highlighting" is that the user can compare palettes against their actual
 * chat content (the messages still rendered behind the picker) instead of
 * guessing from a label.
 */
export const ThemePicker: React.FC<ThemePickerProps> = ({ onDone, onCancel }) => {
  const colors = useColors();
  const savedSetting = useThemeSetting();
  const { setPreviewTheme, savePreview, cancelPreview } = usePreviewTheme();
  const width = usePanelWidth();

  // Start the cursor on the saved setting so the user sees what's active.
  const initial = Math.max(0, THEME_SETTINGS.indexOf(savedSetting));
  const [index, setIndex] = useState(initial);

  // Push the highlighted setting as a preview so the surrounding UI repaints.
  useEffect(() => {
    const setting = THEME_SETTINGS[index];
    if (setting) setPreviewTheme(setting);
    // Cleanup on unmount: if we never confirmed/cancelled (e.g. process exit
    // mid-pick), drop the preview to avoid stranding a wrong palette in the
    // saved-setting renderer.
    return () => { /* explicit save/cancel handles the normal path */ };
  }, [index, setPreviewTheme]);

  useInput((_input, key) => {
    if (key.escape) {
      cancelPreview();
      onCancel();
      return;
    }
    if (key.return) {
      const setting = THEME_SETTINGS[index];
      savePreview();
      if (setting) onDone(setting);
      return;
    }
    if (key.upArrow) {
      setIndex((i) => (i - 1 + THEME_SETTINGS.length) % THEME_SETTINGS.length);
      return;
    }
    if (key.downArrow) {
      setIndex((i) => (i + 1) % THEME_SETTINGS.length);
      return;
    }
  });

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={colors.primary}
      paddingX={1}
      marginBottom={1}
      width={width}
    >
      <Box justifyContent="space-between">
        <Text bold color={colors.primary}>Theme</Text>
        <Text color={colors.muted}>
          arrow keys live-preview · enter save · esc revert
        </Text>
      </Box>
      <Box marginTop={1} flexDirection="column">
        <Text color={colors.muted}>
          The text below repaints in the highlighted theme — pick the one
          that reads cleanest against your terminal background.
        </Text>
      </Box>
      <Box marginTop={1} flexDirection="column">
        {THEME_SETTINGS.map((setting, i) => {
          const selected = i === index;
          const isSaved = setting === savedSetting;
          return (
            <Box key={setting}>
              <Text color={selected ? colors.primary : colors.border}>
                {selected ? '▌ ' : '  '}
              </Text>
              <Box width={Math.max(10, Math.floor(width / 4))}>
                <Text color={selected ? colors.primary : colors.text} bold={selected}>
                  {setting}
                </Text>
              </Box>
              <Text color={selected ? colors.text : colors.muted} wrap="truncate-end">
                {THEME_LABELS[setting]}
                {isSaved ? '  · saved' : ''}
              </Text>
            </Box>
          );
        })}
      </Box>
      <Box marginTop={1}>
        <Text color={colors.muted}>
          Saved → <Text color={colors.text}>{savedSetting}</Text>
        </Text>
      </Box>
    </Box>
  );
};
