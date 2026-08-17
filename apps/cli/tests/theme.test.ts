import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'fs';
import { themeFromRgb } from '../src/theme/oscQuery.js';
import { getTheme } from '../src/theme/themes.js';

const listFiles = (dir: string): string[] => {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const path = `${dir}/${entry}`;
    if (statSync(path).isDirectory()) {
      out.push(...listFiles(path));
    } else if (/\.[tj]sx?$/.test(path)) {
      out.push(path);
    }
  }
  return out;
};

describe('theme palettes', () => {
  it('uses balanced semantic accents for dark and light modes', () => {
    expect(getTheme('dark')).toMatchObject({
      primary: '#d97757',
      accent: '#c6613f',
      text: undefined,
      muted: 'ansi:blackBright',
      welcome: {
        appTitle: '#d97757',
        sectionCount: '#d97757',
        sectionTitle: undefined,
      },
      bottomBar: {
        effortXhigh: '#991b1b',
      },
    });
    expect(getTheme('light')).toMatchObject({
      primary: '#d97757',
      accent: '#c6613f',
      muted: 'ansi:blackBright',
      text: undefined,
      welcome: {
        appTitle: '#d97757',
        sectionCount: '#d97757',
        sectionTitle: undefined,
      },
      bottomBar: {
        effortXhigh: '#991b1b',
      },
    });
  });

  it('resolves auto to dim variants on non-extreme terminal backgrounds', () => {
    expect(themeFromRgb({ r: 0, g: 0, b: 0 })).toBe('dark');
    expect(themeFromRgb({ r: 0.25, g: 0.25, b: 0.25 })).toBe('dark-dim');
    expect(themeFromRgb({ r: 0.7, g: 0.7, b: 0.7 })).toBe('light-dim');
    expect(themeFromRgb({ r: 1, g: 1, b: 1 })).toBe('light');
  });

  it('refreshes the resolved auto theme after startup', () => {
    const source = readFileSync('src/theme/ThemeProvider.tsx', 'utf8');
    expect(source).toContain("activeSetting !== 'auto'");
    expect(source).toContain('detectAutoTheme(querier)');
    expect(source).toContain('setCachedSystemTheme(bg)');
    expect(source).not.toContain('setInterval');
  });

  it('keeps normal UI component colors in theme tokens', () => {
    const files = [
      ...listFiles('src/components'),
      ...listFiles('src/screens'),
      ...listFiles('src/ui'),
    ];
    const offenders = files.flatMap((file) => {
      const source = readFileSync(file, 'utf8');
      return /#[0-9a-fA-F]{3,8}|(?:backgroundColor|color)="ansi:/.test(source) ? [file] : [];
    });
    expect(offenders).toEqual([]);
  });

  it('does not require theme tokens to be hex colors', () => {
    const files = [
      ...listFiles('src/components'),
      ...listFiles('src/screens'),
      ...listFiles('src/ui'),
    ];
    const offenders = files.flatMap((file) => {
      const source = readFileSync(file, 'utf8');
      return /chalk\.(?:hex|bgHex)\(\s*colors\./.test(source) ? [file] : [];
    });
    expect(offenders).toEqual([]);
  });
});
