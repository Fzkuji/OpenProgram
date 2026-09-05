import { describe, expect, it } from 'vitest';

import { readFileSync } from 'fs';

import { clipboardCommands } from '../src/utils/clipboard.js';

describe('Linux clipboard routing', () => {
  it('uses OSC 52 rather than a remote graphical clipboard over SSH', () => {
    expect(clipboardCommands('linux', {
      SSH_CONNECTION: 'client 123 server 22',
      DISPLAY: 'localhost:10.0',
      WAYLAND_DISPLAY: 'wayland-0',
    })).toEqual([]);
  });

  it('only probes clipboard tools backed by the active Linux display', () => {
    expect(clipboardCommands('linux', {})).toEqual([]);
    expect(clipboardCommands('linux', { WAYLAND_DISPLAY: 'wayland-0' }))
      .toEqual([['wl-copy', []]]);
    expect(clipboardCommands('linux', { DISPLAY: ':0' }))
      .toEqual([
        ['xclip', ['-selection', 'clipboard']],
        ['xsel', ['--clipboard', '--input']],
      ]);
  });

  it('uses the Windows host clipboard from a headless WSL shell', () => {
    expect(clipboardCommands('linux', { WSL_DISTRO_NAME: 'Ubuntu' }))
      .toEqual([['clip.exe', []]]);
    expect(clipboardCommands('linux', {
      WSL_DISTRO_NAME: 'Ubuntu',
      DISPLAY: ':0',
      WAYLAND_DISPLAY: 'wayland-0',
    })).toEqual([['clip.exe', []]]);
  });

  it('uses the shared capability selector in the OSC native fallback', () => {
    const source = readFileSync('src/runtime/ink/termio/osc.ts', 'utf8');

    expect(source).toContain("clipboardCommands('linux', process.env)");
    expect(source).not.toContain("execFileNoThrow('wl-copy'");
    expect(source).not.toContain("execFileNoThrow('xclip'");
    expect(source).not.toContain("execFileNoThrow('xsel'");
  });
});
