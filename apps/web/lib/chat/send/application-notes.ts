/**
 * What an application window says a conversation's next message is about.
 *
 * A window that declares `chat` (Auto Research) shows the owner's composer
 * for a conversation it keeps and tells it, before each message, what that
 * message is about: the research task open in the window, its deliverable,
 * its failing checks. The note rides the chat frame as `application_note`
 * and goes to the model beside the message, never into its text
 * (`turn_context` id `application`). Held in memory only: a window that
 * closes stops sending one.
 */
const notes = new Map<string, string>();

export function setApplicationNote(sessionId: string, note: string | null): void {
  if (note) notes.set(sessionId, note);
  else notes.delete(sessionId);
}

export function applicationNote(sessionId: string | null | undefined): string | undefined {
  return sessionId ? notes.get(sessionId) : undefined;
}
