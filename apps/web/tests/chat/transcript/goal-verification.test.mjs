import assert from "node:assert/strict";
import test from "node:test";
import { verificationSummary } from "../../../lib/chat/decisions/goal-verification.ts";

const text = (_en, zh) => zh;
test("only the accepted backend verdict produces a completion summary", () => {
  assert.equal(verificationSummary({id:"v", status:"pending"}, "done", text), "正在验收目标…");
  assert.equal(verificationSummary({id:"v", status:"unavailable"}, "done", text), "验收结果未确认");
  assert.equal(verificationSummary({id:"v", status:"pending"}, "cancelled", text), "验收已取消");
  assert.equal(verificationSummary({id:"v", status:"met", requirements:[{id:"todo:0",verdict:"met"},{id:"todo:1",verdict:"met"}]}, "done", text), "目标已完成");
  assert.equal(verificationSummary({id:"v", status:"unmet"}, "done", text), "目标验收未通过");
});
