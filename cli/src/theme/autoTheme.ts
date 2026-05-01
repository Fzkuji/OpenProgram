import type { TerminalQuerier } from '../runtime/ink/terminal-querier.js';
import type { ThemeName } from './themes.js';
import { queryHostAppearanceTheme } from './hostAppearance.js';
import { queryTerminalBg, queryTerminalBgWithQuerier } from './oscQuery.js';

export async function detectAutoTheme(
  querier: TerminalQuerier | null | undefined,
): Promise<ThemeName | undefined> {
  const terminalTheme = querier
    ? await queryTerminalBgWithQuerier(querier, 500)
    : await queryTerminalBg(250);
  return terminalTheme ?? await queryHostAppearanceTheme();
}
