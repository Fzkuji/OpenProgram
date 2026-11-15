/**
 * Shared module loader for Web unit tests: lets a test `await import()` app
 * modules (`@/…` aliases, extensionless relative imports, `.tsx`) straight
 * from source under `node --experimental-strip-types --test`.
 *
 *   import { installLoader, STUBS } from "../_support/load.mjs";
 *   installLoader({ stubs: { "@/lib/i18n": STUBS.i18n, "./detail": "export const Detail = () => null;" } });
 *   const { Thing } = await import("../../components/thing.tsx");
 *
 * Call it once per test file, at module top, BEFORE the first import of app
 * code (static `import` lines are hoisted, so app modules must be loaded with
 * `await import()` after this call).
 *
 * What it does:
 * - `@/x` resolves against apps/web; `@/x` and extensionless `./x` try
 *   `x`, `x.ts`, `x.tsx`, `x/index.ts`, `x/index.tsx` in that order.
 * - `.tsx` (and `.ts` with `transpileTs: true`, for TS that strip-types
 *   cannot erase: enums, namespaces, parameter properties) is compiled with
 *   `typescript`'s `transpileModule` (React JSX runtime, ESNext).
 * - `.module.css` imports become a Proxy that returns each class name as its
 *   own key (`styles.row === "row"`), so tests can query rendered classes;
 *   plain `.css` side-effect imports become an empty module.
 * - `stubs` maps an exact import specifier to module source. The stub may
 *   import packages ("react") but not relative paths; reach test state
 *   through `globalThis`. `null` removes a default stub.
 * - `replace` maps a path fragment of the resolved URL (e.g.
 *   "/components/ui/dialog.tsx", "/@radix-ui/react-dropdown-menu/") to module
 *   source: it swaps that module whoever imports it, however the specifier
 *   is spelt.
 */
import { existsSync, readFileSync, statSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";
import ts from "typescript";

export const webRoot = new URL("../../", import.meta.url);

/** Ready-made stub sources a test can opt into by specifier. */
export const STUBS = {
  /** English passthrough: `t(key)` → key, `text(en)` → en. One stable object:
   *  components list `text` in effect dependencies, and a fresh function per
   *  render would re-run those effects forever. */
  i18n: [
    "const translation = { t: (key) => key, text: (en) => en, locale: 'en' };",
    "export const useTranslation = () => translation;",
    "export const translateText = (en) => en;",
  ].join("\n"),
  /** A router whose calls land in `globalThis.__routerCalls`; the path is `window.location.pathname`. */
  nextNavigation: [
    "const calls = (globalThis.__routerCalls ??= []);",
    "const router = {",
    "  push: (href) => calls.push(['push', href]),",
    "  replace: (href) => calls.push(['replace', href]),",
    "  prefetch() {}, back: () => calls.push(['back']), forward: () => calls.push(['forward']), refresh() {},",
    "};",
    "export const useRouter = () => router;",
    "export const usePathname = () => globalThis.window?.location?.pathname ?? '/';",
    "export const useSearchParams = () => new URLSearchParams(globalThis.window?.location?.search ?? '');",
    "export const useParams = () => ({});",
    "export const redirect = (href) => { calls.push(['redirect', href]); };",
    "export const notFound = () => { throw new Error('notFound()'); };",
  ].join("\n"),
};

/** Stubs every test gets unless it overrides or removes them. */
export const DEFAULT_STUBS = {
  "next/navigation": STUBS.nextNavigation,
};

const CSS_MODULE = "export default new Proxy({}, { get: (_, key) => typeof key === 'string' ? key : undefined });";
const EXTENSIONS = ["", ".ts", ".tsx", "/index.ts", "/index.tsx"];

const dataUrl = (source) => `data:text/javascript,${encodeURIComponent(source)}`;
// Stubs get a file: URL inside the app (never read from disk) so their own
// bare imports resolve from the app's node_modules.
const stubUrl = (specifier) => new URL(`tests/_support/__stub__/${encodeURIComponent(specifier)}.mjs`, webRoot).href;

function isFile(url) {
  const path = fileURLToPath(url);
  return existsSync(path) && statSync(path).isFile();
}

/** Compile TS/TSX source to an ES module the way the app's bundler would. */
export function transpile(source, fileName = "module.tsx") {
  return ts.transpileModule(source, {
    fileName,
    compilerOptions: {
      jsx: ts.JsxEmit.ReactJSX,
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
      verbatimModuleSyntax: false,
    },
  }).outputText;
}

let installed = false;

/**
 * Register the resolve/load hooks. Options:
 * - `stubs`: `{ [specifier]: source | null }`, merged over DEFAULT_STUBS.
 * - `replace`: `{ [resolvedUrlFragment]: source }`.
 * - `cssModules`: "proxy" (default) or "empty" (`export default {}`).
 * - `transpileTs`: also run `.ts` files through transpileModule.
 */
export function installLoader({ stubs = {}, replace = {}, cssModules = "proxy", transpileTs = false } = {}) {
  if (installed) throw new Error("installLoader() was already called in this test file");
  installed = true;
  const stubMap = Object.fromEntries(
    Object.entries({ ...DEFAULT_STUBS, ...stubs }).filter(([, source]) => source != null),
  );
  const replacements = Object.entries(replace);
  const stubSources = new Map(Object.entries(stubMap).map(([specifier, source]) => [stubUrl(specifier), source]));
  const cssModuleSource = cssModules === "empty" ? "export default {};" : CSS_MODULE;

  registerHooks({
    resolve(specifier, context, nextResolve) {
      if (Object.hasOwn(stubMap, specifier)) {
        return { url: stubUrl(specifier), shortCircuit: true };
      }
      if (specifier.endsWith(".module.css")) return { url: dataUrl(cssModuleSource), shortCircuit: true };
      if (specifier.endsWith(".css")) return { url: dataUrl("export default {};"), shortCircuit: true };
      const base = specifier.startsWith("@/")
        ? new URL(specifier.slice(2), webRoot).href
        : specifier.startsWith("./") || specifier.startsWith("../")
          ? new URL(specifier, context.parentURL).href
          : null;
      if (base && !base.startsWith("data:")) {
        for (const suffix of EXTENSIONS) {
          if (isFile(base + suffix)) return { url: base + suffix, shortCircuit: true };
        }
      }
      return nextResolve(specifier, context);
    },
    load(url, context, nextLoad) {
      if (stubSources.has(url)) return { format: "module", source: stubSources.get(url), shortCircuit: true };
      if (url.startsWith("file:")) {
        const hit = replacements.find(([fragment]) => url.includes(fragment));
        if (hit) return { format: "module", source: hit[1], shortCircuit: true };
        // The App's bundler imports JSON without an import attribute;
        // Node requires one, so a JSON file loads as its default export.
        if (url.endsWith(".json") && !context.importAttributes?.type) {
          return { format: "module", source: `export default ${readFileSync(fileURLToPath(url), "utf8")};`, shortCircuit: true };
        }
        if (url.endsWith(".tsx") || (transpileTs && url.endsWith(".ts"))) {
          const path = fileURLToPath(url);
          return { format: "module", source: transpile(readFileSync(path, "utf8"), path), shortCircuit: true };
        }
      }
      return nextLoad(url, context);
    },
  });
}
