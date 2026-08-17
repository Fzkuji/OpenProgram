import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs';
import { homedir } from 'os';
import { join, dirname } from 'path';
import { DEFAULT_SETTING, isThemeSetting, ThemeSetting } from './themes.js';

const CONFIG_PATH = join(homedir(), '.openprogram', 'cli-config.json');

interface CliConfig {
  theme?: string;
}

const readConfig = (): CliConfig => {
  if (!existsSync(CONFIG_PATH)) return {};
  try {
    const raw = readFileSync(CONFIG_PATH, 'utf8');
    const parsed = JSON.parse(raw);
    return typeof parsed === 'object' && parsed ? (parsed as CliConfig) : {};
  } catch {
    return {};
  }
};

const writeConfig = (cfg: CliConfig): void => {
  const dir = dirname(CONFIG_PATH);
  if (!existsSync(dir)) {
    try { mkdirSync(dir, { recursive: true }); } catch { /* best effort */ }
  }
  try {
    writeFileSync(CONFIG_PATH, JSON.stringify(cfg, null, 2) + '\n');
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
