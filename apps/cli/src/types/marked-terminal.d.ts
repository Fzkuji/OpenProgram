declare module 'marked-terminal' {
  // marked-terminal exports an ESM default factory that returns an
  // object compatible with `marked.use(extension)`. The shape doesn't
  // really matter for our use — we just need TypeScript to stop
  // complaining about the implicit any.
  export function markedTerminal(options?: Record<string, unknown>): unknown;
  const _default: typeof markedTerminal;
  export default _default;
}
