// Type shims for libraries loaded as CDN <script> tags by AppShell
// (`EXTERNAL_LIBS`). Keeping these typed lets React modules consume
// them via `window.*` without scattering `as any` everywhere.

declare global {
  interface MarkedLib {
    parse(src: string, opts?: { breaks?: boolean; gfm?: boolean }): string;
  }

  interface KatexDelimiter {
    left: string;
    right: string;
    display: boolean;
  }

  interface KatexAutoRenderOptions {
    delimiters?: KatexDelimiter[];
    throwOnError?: boolean;
    ignoredTags?: string[];
    ignoredClasses?: string[];
  }

  interface Window {
    marked?: MarkedLib;
    renderMathInElement?: (el: HTMLElement, opts?: KatexAutoRenderOptions) => void;
  }
}

export {};
