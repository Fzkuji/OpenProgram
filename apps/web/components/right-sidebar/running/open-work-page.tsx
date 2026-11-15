"use client";

/** One thing the agent still owes the reader. */
import { removeOpenWork, setOpenWorkStatus } from "@/lib/chat/conversation-work";
import { updatedTime } from "../debugger/debugger-presentation";
import type { ActivityPageProps } from "./activity-page-types";
import { CircleCheck, RotateCcw, Trash2 } from "lucide-react";
import { activityGoneFrame, ActivityPageFrame } from "./activity-page";
import { ActivityActionRow, ActivityDetail, ActivityKeyValue, ActivityNote } from "./activity-page-row";
import { Glyph } from "./running-rows";
import { when } from "./running-format";

/** Longer than a page head shows on one line, so the whole item is repeated below. */
const LONG_ITEM = 32;

export function OpenWorkPage({ target, context }: ActivityPageProps<"work">) {
  const { text, sessionId, work, pendingKey } = context;
  const item = work?.open_work.find(entry => entry.id === target.workId);
  if (!item) return work ? activityGoneFrame(text) : <ActivityPageFrame kind={<Glyph name="checklist" tone="plan" large />} title={text("Open work", "未完成事项")} status={text("Loading…", "正在读取…")} axis={null}>{null}</ActivityPageFrame>;
  const done = item.status === "done";
  const statusKey = `work-status:${item.id}`;
  const removeKey = `work-remove:${item.id}`;
  const toggle = () => void context.act(() => setOpenWorkStatus(sessionId, item.id, done ? "open" : "done"), text("Could not change it.", "未能修改。"), statusKey);
  const remove = async () => { if (await context.act(() => removeOpenWork(sessionId, item.id), text("Could not remove it.", "未能删除。"), removeKey)) context.close(); };
  return <ActivityPageFrame kind={<Glyph name="checklist" tone="plan" large />} title={item.text}
    status={[done ? text("Done", "已完成") : text("Open", "未完成"), when(done ? item.done_at ?? item.updated_at : item.updated_at, text)].join(" · ")} tone={done ? "ok" : "muted"} axis={null}>
    <ActivityActionRow icon={done ? RotateCcw : CircleCheck} label={done ? text("Reopen", "重新打开") : text("Mark done", "标为完成")}
      note={done ? text("Puts it back in its instructions", "重新写回它的指令") : text("Takes it out of its instructions", "从它的指令里拿掉")}
      disabled={pendingKey !== null && pendingKey !== statusKey} busy={pendingKey === statusKey} onClick={toggle} />
    <ActivityActionRow icon={Trash2} label={text("Remove", "删除")} note={text("Back to Activity afterwards", "删除后回到运行记录")}
      disabled={pendingKey !== null && pendingKey !== removeKey} busy={pendingKey === removeKey} onClick={() => void remove()} />
    <ActivityDetail>
      <ActivityNote>{text("What the agent still owes you in this conversation. Open items are part of its instructions on every turn here.", "Agent 在这个会话里仍欠你的事。未完成的事项每一轮都写在它的指令里。")}</ActivityNote>
      {item.text.length > LONG_ITEM ? <ActivityKeyValue label={text("Item", "事项")} value={item.text} /> : null}
      {item.note ? <ActivityKeyValue label={text("Note", "备注")} value={item.note} /> : null}
      <ActivityKeyValue label={text("Created", "创建时间")} value={updatedTime(item.created_at)} />
    </ActivityDetail>
  </ActivityPageFrame>;
}
