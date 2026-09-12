import { fileURLToPath } from "node:url";

/** @type {import('postcss-load-config').Config} */
const config = {
  plugins: {
    "@tailwindcss/postcss": {},
    [fileURLToPath(new URL("./scripts/postcss-katex-fonts.cjs", import.meta.url))]: {},
  },
};

export default config;
