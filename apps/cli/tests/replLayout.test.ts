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
    expect(source).toContain('welcome={pickerNode ? undefined : (stats ?? {})}');
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

  it('keeps bordered top-level panels aligned to the terminal width', () => {
    const source = read('src/utils/useTerminalWidth.ts');

    expect(source).toContain('return Math.max(MIN_PANEL_WIDTH, cols);');
    expect(source).not.toContain('cols - 1');
  });

  it('parks the terminal cursor at the prompt caret for IME input', () => {
    const source = read('src/components/PromptInput/PromptInput.tsx');

    expect(source).toContain('useDeclaredCursor');
    expect(source).toContain('ref={cursorRef}');
    expect(source).toContain('innerWidth = Math.max(8, width - 4)');
    expect(source).toContain('width={width}');
    expect(source).toContain(': null;');
    expect(source).not.toContain(": 'enter';");
    expect(source).not.toContain('<Text inverse>{inputViewport.cursor}</Text>');
  });

  it('reserves tab for prompt completion instead of thinking effort', () => {
    const repl = read('src/screens/REPL.tsx');
    const bottomBar = read('src/components/BottomBar.tsx');
    const prompt = read('src/components/PromptInput/PromptInput.tsx');
    const registry = read('src/commands/registry.ts');

    expect(registry).toContain("name: 'effort'");
    expect(prompt).toContain('tabIndex={0}');
    expect(prompt).toContain('autoFocus');
    expect(prompt).toContain('onKeyDownCapture');
    expect(prompt).toContain('event.preventDefault()');
    expect(repl).not.toContain("key.ctrl && input === 't'");
    expect(repl).not.toContain('key.tab && !key.shift');
    expect(bottomBar).not.toContain('ctrl+t');
  });

  it('keeps slash command hints out of the persistent bottom bar', () => {
    const repl = read('src/screens/REPL.tsx');
    const bottomBar = read('src/components/BottomBar.tsx');

    expect(repl).not.toContain('slashMode');
    expect(bottomBar).not.toContain('slashMode');
    expect(bottomBar).not.toContain('enter run');
    expect(bottomBar).not.toContain('tab fill');
    expect(bottomBar).not.toContain('esc cancel');
  });

  it('opens effort as an option picker', () => {
    const handler = read('src/commands/handler.ts');
    const router = read('src/screens/repl/pickerRouter.tsx');
    const types = read('src/screens/repl/types.ts');

    expect(handler).toContain("ctx.openPicker('effort')");
    expect(types).toContain("'effort'");
    expect(router).toContain("pickerKind === 'effort'");
    expect(router).toContain('Set thinking effort');
  });

  it('uses the native terminal cursor for declared prompt carets', () => {
    const source = read('src/runtime/ink/ink.tsx');

    expect(source).toContain('content: SHOW_CURSOR');
    expect(source).toContain('content: HIDE_CURSOR');
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
