import { expect, it, vi } from 'vitest';

const capture = vi.hoisted(() => ({
  render: vi.fn(async (_tree: unknown) => ({ waitUntilExit: () => new Promise(() => {}) })),
}));
vi.mock('../src/runtime/index', () => ({ render: capture.render }));
vi.mock('../src/screens/REPL.js', () => ({ REPL: () => null }));
vi.mock('../src/screens/Demo.js', () => ({ Demo: () => null }));
vi.mock('../src/ws/client.js', () => ({ BackendClient: class { connect() {} } }));
vi.mock('../src/theme/autoTheme.js', () => ({ detectAutoTheme: async () => null }));

it('passes the Python launcher resume session to the existing TUI', async () => {
  vi.stubEnv('OPENPROGRAM_CONV', 'saved-goal-session');
  const sigint = process.listeners('SIGINT');
  const sigterm = process.listeners('SIGTERM');
  try {
    await import('../src/index.js');
    expect(capture.render).toHaveBeenCalledOnce();
    const tree = capture.render.mock.calls[0]?.[0] as unknown as {
      props: { children: { props: { initialConversation?: string } } };
    };
    expect(tree.props.children.props.initialConversation).toBe('saved-goal-session');
  } finally {
    vi.unstubAllEnvs();
    for (const fn of process.listeners('SIGINT')) if (!sigint.includes(fn)) process.removeListener('SIGINT', fn);
    for (const fn of process.listeners('SIGTERM')) if (!sigterm.includes(fn)) process.removeListener('SIGTERM', fn);
  }
});
