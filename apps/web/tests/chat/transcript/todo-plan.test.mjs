import assert from "node:assert/strict";
import test from "node:test";

const {
  normalizePlans, carryUnfinishedPlan, placedAnchor, planShown, planWindow, plansByRow, turnState, useTodoStore,
} = await import("../../../lib/chat/transcript/todo-plan.ts");

// A placed plan as the backend sends it (todo/shared.py place_plan).
const placed = (anchor, planId, statuses, updated = 1) => ({
  anchor_id: anchor, plan_id: planId, scope: "", updated_at: updated,
  todos: statuses.map((status, i) => ({ id: String(Number(planId) + i), subject: `task ${i}`, status })),
});

test("malformed plans and rows are dropped and unknown statuses read as to do", () => {
  const plans = normalizePlans([
    { anchor_id: "a_reply", plan_id: "1", todos: [
      { id: "1", subject: "  ship it  ", status: "weird" },
      { id: "2", subject: "" },
      null,
      { id: 3, subject: "numeric id", status: "completed" },
    ] },
    { anchor_id: "", plan_id: "9", todos: [{ id: "9", subject: "no turn" }] },
    { anchor_id: "b_reply", plan_id: "5", todos: [] },
    "nope",
  ]);
  assert.equal(plans.length, 1);
  assert.deepEqual(plans[0].todos, [
    { id: "1", subject: "ship it", status: "pending" },
    { id: "3", subject: "numeric id", status: "completed" },
  ]);
  assert.deepEqual(normalizePlans("nope"), []);
});

test("a plan goes under the reply of its turn, never after the last row", () => {
  const plans = normalizePlans([placed("t1_reply", "1", ["completed", "pending"])]);
  const rows = ["t1", "t1_reply", "t2", "t2_reply", "t3", "t3_reply"];
  const at = plansByRow(plans, rows, {});
  assert.deepEqual([...at.keys()], ["t1_reply"]);
  assert.equal(at.get("t3_reply"), undefined, "later turns push it up instead of carrying it along");
});

test("each turn that changed the plan keeps its own copy, in placing order", () => {
  const plans = normalizePlans([
    placed("t1_reply", "1", ["pending", "pending"]),
    placed("t2_reply", "1", ["completed", "in_progress"]),
    placed("t2_reply", "3", ["pending"]),
  ]);
  const at = plansByRow(plans, ["t1_reply", "t2_reply"], {});
  assert.deepEqual(at.get("t1_reply").map((p) => p.todos.map((t) => t.status)), [["pending", "pending"]]);
  assert.deepEqual(at.get("t2_reply").map((p) => p.planId), ["1", "3"], "two plans in one turn both show");
});

test("a plan whose turn is on another branch or not loaded does not show", () => {
  const plans = normalizePlans([placed("old_version_reply", "1", ["pending"]), placed("job_user_reply", "2", ["pending"])]);
  assert.equal(plansByRow(plans, ["t1_reply"], {}).size, 0);
});

test("a reply a restart cut off hands its plan to the reply that carried it on", () => {
  const messages = {
    cut_reply: { id: "cut_reply", role: "assistant", content: "", continuedBy: "next_reply" },
    next_reply: { id: "next_reply", role: "assistant", content: "" },
  };
  assert.equal(placedAnchor("cut_reply", messages), "next_reply");
  assert.equal(placedAnchor("cut_reply", { cut_reply: messages.cut_reply }), "cut_reply", "until the continuation is loaded");
  const at = plansByRow(normalizePlans([placed("cut_reply", "1", ["pending"])]), ["next_reply"], messages);
  assert.deepEqual([...at.keys()], ["next_reply"]);
});

test("only a turn still running counts as live", () => {
  for (const status of ["pending", "streaming", "running", "cancelling"]) {
    assert.equal(turnState({ status }, false), "running", status);
    assert.equal(turnState({ status }, true), "ended", `${status} with a terminal execution`);
  }
  assert.equal(turnState({ status: "cancelled" }, false), "stopped");
  assert.equal(turnState({ status: "interrupted" }, false), "stopped");
  assert.equal(turnState({ status: "paused" }, false), "stopped");
  assert.equal(turnState({ status: "error" }, false), "failed");
  assert.equal(turnState({ status: "done" }, false), "ended");
  assert.equal(turnState(undefined, false), "ended");
});

const make = (...statuses) => ({ scope: "", todos: statuses.map((status, i) => ({ id: String(i), subject: "s", status })) });

test("what stays after a turn ends", () => {
  assert.equal(planShown(make("completed"), "running", false), true);
  assert.equal(planShown(make("pending"), "ended", true), false);
  assert.equal(planShown(make("pending"), "ended", true, 1), true);
  assert.equal(planShown(make("in_progress"), "stopped", true), true);
  assert.equal(planShown(make("in_progress"), "failed", false), false);
  assert.equal(planShown(make("completed"), "stopped", true), false);
});

test("a running turn without a plan carries the unfinished one", () => {
  const byRow = new Map();
  const live = { r: { role: "assistant", status: "running" } };
  carryUnfinishedPlan(byRow, [make("completed", "pending")], ["r"], live);
  assert.equal(byRow.get("r")[0].stale, true);
  const done = new Map();
  carryUnfinishedPlan(done, [make("completed")], ["r"], live);
  assert.equal(done.size, 0);
  const idle = new Map();
  carryUnfinishedPlan(idle, [make("pending")], ["r"], { r: { role: "assistant", status: "done" } });
  assert.equal(idle.size, 0);
});

test("the window starts one before the current step", () => {
  const todos = make(...Array.from({ length: 30 }, (_, i) => (i < 5 ? "completed" : i === 5 ? "in_progress" : "pending"))).todos;
  const view = planWindow(todos);
  assert.equal(view.hiddenBefore, 4);
  assert.equal(view.visible.length, 12);
  assert.equal(view.openAfter, 14);
});

test("the store keeps each session's placed plans apart", () => {
  useTodoStore.getState().setPlans("a", [placed("t1_reply", "1", ["pending"])]);
  useTodoStore.getState().setPlans("b", []);
  assert.equal(useTodoStore.getState().plansBySession.a.length, 1);
  assert.deepEqual(useTodoStore.getState().plansBySession.b, []);
  useTodoStore.getState().setPlans("a", undefined);
  assert.deepEqual(useTodoStore.getState().plansBySession.a, [], "a frame without plans clears them");
});
