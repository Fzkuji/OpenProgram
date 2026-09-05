import { homedir } from 'os';
import { join } from 'path';

/** Resolve the same per-profile state root as ``openprogram.paths``. */
export function tuiStateDir(
  environment: NodeJS.ProcessEnv = process.env,
  home: string = homedir(),
): string {
  const explicit = environment.OPENPROGRAM_STATE_DIR?.trim();
  if (explicit) return explicit;

  // Retain the former development override as a compatibility fallback, but
  // packaged launches always receive OPENPROGRAM_STATE_DIR from Python.
  const legacyOverride = environment.AGENTIC_DIR?.trim();
  if (legacyOverride) return legacyOverride;

  const profile = environment.OPENPROGRAM_PROFILE?.trim();
  return join(home, profile ? `.openprogram-${profile}` : '.openprogram');
}
