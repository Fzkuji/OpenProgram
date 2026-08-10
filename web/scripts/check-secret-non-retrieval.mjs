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

// --- MCP server env / headers / auth secrets -------------------------
//
// An MCP server's `env` and `headers` hold arbitrary credentials, so the
// API returns `{has_value, masked}` per name rather than the value. These
// assertions pin the two halves of that contract on the frontend: the
// types cannot carry a plaintext value, and the edit dialog never
// prefills a secret input from a server response.

const mcpSecretMapFields = interfaceProperties(
  "components/mcp/mcp-detail-view.tsx",
  "SecretPreview",
).sort();
assert.deepEqual(
  mcpSecretMapFields,
  ["has_value", "masked"],
  "MCP SecretPreview must be masked-only",
);

const mcpAuthFields = interfaceProperties(
  "components/mcp/mcp-detail-view.tsx",
  "ServerAuthInfo",
);
for (const forbidden of ["token", "client_secret", "value", "reveal"]) {
  assert.equal(
    mcpAuthFields.includes(forbidden),
    false,
    `ServerAuthInfo must not include plaintext field ${forbidden}`,
  );
}
for (const required of ["has_token", "masked_token", "has_client_secret"]) {
  assert.ok(
    mcpAuthFields.includes(required),
    `ServerAuthInfo must include ${required}`,
  );
}

// `ServerStatus.env` / `.headers` must be the masked map, never a plain
// `Record<string, string>` that could hold values.
{
  const sourceFile = parse("components/mcp/mcp-detail-view.tsx");
  let statusMembers = null;
  for (const statement of sourceFile.statements) {
    if (ts.isInterfaceDeclaration(statement) && statement.name.text === "ServerStatus") {
      statusMembers = statement.members.filter(ts.isPropertySignature);
    }
  }
  assert.ok(statusMembers, "mcp-detail-view must declare ServerStatus");
  for (const field of ["env", "headers"]) {
    const member = statusMembers.find(
      (m) => m.name.getText(sourceFile).replace(/["']/g, "") === field,
    );
    assert.ok(member, `ServerStatus must declare ${field}`);
    assert.equal(
      member.type.getText(sourceFile),
      "SecretMap",
      `ServerStatus.${field} must be the masked SecretMap, not a plaintext record`,
    );
  }
}

// The edit dialog opens with empty secret inputs. Prefilling any of them
// from a server response is what would put a mask (or worse, a value)
// back on the wire, so the initializer literals must be empty strings.
{
  const sourceFile = parse("components/mcp/mcp-page.tsx");
  const emptySecretFields = ["env", "headers", "bearerToken", "oauthClientSecret"];
  const assignments = [];
  function visit(node) {
    if (ts.isPropertyAssignment(node)) {
      const key = node.name.getText(sourceFile).replace(/["']/g, "");
      if (emptySecretFields.includes(key)) assignments.push([key, node.initializer]);
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  for (const field of emptySecretFields) {
    const found = assignments.filter(([key]) => key === field);
    assert.ok(found.length > 0, `mcp-page must initialize ${field}`);
    for (const [, initializer] of found) {
      assert.ok(
        ts.isStringLiteralLike(initializer) && initializer.text === "",
        `mcp-page must open the MCP edit dialog with an empty ${field}, got ${initializer.getText(sourceFile)}`,
      );
    }
  }
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
