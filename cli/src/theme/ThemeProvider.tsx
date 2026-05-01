import React, { createContext, useContext, useState, useCallback, useMemo, useEffect } from 'react';
import { useStdin, useTerminalFocus } from '../runtime/index.js';
import { detectAutoTheme } from './autoTheme.js';
import { ColorTheme, getTheme, ThemeName, ThemeSetting } from './themes.js';
import { loadThemeSetting, saveThemeSetting } from './persistence.js';
import { getSystemThemeName, setCachedSystemTheme, subscribeSystemTheme } from './systemTheme.js';

interface ThemeContextShape {
  /** Saved preference. May be 'auto'. */
  themeSetting: ThemeSetting;
  /** Resolved name actually painted to screen. Never 'auto'. */
  currentTheme: ThemeName;
  /** Active palette. */
  colors: ColorTheme;
  /** Save a setting (also clears any preview). Persists to disk. */
  setThemeSetting: (setting: ThemeSetting) => void;
  /** Live-preview a setting without saving — repaints UI immediately. */
  setPreviewTheme: (setting: ThemeSetting) => void;
  /** Confirm the current preview as the saved setting. */
  savePreview: () => void;
  /** Drop the preview and restore the saved setting. */
  cancelPreview: () => void;
}

const fallback: ThemeContextShape = {
  themeSetting: 'dark',
  currentTheme: 'dark',
  colors: getTheme('dark'),
  setThemeSetting: () => {},
  setPreviewTheme: () => {},
  savePreview: () => {},
  cancelPreview: () => {},
};

const ThemeContext = createContext<ThemeContextShape | null>(null);
const AUTO_THEME_REFRESH_MS = 5000;

const resolve = (setting: ThemeSetting): ThemeName =>
  setting === 'auto' ? getSystemThemeName() : setting;

export const ThemeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [themeSetting, setThemeSettingState] = useState<ThemeSetting>(() => loadThemeSetting());
  const [previewSetting, setPreviewSetting] = useState<ThemeSetting | null>(null);
  const terminalFocused = useTerminalFocus();
  const { querier } = useStdin();
  // Bumps when the cached system theme changes (e.g. OSC 11 reply lands).
  // Used so React re-resolves `auto` without us threading state through.
  const [, setSystemTick] = useState(0);

  useEffect(
    () => subscribeSystemTheme(() => setSystemTick((n) => n + 1)),
    [],
  );

  // Preview wins while the picker is highlighting an option. Confirm or
  // cancel through the explicit save/cancel callbacks.
  const activeSetting = previewSetting ?? themeSetting;
  const currentTheme = resolve(activeSetting);

  useEffect(() => {
    if (activeSetting !== 'auto' || !terminalFocused) return;

    let cancelled = false;
    let inFlight = false;

    const refresh = () => {
      if (cancelled || inFlight) return;
      inFlight = true;
      detectAutoTheme(querier)
        .then((bg) => {
          if (!cancelled && bg) setCachedSystemTheme(bg);
        })
        .catch(() => {})
        .finally(() => {
          inFlight = false;
        });
    };

    refresh();
    const timer = setInterval(refresh, AUTO_THEME_REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [activeSetting, terminalFocused, querier]);

  const setThemeSetting = useCallback((setting: ThemeSetting) => {
    setThemeSettingState(setting);
    setPreviewSetting(null);
    saveThemeSetting(setting);
  }, []);

  const setPreviewTheme = useCallback((setting: ThemeSetting) => {
    setPreviewSetting(setting);
  }, []);

  const savePreview = useCallback(() => {
    setPreviewSetting((preview) => {
      if (preview === null) return null;
      setThemeSettingState(preview);
      saveThemeSetting(preview);
      return null;
    });
  }, []);

  const cancelPreview = useCallback(() => {
    setPreviewSetting(null);
  }, []);

  const value = useMemo<ThemeContextShape>(
    () => ({
      themeSetting,
      currentTheme,
      colors: getTheme(currentTheme),
      setThemeSetting,
      setPreviewTheme,
      savePreview,
      cancelPreview,
    }),
    [themeSetting, currentTheme, setThemeSetting, setPreviewTheme, savePreview, cancelPreview],
  );
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
};

export function useTheme(): ThemeContextShape {
  return useContext(ThemeContext) ?? fallback;
}

export function useColors(): ColorTheme {
  return useTheme().colors;
}

/** For UI that surfaces 'auto' as a distinct option. */
export function useThemeSetting(): ThemeSetting {
  return useTheme().themeSetting;
}

/** Live-preview controls used by ThemePicker. */
export function usePreviewTheme(): {
  setPreviewTheme: (s: ThemeSetting) => void;
  savePreview: () => void;
  cancelPreview: () => void;
} {
  const { setPreviewTheme, savePreview, cancelPreview } = useTheme();
  return { setPreviewTheme, savePreview, cancelPreview };
}
