import { describe, expect, it } from 'vitest';
import { readFileSync } from 'fs';

const read = (path: string): string => readFileSync(path, 'utf8');

describe('REPL layout contract', () => {
  it('uses the fullscreen app-owned transcript layout', () => {
    const source = read('src/screens/REPL.tsx');

    expect(source).toContain('<Shell mouseTracking mode="alt">');
    expect(source).toContain('<TranscriptViewport');
    expect(source).toContain('scrollRef={transcriptScrollRef}');
    expect(source).toContain('<Messages');
    expect(source).toContain('const hasTranscript = committed.length > 0 || streaming !== null;');
    expect(source).toContain('<Welcome stats={welcomeStats} fillAvailable={!hasTranscript} />');
    expect(source).not.toContain('welcome={');
    expect(source).not.toContain('fillWelcome');
    expect(source).not.toContain('onTranscriptScroll');
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

  it('keeps transcript scrolling out of PromptInput', () => {
    const source = read('src/components/PromptInput/PromptInput.tsx');

    expect(source).not.toContain('onTranscriptScroll');
    expect(source).not.toContain('TranscriptScrollAction');
  });

  it('owns transcript scroll controls and an app scrollbar in one component', () => {
    const source = read('src/components/TranscriptViewport.tsx');

    expect(source).toContain('ScrollBox');
    expect(source).toContain('TranscriptScrollbar');
    expect(source).toContain("prependListener('input'");
    expect(source).toContain('position="absolute"');
    expect(source).toContain('stopImmediatePropagation');
  });
});
