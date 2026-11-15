import assert from "node:assert/strict";
import test from "node:test";

import {
  recentsConversationsEqual,
  runningIdSetEqual,
} from "../../components/sidebar/sessions/recents-select.ts";

test("recents equality ignores non-list fields", () => {
  const a = {
    s1: { title: "A", unread: true, workspace_alignment: { status: "aligned" } },
  };
  const b = {
    s1: { title: "A", unread: true, workspace_alignment: { status: "mismatch" } },
  };
  assert.equal(recentsConversationsEqual(a, b), true);
});

test("recents equality sees title / unread / recency", () => {
  const base = { s1: { title: "A", unread: false, updated_at: 1 } };
  assert.equal(
    recentsConversationsEqual(base, { s1: { title: "B", unread: false, updated_at: 1 } }),
    false,
  );
  assert.equal(
    recentsConversationsEqual(base, { s1: { title: "A", unread: true, updated_at: 1 } }),
    false,
  );
  assert.equal(
    recentsConversationsEqual(base, { s1: { title: "A", unread: false, updated_at: 2 } }),
    false,
  );
});

test("recents equality sees membership change", () => {
  assert.equal(
    recentsConversationsEqual({ s1: { title: "A" } }, { s1: { title: "A" }, s2: { title: "B" } }),
    false,
  );
});

test("running set equality ignores task payload", () => {
  assert.equal(
    runningIdSetEqual({ s1: { msg_id: "a" } }, { s1: { msg_id: "b", stream_events: [1] } }),
    true,
  );
  assert.equal(runningIdSetEqual({ s1: {} }, { s1: {}, s2: {} }), false);
  assert.equal(runningIdSetEqual({ s1: {} }, {}), false);
});

