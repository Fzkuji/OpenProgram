// Builds the standalone application page into ../shell (shell.js, shell.css,
// fonts.css, fonts/) from src/ with OpenProgram's own UI kit, as the Meetings
// window is built (programs/applications/meeting/web/build.mjs):
//
//     node openprogram/interfaces/standalone/web/build.mjs
//
// The web app is found beside this checkout (apps/web) unless OPENPROGRAM_WEB
// names it. Run it again after changing src/ or the kit, and commit shell/.
import { createRequire } from "node:module";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const web = resolve(process.env.OPENPROGRAM_WEB || join(here, "../../../../apps/web"));
if (!existsSync(join(web, "components/ui/button.tsx"))) {
  console.error("OPENPROGRAM_WEB must name the OpenProgram web app folder (apps/web)");
  process.exit(2);
}
const need = createRequire(join(web, "package.json"));
const esbuild = need("esbuild");
const postcss = need("postcss");
const tailwind = need("@tailwindcss/postcss");
const out = join(here, "..", "shell");
const nodePaths = [join(web, "node_modules"), join(web, "../../node_modules")];

// "@/x" is the web app's import alias; the modules that reach for the App's
// page are replaced by the standalone page's versions (src/shims).
const shims = {
  "@/lib/i18n": join(here, "src/shims/i18n.ts"),
  "@/lib/net/applications": join(here, "src/shims/applications-net.ts"),
  "@/lib/chat/markdown/mermaid-lazy": join(here, "src/shims/mermaid-lazy.ts"),
};
const alias = {
  name: "openprogram-alias",
  setup(build) {
    build.onResolve({ filter: /^@\// }, async (args) => {
      if (shims[args.path]) return { path: shims[args.path] };
      return build.resolve("./" + args.path.slice(2), { resolveDir: web, kind: args.kind });
    });
    build.onResolve({ filter: /mermaid-lazy(\.ts)?$/ }, () => ({ path: shims["@/lib/chat/markdown/mermaid-lazy"] }));
  },
};

await esbuild.build({
  entryPoints: [join(here, "src/shell.tsx")],
  outfile: join(out, "shell.js"),
  bundle: true, format: "esm", minify: true, target: "es2022", jsx: "automatic",
  legalComments: "none", nodePaths,
  define: { "process.env.NODE_ENV": '"production"' },
  plugins: [alias], logLevel: "warning",
});

await esbuild.build({
  stdin: { contents: '@import "@fontsource-variable/inter";', resolveDir: web, loader: "css" },
  outfile: join(out, "fonts.css"), bundle: true, minify: true,
  loader: { ".woff2": "file" }, assetNames: "fonts/[name]", nodePaths, logLevel: "warning",
});

const css = `@import "tailwindcss";
@import "tw-animate-css";
@import "./styles/design/tailwind.css";
@import "./styles/design/index.css";
@import "./styles/themes/index.css";
@import "./styles/base/document.css";
@import "./styles/base/focus.css";
@import "./styles/base/scrollbar.css";
@import "./styles/chat/messages/bubbles.css";
@import "./styles/chat/messages/file-links.css";
@source "${join(web, "components/ui")}";
@source "${join(web, "lib/format-utils")}";
@source "${join(here, "src")}";
html, body, #root { height: 100%; margin: 0; }
`;
const from = join(web, "app", "standalone.css");
const result = await postcss([tailwind()]).process(css, { from, to: join(out, "theme.css") });
writeFileSync(join(out, "theme.css"), result.css);
if (!existsSync(join(out, "shell.css"))) writeFileSync(join(out, "shell.css"), "");
console.log(`built ${out}: shell.js ${(readFileSync(join(out, "shell.js")).length / 1024).toFixed(0)} KiB`);
