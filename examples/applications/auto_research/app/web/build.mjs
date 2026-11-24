// Builds ui/app.js, ui/app.css (the components' own CSS) and ui/theme.css from src/ with OpenProgram's own UI kit:
// the shadcn radix-nova components, theme tokens and Tailwind setup of the
// OpenProgram web app. Point OPENPROGRAM_WEB at its apps/web folder:
//
//     OPENPROGRAM_WEB=<OpenProgram checkout>/apps/web node build.mjs
//
// The output is plain static files: the app page loads no remote script.
import { createRequire } from "node:module";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const web = resolve(process.env.OPENPROGRAM_WEB || process.argv[2] || "");
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
// by the app's versions (src/shims) where they reach for the host's server.
const shims = {
  "@/lib/i18n": join(here, "src/shims/i18n.ts"),
  "@/lib/tabs/center-tabs-store": join(here, "src/shims/center-tabs-store.ts"),
};
const alias = {
  name: "openprogram-alias",
  setup(build) {
    build.onResolve({ filter: /^@\// }, async (args) => {
      if (shims[args.path]) return { path: shims[args.path] };
      return build.resolve("./" + args.path.slice(2), { resolveDir: web, kind: args.kind });
    });
  },
};

await esbuild.build({
  entryPoints: [join(here, "src/main.tsx")],
  outfile: join(out, "app.js"),
  bundle: true, format: "esm", minify: true, target: "es2022", jsx: "automatic",
  nodePaths: [join(web, "node_modules"), join(web, "../../node_modules")],
  define: { "process.env.NODE_ENV": '"production"' },
  plugins: [alias], logLevel: "warning",
});

// The web app's stylesheet without its page layout: Tailwind, the design system's tokens and
// tiers, the colour themes, the document base and the menu row styles, scanning the kit's
// components and this app's sources.
const css = `@import "tailwindcss";
@import "tw-animate-css";
@import "./styles/design/tailwind.css";
@import "./styles/design/index.css";
@import "./styles/themes/index.css";
@import "./styles/base/document.css";
@import "./styles/base/focus.css";
@import "./styles/base/scrollbar.css";
@import "./styles/chat/composer/top-bar-chips.css";
@source "${join(web, "components/ui")}";
@source "${join(here, "src")}";
`;
const from = join(web, "app", "auto-research.css");   // resolves the imports like globals.css does
const result = await postcss([tailwind()]).process(css, { from, to: join(out, "theme.css") });
writeFileSync(join(out, "theme.css"), result.css);
console.log("built ui/app.js, ui/app.css and ui/theme.css");
