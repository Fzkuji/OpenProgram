import { join } from 'path';
import { describe, expect, it } from 'vitest';

import { tuiStateDir } from '../src/utils/stateDir.js';

describe('TUI state directory', () => {
  it('prefers the authoritative state directory from the Python launcher', () => {
    expect(tuiStateDir({
      OPENPROGRAM_STATE_DIR: '/srv/openprogram-state',
      OPENPROGRAM_PROFILE: 'ignored',
    }, '/home/user')).toBe('/srv/openprogram-state');
  });

  it('isolates direct Node launches by profile', () => {
    expect(tuiStateDir({ OPENPROGRAM_PROFILE: 'linux-check' }, '/home/user'))
      .toBe(join('/home/user', '.openprogram-linux-check'));
    expect(tuiStateDir({}, '/home/user'))
      .toBe(join('/home/user', '.openprogram'));
  });
});
