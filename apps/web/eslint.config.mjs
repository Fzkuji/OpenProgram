import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    // eslint-config-next 16 enables React Compiler diagnostics that were not
    // part of the React 18 lint contract. Keep the existing React 18 source
    // checks while those rules are migrated with the affected components.
    rules: {
      "react-hooks/immutability": "off",
      "react-hooks/purity": "off",
      "react-hooks/refs": "off",
      "react-hooks/set-state-in-effect": "off",
      // Only says the (not enabled) React Compiler would skip a component.
      "react-hooks/preserve-manual-memoization": "off",
      // Introduced by eslint-config-next 16; this rejects a pre-existing
      // browser-compatible CommonJS adapter in file-drafts.ts.
      "@next/next/no-assign-module-variable": "off",
      // The app is a static export with `images.unoptimized`, so next/image
      // adds nothing; its <img> sources are blob/data URLs, screenshots and
      // favicons.
      "@next/next/no-img-element": "off",
      // A leading underscore marks a parameter or destructured field that is
      // dropped on purpose (`({ onSelect: _onSelect, ...item }) => item`).
      "@typescript-eslint/no-unused-vars": ["warn", {
        argsIgnorePattern: "^_",
        varsIgnorePattern: "^_",
        caughtErrorsIgnorePattern: "^_",
        destructuredArrayIgnorePattern: "^_",
        ignoreRestSiblings: true,
      }],
    },
  },
  {
    // A timer or handle declared first so the cleanup closure above its
    // assignment can read it (`let timer; const finish = () =>
    // clearTimeout(timer); timer = setTimeout(finish)`) cannot be const.
    files: ["**/*.ts", "**/*.tsx"],
    rules: { "prefer-const": ["error", { ignoreReadBeforeAssign: true }] },
  },
  {
    // Unit tests drive hooks directly against a stubbed React to assert
    // what they send; there is no component to call them from.
    files: ["tests/**"],
    rules: { "react-hooks/rules-of-hooks": "off" },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // These are generated third-party PDF assets, not application sources.
    "public/document-assets/**",
    // Temporary bundles a test run writes and removes; an interrupted run
    // can leave one behind.
    ".resources-test-*/**",
  ]),
]);

export default eslintConfig;
