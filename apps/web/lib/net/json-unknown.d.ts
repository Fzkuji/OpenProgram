/** Server replies are unknown until checked: an untyped field rendered as-is broke whole pages. */
interface Body { json(): Promise<unknown>; }
