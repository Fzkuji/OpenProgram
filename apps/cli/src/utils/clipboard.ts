import { spawn } from 'child_process';

/**
 * Copy text to the system clipboard.
 *
 * Strategy:
 *  - macOS: pbcopy
 *  - Linux: xclip / wl-copy / xsel (try in order)
 *  - Windows: clip
 *  - Fallback: emit an OSC 52 escape so capable terminals
 *    (iTerm2, kitty, recent ConPTY) still pick it up.
 *
 * Resolves to true if a backend accepted the text. Never throws.
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  const tries =
    process.platform === 'darwin'
      ? [['pbcopy', []]]
      : process.platform === 'win32'
      ? [['clip', []]]
      : [
          ['wl-copy', []],
          ['xclip', ['-selection', 'clipboard']],
          ['xsel', ['--clipboard', '--input']],
        ];

  for (const [bin, args] of tries) {
    const ok = await new Promise<boolean>((resolve) => {
      try {
        const proc = spawn(bin as string, args as string[], {
          stdio: ['pipe', 'ignore', 'ignore'],
        });
        proc.on('error', () => resolve(false));
        proc.on('close', (code) => resolve(code === 0));
        proc.stdin.write(text);
        proc.stdin.end();
      } catch {
        resolve(false);
      }
    });
    if (ok) return true;
  }

  // OSC 52 fallback. Limit to ~75 KB; some terminals truncate beyond that.
  const b64 = Buffer.from(text).toString('base64');
  if (b64.length < 75000) {
    process.stdout.write(`\x1b]52;c;${b64}\x07`);
    return true;
  }
  return false;
}
