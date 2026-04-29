import { describe, expect, it } from 'vitest';
import { readFileSync } from 'fs';

const read = (path: string): string => readFileSync(path, 'utf8');

describe('REPL layout contract', () => {
  it('uses the fullscreen app-owned transcript layout', () => {
    const source = read('src/screens/REPL.tsx');

    expect(source).toContain('<Shell mouseTracking mode="alt">');
    expect(source).toContain('<ScrollView stickyBottom>');
    expect(source).toContain('<Messages');
  });

  it('does not write the REPL transcript into terminal scrollback', () => {
    const source = read('src/screens/REPL.tsx');

    expect(source).not.toContain('useScrollbackWriter');
    expect(source).not.toContain('formatTurnText');
    expect(source).not.toContain('formatWelcomeText');
    expect(source).not.toContain('resetScrollbackCursor');
  });

  it('clears previous shell history before starting the live REPL', () => {
    const source = read('src/index.tsx');

    expect(source).toContain("process.stdout.write('\\x1b[2J\\x1b[3J\\x1b[H')");
  });
});
