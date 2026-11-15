import assert from "node:assert/strict";
import test from "node:test";
import { settlePendingSends } from "../../../lib/session-store/messages/pending-sends.ts";

const now = 1_800_000_000_000;
const user = (id, content, status, timestamp = now) => ({ id, role: "user", content, status, timestamp });

test("a send the load holds is replaced by the loaded turn", () => {
  const loaded = user("u1", "hello", "done", now + 800);
  assert.deepEqual(settlePendingSends([loaded, user("pending_1", "hello", "pending")], undefined), [loaded]);
  assert.deepEqual(settlePendingSends([loaded, user("pending_1", "hello", "error")], undefined), [loaded]);
});

test("a send without its ACK that the load lacks reads not sent; the one in flight keeps waiting", () => {
  const [lost] = settlePendingSends([user("pending_1", "hello", "pending")], "pending_2");
  assert.equal(lost.status, "error");
  assert.equal(lost.errorReason, "not_sent");
  const waiting = user("pending_2", "hello", "pending");
  assert.deepEqual(settlePendingSends([waiting], "pending_2"), [waiting]);
});

test("an earlier turn with the same words does not stand in for a failed send", () => {
  const earlier = user("u0", "ok", "done", now - 60_000);
  const failed = user("pending_1", "ok", "error");
  assert.deepEqual(settlePendingSends([earlier, failed], undefined), [earlier, failed]);
});
