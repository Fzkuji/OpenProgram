// Rule (security): credentials are never retrievable in plaintext by the web
// app. Checked on the AST: no reveal identifiers or reveal URLs, key previews
// and credential rows are masked-only types, MCP env/headers are masked maps,
// and the MCP edit dialog opens with empty secret inputs. The runtime side
// (masked getKey request, replacement guard) is tests/settings/secret-non-retrieval.test.mjs.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const root = new URL("../../", import.meta.url);
const targetPaths = [
  "components/settings/providers/auth/api-key.tsx",
  "components/settings/providers/detail/credentials-section.tsx",
  "components/settings/providers/detail/credential-model.ts",
  "components/settings/providers/detail/add-credential-dialog.tsx",
  "components/settings/providers/add/key-entry.tsx",
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

// A credential row carries a key's last four characters, never the key.
const credentialFields = interfaceProperties(
  "components/settings/providers/types.ts",
  "CredentialRow",
);
assert.ok(credentialFields.includes("key_tail"), "CredentialRow must include key_tail");
for (const forbidden of ["api_key", "access_token", "refresh_token", "secret", "can_reveal", "value", "reveal"]) {
  assert.equal(
    credentialFields.includes(forbidden),
    false,
    `CredentialRow must not include plaintext/reveal field ${forbidden}`,
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

console.log("check-secret-non-retrieval: ok");
