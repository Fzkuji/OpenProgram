import type { TerminalQuerier } from '../runtime/ink/terminal-querier.js';
import type { ThemeName } from './themes.js';
import { queryTerminalBg, queryTerminalBgWithQuerier } from './oscQuery.js';

export async function detectAutoTheme(
  querier: TerminalQuerier | null | undefined,
): Promise<ThemeName | undefined> {
  return querier
    ? await queryTerminalBgWithQuerier(querier, 500)
    : await queryTerminalBg(250);
}
