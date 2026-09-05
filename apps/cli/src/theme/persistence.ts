import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs';
import { join, dirname } from 'path';
import { tuiStateDir } from '../utils/stateDir.js';
import { DEFAULT_SETTING, isThemeSetting, ThemeSetting } from './themes.js';

const configPath = (): string => join(tuiStateDir(), 'cli-config.json');

interface CliConfig {
  theme?: string;
}

const readConfig = (): CliConfig => {
  const path = configPath();
  if (!existsSync(path)) return {};
  try {
    const raw = readFileSync(path, 'utf8');
    const parsed = JSON.parse(raw);
    return typeof parsed === 'object' && parsed ? (parsed as CliConfig) : {};
  } catch {
    return {};
  }
};

const writeConfig = (cfg: CliConfig): void => {
  const path = configPath();
  const dir = dirname(path);
  if (!existsSync(dir)) {
    try { mkdirSync(dir, { recursive: true }); } catch { /* best effort */ }
  }
  try {
    writeFileSync(path, JSON.stringify(cfg, null, 2) + '\n');
  } catch { /* best effort */ }
};

export function loadThemeSetting(): ThemeSetting {
  const cfg = readConfig();
  if (cfg.theme && isThemeSetting(cfg.theme)) return cfg.theme;
  return DEFAULT_SETTING;
}

export function saveThemeSetting(setting: ThemeSetting): void {
  const cfg = readConfig();
  cfg.theme = setting;
  writeConfig(cfg);
}
