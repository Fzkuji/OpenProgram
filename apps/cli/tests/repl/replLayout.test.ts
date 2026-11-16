import { describe, expect, it } from 'vitest';
import { readFileSync } from 'fs';

const read = (path: string): string => readFileSync(path, 'utf8');

describe('REPL layout contract', () => {
  it('uses the app-owned transcript layout with an inline accessibility fallback', () => {
    const source = read('src/screens/REPL.tsx');

    expect(source).toContain('altScreen = true');
    expect(source).toContain('screenReader = false');
    expect(source).toContain('mouseTracking={altScreen && !screenReader}');
    expect(source).toContain("mode={altScreen && !screenReader ? 'alt' : 'inline'}");
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

  it('enters alternate screen without erasing the main-screen scrollback', () => {
    const entry = read('src/index.tsx');
    const alternateScreen = read('src/runtime/ink/components/AlternateScreen.tsx');
    const runtime = read('src/runtime/ink/ink.tsx');

    expect(entry).not.toContain("process.stdout.write('\\x1b[2J\\x1b[3J\\x1b[H')");
    expect(alternateScreen).toContain('ENTER_ALT_SCREEN +');
    expect(alternateScreen).toContain('ERASE_SCREEN +');
    expect(alternateScreen).not.toContain('ERASE_SCROLLBACK');
    expect(runtime).not.toContain('ERASE_SCROLLBACK');
  });

  it('does not race Ink input with a pre-render terminal query', () => {
    const source = read('src/index.tsx');

    expect(source).not.toContain('detectAutoTheme(null)');
    expect(source).not.toContain('setCachedSystemTheme');
  });

  it('fails cleanly before rendering ANSI frames when stdio is not a TTY', () => {
    const source = read('src/index.tsx');

    expect(source).toContain('!process.stdin.isTTY || !process.stdout.isTTY');
    expect(source).toContain('use `openprogram --print` for pipelines');
    expect(source.indexOf('!process.stdin.isTTY || !process.stdout.isTTY'))
      .toBeLessThan(source.indexOf('new BackendClient(ws)'));
  });

  it('uses conventional process exit codes for POSIX termination signals', () => {
    const source = read('src/index.tsx');

    expect(source).toContain("process.once('SIGINT', () => process.exit(130))");
    expect(source).toContain("process.once('SIGTERM', () => process.exit(143))");
  });

  it('closes the backend when rendering or waiting for exit fails', () => {
    const source = read('src/index.tsx');
    const connect = source.indexOf('client.connect()');
    const render = source.indexOf('await render(root');
    const finallyBlock = source.indexOf('} finally {');
    const close = source.indexOf('client.close()');

    expect(source.indexOf('try {')).toBeLessThan(connect);
    expect(connect).toBeLessThan(render);
    expect(render).toBeLessThan(finallyBlock);
    expect(finallyBlock).toBeLessThan(close);
  });

  it('loads the requested transcript for --resume on startup', () => {
    const source = read('src/screens/REPL.tsx');

    expect(source).toContain("if (initialConversation)");
    expect(source).toContain("client.send({ action: 'load_session', session_id: initialConversation })");
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
