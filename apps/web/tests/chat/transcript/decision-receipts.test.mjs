import assert from "node:assert/strict";
import { test } from "node:test";
import { isSettled, showsReceipt } from "../../../lib/chat/decisions/decision-receipts.ts";

test("a granted or refused permission leaves no card behind", () => {
  assert.equal(showsReceipt({ kind: "approval" }, { status: "answered" }), false);
  assert.equal(showsReceipt({ kind: "approval" }, { status: "declined" }), false);
});

test("a permission that closed unconfirmed or failed keeps its card", () => {
  assert.equal(showsReceipt({ kind: "approval" }, { status: "closed" }), true);
  assert.equal(showsReceipt({ kind: "approval" }, { status: "answered", error: "lost" }), true);
});

test("an answered question keeps its answer in the transcript", () => {
  for (const kind of ["ask", "confirm", "form", "ask_many"]) {
    assert.equal(showsReceipt({ kind }, { status: "answered" }), true);
  }
});

test("an open decision is a pending panel, not a receipt", () => {
  assert.equal(isSettled(undefined), false);
  assert.equal(showsReceipt({ kind: "ask" }, undefined), false);
  assert.equal(showsReceipt({ kind: "ask" }, { status: "sending" }), false);
});
