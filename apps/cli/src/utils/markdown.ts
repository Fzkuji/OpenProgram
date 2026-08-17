import { marked } from 'marked';
import { markedTerminal } from 'marked-terminal';

/**
 * Markdown → ANSI for terminal display.
 *
 * marked + marked-terminal cost ~100ms at module-import time. We tried
 * lazy require() but esbuild's ESM bundle can't dynamic-require, so we
 * pay the cost up front. The trade-off is fine — Ink's first render is
 * still under 100ms after this.
 */
let configured = false;

export const renderMarkdown = (text: string): string => {
  if (!configured) {
    marked.use(markedTerminal() as Parameters<typeof marked.use>[0]);
    configured = true;
  }
  try {
    const out = marked.parse(text) as string;
    return out.replace(/\n+$/, '');
  } catch {
    return text;
  }
};
