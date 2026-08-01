/**
 * The chat route the user was last on (`/chat` or `/s/<id>`).
 *
 * `AppShell`'s route effect records it; the Functions page reads it so
 * "Use"/"Edit" returns to the conversation the user came from instead of
 * dumping them on a blank `/chat`.
 */

let lastChatPath: string | null = null;

export function setLastChatPath(p: string): void {
  lastChatPath = p;
}

export function getLastChatPath(): string | null {
  return lastChatPath;
}
