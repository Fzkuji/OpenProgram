export type ClipboardCommand = readonly [binary: string, args: readonly string[]];

/**
 * Return only native clipboard helpers that can reach the current user's
 * local desktop.  Keep this capability decision below both clipboard entry
 * points: OSC 52's native safety net must not probe X11/Wayland binaries in a
 * headless console, and an SSH session must never write the remote desktop's
 * clipboard.
 */
export function clipboardCommands(
  platform: NodeJS.Platform = process.platform,
  environment: NodeJS.ProcessEnv = process.env,
): ClipboardCommand[] {
  if (environment.SSH_CONNECTION) return [];
  if (platform === 'darwin') return [['pbcopy', []]];
  if (platform === 'win32') return [['clip', []]];
  if (platform !== 'linux') return [];

  // WSL's host bridge is usable even in a headless shell. Prefer it over
  // optional WSLg display servers so copy consistently reaches Windows.
  if (environment.WSL_DISTRO_NAME) return [['clip.exe', []]];

  const commands: ClipboardCommand[] = [];
  if (environment.WAYLAND_DISPLAY) commands.push(['wl-copy', []]);
  if (environment.DISPLAY) {
    commands.push(['xclip', ['-selection', 'clipboard']]);
    commands.push(['xsel', ['--clipboard', '--input']]);
  }
  return commands;
}

/**
 * What to write to a clipboard helper's stdin. Windows' clip.exe reads a
 * pipe in the console code page, so UTF-8 Chinese or emoji arrive as
 * mojibake; it reads UTF-16LE correctly when the text starts with a byte
 * order mark. Every other helper takes UTF-8.
 */
export function clipboardInput(binary: string, text: string): string | Buffer {
  if (binary !== 'clip' && binary !== 'clip.exe') return text;
  return Buffer.concat([Buffer.from([0xff, 0xfe]), Buffer.from(text, 'utf16le')]);
}
