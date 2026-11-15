/** Terminal text as a block for the composer. */
export function terminalSelectionText(selection: string): string {
  const body = selection.replace(/\s+$/, "");
  const fence = body.includes("```") ? "````" : "```";
  return `From the terminal:\n${fence}text\n${body}\n${fence}`;
}
