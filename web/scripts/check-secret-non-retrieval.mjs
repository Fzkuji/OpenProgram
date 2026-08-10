import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";
import ts from "typescript";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) {
      const base = new URL(`../${specifier.slice(2)}`, import.meta.url).href;
      const tsFile = `${base}.ts`;
      const tsxFile = `${base}.tsx`;
      const url = existsSync(fileURLToPath(tsFile))
        ? tsFile
        : existsSync(fileURLToPath(tsxFile))
          ? tsxFile
          : `${base}/index.ts`;
      return { url, shortCircuit: true };
    }
    if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)) {
      const base = new URL(specifier, context.parentURL).href;
      const tsFile = `${base}.ts`;
      const tsxFile = `${base}.tsx`;
      const url = existsSync(fileURLToPath(tsFile)) ? tsFile : tsxFile;
      return { url, shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

const fetchCalls = [];
globalThis.fetch = async (url, init) => {
  fetchCalls.push({ url, init });
  return {
    ok: true,
    status: 200,
    text: async () => JSON.stringify({ has_value: true, masked: "sk-…abc4" }),
  };
};

const { api } = await import("../lib/net/api.ts");
const preview = await api.getKey("OPENAI_API_KEY", true);
assert.deepEqual(preview, { has_value: true, masked: "sk-…abc4" });
assert.deepEqual(
  fetchCalls,
  [{
    url: "/api/config/key/OPENAI_API_KEY",
    init: { headers: { "Content-Type": "application/json" } },
  }],
  "getKey must have one masked-only request shape even when an old caller passes a second argument",
);

const root = new URL("../", import.meta.url);
const targetPaths = [
  "components/providers/provider-detail.tsx",
  "components/settings/providers/api-key.tsx",
  "components/settings/providers/account-manager.tsx",
  "lib/net/api.ts",
];

function parse(path) {
  const source = readFileSync(new URL(path, root), "utf8");
  return ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
}

function identifiersAndStrings(sourceFile) {
  const identifiers = new Set();
  const strings = [];
  function visit(node) {
    if (ts.isIdentifier(node)) identifiers.add(node.text);
    if (ts.isStringLiteralLike(node)) strings.push(node.text);
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return { identifiers, strings };
}

for (const path of targetPaths) {
  const { identifiers, strings } = identifiersAndStrings(parse(path));
  for (const forbidden of ["reveal", "revealKey", "can_reveal", "Eye", "EyeOff"]) {
    assert.equal(
      identifiers.has(forbidden),
      false,
      `${path} must not expose the credential-reveal identifier ${forbidden}`,
    );
  }
  for (const literal of strings) {
    assert.equal(
      /(?:reveal=|\/reveal(?:$|[/?]))/i.test(literal),
      false,
      `${path} must not contain a credential-reveal URL: ${literal}`,
    );
  }
}

function interfaceProperties(path, name) {
  const sourceFile = parse(path);
  let properties = null;
  for (const statement of sourceFile.statements) {
    if (ts.isInterfaceDeclaration(statement) && statement.name.text === name) {
      properties = statement.members
        .filter(ts.isPropertySignature)
        .map((member) => member.name.getText(sourceFile).replace(/["']/g, ""));
    }
  }
  assert.ok(properties, `${path} must declare ${name}`);
  return properties;
}

assert.deepEqual(
  interfaceProperties("lib/types.ts", "KeyPreview").sort(),
  ["has_value", "masked"],
  "KeyPreview must be masked-only",
);

const accountFields = interfaceProperties(
  "components/settings/providers/types.ts",
  "ProviderAccountView",
);
for (const required of ["has_value", "masked_key"]) {
  assert.ok(accountFields.includes(required), `ProviderAccountView must include ${required}`);
}
for (const forbidden of ["identity", "can_reveal", "value", "reveal"]) {
  assert.equal(
    accountFields.includes(forbidden),
    false,
    `ProviderAccountView must not include plaintext/reveal field ${forbidden}`,
  );
}

let replacement;
try {
  replacement = await import("../lib/net/secret-replacement.ts");
} catch (error) {
  assert.fail(`secret replacement guard is missing: ${error}`);
}

const { normalizeSecretReplacement } = replacement;
assert.equal(normalizeSecretReplacement("  sk-new-secret  ", "sk-…abc4"), "sk-new-secret");
assert.equal(normalizeSecretReplacement("sk-…abc4", "sk-…abc4"), null);
assert.equal(normalizeSecretReplacement("••••••••", "••••••••"), null);
assert.equal(normalizeSecretReplacement(""), null);
assert.equal(normalizeSecretReplacement("secret\nsecond-line"), null);
assert.equal(normalizeSecretReplacement("密钥"), null);

console.log("check-secret-non-retrieval: ok");
