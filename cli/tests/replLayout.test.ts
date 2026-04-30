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
    expect(source).toContain('welcome={pickerNode ? undefined : (stats ?? undefined)}');
    expect(source).toContain('fillWelcome={committed.length === 0 && !streaming && !pickerNode}');
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

  it('parks the terminal cursor at the prompt caret for IME input', () => {
    const source = read('src/components/PromptInput/PromptInput.tsx');

    expect(source).toContain('useDeclaredCursor');
    expect(source).toContain('ref={cursorRef}');
  });

  it('owns transcript scroll controls without drawing an app scrollbar', () => {
    const source = read('src/components/TranscriptViewport.tsx');

    expect(source).toContain('ScrollBox');
    expect(source).toContain("prependListener('input'");
    expect(source).toContain('stopImmediatePropagation');
    expect(source).not.toContain('TranscriptScrollbar');
    expect(source).not.toContain('computeScrollbarCells');
  });
});
