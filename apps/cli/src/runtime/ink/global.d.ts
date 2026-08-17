/// <reference types="react" />

// Ambient declarations for the runtime.
//
//  - JSX intrinsic elements `ink-box`, `ink-text`, `ink-link`, and
//    `ink-raw-ansi` are produced by the React reconciler in this
//    runtime and consumed by the host (output.ts). They never reach
//    the DOM, so React's default JSX type checks reject them — the
//    augmentation below adds them to React's IntrinsicElements.
//  - `Bun` is referenced via runtime checks (`typeof Bun !==
//    'undefined'`) so a single source file runs under Node and Bun.
//    We don't depend on @types/bun (much larger surface than we
//    need); the typed shape below covers only the APIs the runtime
//    actually calls (stringWidth, semver, wrapAnsi).
//  - `react/compiler-runtime` ships in React 19's package but lacks
//    a typed export for `c` (used by the React Forget compiler).
//  - `bidi-js` doesn't ship its own types.
//
// `react-reconciler`, `semver`, `lodash-es/*` and `stack-utils` use
// the @types packages installed in devDependencies.
//
// This file is a SCRIPT (no top-level import/export) so its global
// declarations register at the program level.

declare module 'react/compiler-runtime' {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export function c(size: number): any[]
}

declare module 'bidi-js' {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const bidiFactory: () => Record<string, any>
  export default bidiFactory
}

interface BunSemver {
  order(a: string, b: string): -1 | 0 | 1
  satisfies(version: string, range: string): boolean
}

interface BunRuntime {
  stringWidth(s: string, opts?: { ambiguousIsNarrow?: boolean }): number
  semver: BunSemver
  wrapAnsi?(input: string, columns: number, options?: { hard?: boolean; wordWrap?: boolean; trim?: boolean }): string
}

declare var Bun: BunRuntime | undefined

declare namespace React {
  namespace JSX {
    interface IntrinsicElements {
      'ink-box': Record<string, unknown>
      'ink-text': Record<string, unknown>
      'ink-link': Record<string, unknown>
      'ink-raw-ansi': Record<string, unknown>
    }
  }
}
