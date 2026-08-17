import chalk from 'chalk';
import type { ThemeColor } from './themes.js';
import { colorize } from '../runtime/ink/colorize.js';

export const paint = (color: ThemeColor) =>
  (text: string): string => colorize(text, color, 'foreground');

export const paintBold = (color: ThemeColor) =>
  (text: string): string => colorize(chalk.bold(text), color, 'foreground');
