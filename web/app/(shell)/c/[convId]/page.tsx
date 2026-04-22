// Route marker only — the chat shell lives in AppShell and picks up
// the conv id from pathname via an internal effect. Keeps the WS + DOM
// alive across conversation switches (no remount, no refetch).
export default function ConversationPage() {
  return null;
}
