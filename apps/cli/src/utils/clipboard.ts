import { setClipboard } from '../runtime/ink/termio/osc.js';
import { clipboardCommands } from '../runtime/utils/clipboardCommands.js';
import { execFileNoThrow } from '../runtime/utils/execFileNoThrow.js';

export { clipboardCommands } from '../runtime/utils/clipboardCommands.js';

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
  for (const [binary, args] of clipboardCommands()) {
    const result = await execFileNoThrow(binary, [...args], {
      input: text,
      timeout: 2000,
      useCwd: false,
    });
    if (result.code === 0) return true;
  }

  // OSC 52 fallback. The shared runtime path adds tmux/screen passthrough and
  // loads tmux's paste buffer, so it works over SSH without touching a remote
  // graphical clipboard. Limit the control sequence because terminals often
  // truncate large payloads.
  const b64 = Buffer.from(text).toString('base64');
  if (b64.length >= 75000 || !process.stdout.isTTY) return false;

  try {
    process.stdout.write(await setClipboard(text));
    return true;
  } catch {
    return false;
  }
}
