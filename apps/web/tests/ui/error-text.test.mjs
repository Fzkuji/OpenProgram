import assert from "node:assert/strict";
import { describe, test } from "node:test";

const { errorDetailCode, errorDetailText } = await import("../../lib/net/error-text.ts");

describe("errorDetailText", () => {
  test("a sentence is kept; an empty one falls back", () => {
    assert.equal(errorDetailText("server did not start", "HTTP 502"), "server did not start");
    assert.equal(errorDetailText("  ", "HTTP 502"), "HTTP 502");
    assert.equal(errorDetailText(undefined, "HTTP 502"), "HTTP 502");
  });

  test("a list of what is wrong with the body reads as one line, each with where it is", () => {
    const detail = [
      { type: "missing", loc: ["body", "name"], msg: "Field required" },
      { type: "int_parsing", loc: ["body", "args", 0], msg: "Input should be a valid integer" },
      "plain",
      { loc: ["body"] },
    ];
    assert.equal(errorDetailText(detail, "HTTP 422"), "name: Field required; args.0: Input should be a valid integer; plain");
    assert.equal(errorDetailText([{ loc: ["body"] }], "HTTP 422"), "HTTP 422");
  });

  test("an object gives its message, else its code, else the fallback", () => {
    assert.equal(errorDetailText({ code: "busy", message: "Try again later" }, "HTTP 500"), "Try again later");
    assert.equal(errorDetailText({ code: "mcp_runtime_restart_failed", action: "retry_or_restart" }, "HTTP 500"), "mcp_runtime_restart_failed");
    assert.equal(errorDetailText({ action: "retry" }, "HTTP 500"), "HTTP 500");
  });

  test("the code is read from an object only", () => {
    assert.equal(errorDetailCode({ code: "busy" }), "busy");
    assert.equal(errorDetailCode("busy"), undefined);
    assert.equal(errorDetailCode([{ code: "busy" }]), undefined);
    assert.equal(errorDetailCode({ code: 7 }), undefined);
  });
});
