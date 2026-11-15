"use client";

import type { DetailNode } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { guiCapabilityLabel, guiStatusLabel, type GuiToolView } from "../../chat/messages/execution/gui-tool-view";
import styles from "./gui-tool-detail.module.css";

export function GuiToolDetail({ node, view }: { node: DetailNode; view: GuiToolView }) {
  const { text } = useTranslation();
  const emptyNote = node.name !== "gui_agent" ? ""
    : view.status === "running" ? text("No recorded steps yet.", "暂无已记录步骤。")
    : text("No operation steps were run.", "没有执行任何操作步骤。");
  return <section className={styles.activity} aria-label={text("GUI activity", "GUI 操作记录")}>
    <div className="flex justify-between items-baseline gap-[8px]">
      <strong>{guiCapabilityLabel(node.name, text)}</strong>
      <span className={styles.status} data-gui-status={view.status}>{guiStatusLabel(view.status, text)}</span>
    </div>
    {(view.blocker || view.handoff) && <div className={styles.handoff}>
      {view.blocker && <p>{view.blocker}</p>}
      {view.handoff && <p><strong>{text("User action", "用户操作")}: </strong>{view.handoff}</p>}
    </div>}
    {view.summary && <p className={styles.summary}>{view.summary}</p>}
    {view.steps.length > 0 ? <ol className={styles.steps}>{view.steps.map((step, i) => <li key={i}>
      <div className="flex justify-between items-baseline gap-[8px]">
        <span className={styles.stepName}>
          {guiCapabilityLabel(step.capability, text)}
          {step.target && <span className={styles.target}> · {step.target}</span>}
        </span>
        <span className={styles.status} data-gui-status={step.status}>{guiStatusLabel(step.status, text)}</span>
      </div>
      {step.reason && <p className={styles.reason}>{step.reason}</p>}
    </li>)}</ol> : emptyNote && <p className={styles.empty}>{emptyNote}</p>}
  </section>;
}
