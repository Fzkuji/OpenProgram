import { execFile } from 'child_process';
import type { ThemeName } from './themes.js';

export async function queryHostAppearanceTheme(): Promise<ThemeName | undefined> {
  if (process.platform !== 'darwin') return undefined;

  return new Promise<ThemeName | undefined>((resolve) => {
    execFile('defaults', ['read', '-g', 'AppleInterfaceStyle'], (error, stdout) => {
      if (!error && stdout.trim() === 'Dark') {
        resolve('dark');
        return;
      }
      resolve('light');
    });
  });
}
