// Builds the Meetings window into ../ui (app.js, app.css, theme.css, fonts.css) from src/
// with OpenProgram's own UI kit: the components, theme tokens and Tailwind setup
// of the web app, so the window looks like the rest of the App.
//
//     node openprogram/programs/applications/meeting/web/build.mjs
//
// The web app is found beside this checkout (apps/web) unless OPENPROGRAM_WEB
// names it. The output is plain static files: the window loads no remote script.
// Run it again after changing src/ or the kit, and commit ui/ with the change.
import { createRequire } from "node:module";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const web = resolve(process.env.OPENPROGRAM_WEB || join(here, "../../../../../apps/web"));
if (!existsSync(join(web, "components/ui/button.tsx"))) {
  console.error("OPENPROGRAM_WEB must name the OpenProgram web app folder (apps/web)");
  process.exit(2);
}
const need = createRequire(join(web, "package.json"));
const esbuild = need("esbuild");
const postcss = need("postcss");
const tailwind = need("@tailwindcss/postcss");
const out = join(here, "..", "ui");

// "@/x" is the web app's own import alias; a few of its modules are replaced
// by the window's versions (src/shims) where they reach for the host page.
const shims = {
  "@/lib/i18n": join(here, "src/shims/i18n.ts"),
  "@/lib/tabs/center-tabs-store": join(here, "src/shims/center-tabs-store.ts"),
  // A summary never draws diagrams: the window does not carry Mermaid.
  "@/lib/chat/markdown/mermaid-lazy": join(here, "src/shims/mermaid-lazy.ts"),
  // The App's sidebar drawer is out of the window's reach.
  "@/components/layout/phone-drawer-button": join(here, "src/shims/phone-drawer-button.tsx"),
};
const alias = {
  name: "openprogram-alias",
  setup(build) {
    build.onResolve({ filter: /^@\// }, async (args) => {
      if (shims[args.path]) return { path: shims[args.path] };
      return build.resolve("./" + args.path.slice(2), { resolveDir: web, kind: args.kind });
    });
    // The kit imports some of its own modules by relative path.
    build.onResolve({ filter: /mermaid-lazy(\.ts)?$/ }, () => ({ path: shims["@/lib/chat/markdown/mermaid-lazy"] }));
  },
};

await esbuild.build({
  entryPoints: [join(here, "src/main.tsx")],
  outfile: join(out, "app.js"),
  bundle: true, format: "esm", minify: true, target: "es2022", jsx: "automatic",
  legalComments: "none",
  nodePaths: [join(web, "node_modules"), join(web, "../../node_modules")],
  define: { "process.env.NODE_ENV": '"production"' },
  plugins: [alias], logLevel: "warning",
});

// The App's typeface (Inter), served from the window's own files: a sandboxed
// frame can load fonts only from where its page came from.
await esbuild.build({
  stdin: { contents: '@import "@fontsource-variable/inter";', resolveDir: web, loader: "css" },
  outfile: join(out, "fonts.css"), bundle: true, minify: true,
  loader: { ".woff2": "file" }, assetNames: "fonts/[name]",
  nodePaths: [join(web, "node_modules"), join(web, "../../node_modules")], logLevel: "warning",
});

// The web app's stylesheet without its page layout: Tailwind, the design system's
// tokens and tiers, the colour themes and the document base, scanning the kit's
// components and this window's sources.
const css = `@import "tailwindcss";
@import "tw-animate-css";
@import "./styles/design/tailwind.css";
@import "./styles/design/index.css";
@import "./styles/themes/index.css";
@import "./styles/base/document.css";
@import "./styles/base/focus.css";
@import "./styles/base/scrollbar.css";
@source "${join(web, "components/ui")}";
@source "${join(web, "lib/format-utils")}";
@source "${join(here, "src")}";
`;
const from = join(web, "app", "meeting.css"); // resolves the imports like globals.css does
const result = await postcss([tailwind()]).process(css, { from, to: join(out, "theme.css") });
writeFileSync(join(out, "theme.css"), result.css);
// esbuild names the components' own CSS after the bundle.
if (!existsSync(join(out, "app.css"))) writeFileSync(join(out, "app.css"), "");
console.log(`built ${out}: app.js ${(readFileSync(join(out, "app.js")).length / 1024).toFixed(0)} KiB`);
