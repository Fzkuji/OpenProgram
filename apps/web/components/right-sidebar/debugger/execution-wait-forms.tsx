"use client";

/** Answers a question or approval from here, for waits the chat card cannot
 *  show: a JSON form, several answers, a choice, or an approval scope. */
import { useState } from "react";
import type { DurableWait } from "@/lib/execution/execution-debugger";
import { buildWaitAnswer } from "@/lib/execution/execution-wait";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SimpleSelect } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useTranslation } from "@/lib/i18n";
import styles from "./debugger-panel.module.css";

export type WaitResponse = {
  wait_id: string;
  execution_id: string;
  claim_generation: number;
  outcome: "answer" | "decline";
  value?: unknown;
};

const waitKey = (wait: DurableWait) => `${wait.wait_id}:${wait.claim_generation}`;

export function ExecutionWaitForms({ waits, onRespond }: { waits: DurableWait[]; onRespond?: (input: WaitResponse) => Promise<void> | void }) {
  const { text } = useTranslation();
  const [waitValues, setWaitValues] = useState<Record<string, string>>({});
  const [approvalScopes, setApprovalScopes] = useState<Record<string, string>>({});
  const [pendingWaits, setPendingWaits] = useState<Set<string>>(new Set());
  const [waitError, setWaitError] = useState<string | null>(null);

  async function respondWait(wait: DurableWait, outcome: "answer" | "decline") {
    if (!onRespond) return;
    if (pendingWaits.has(waitKey(wait))) return;
    setWaitError(null);
    setPendingWaits((current) => new Set(current).add(waitKey(wait)));
    try {
      const waitValue = waitValues[waitKey(wait)] || "";
      const approvalScope = approvalScopes[waitKey(wait)] || "";
      let answer: unknown = waitValue.trim();
      if (outcome === "answer") {
        if (wait.kind === "form" || wait.kind === "ask_many" || wait.request?.multi) {
          try {
            answer = JSON.parse(waitValue);
          } catch {
            throw new Error("Enter a valid JSON object or array.");
          }
        }
        answer = buildWaitAnswer(wait, answer, approvalScope);
      }
      await onRespond({
        wait_id: wait.wait_id,
        execution_id: wait.execution_id,
        claim_generation: wait.claim_generation,
        outcome,
        value: outcome === "answer" ? answer : undefined,
      });
      setWaitValues((current) => ({ ...current, [waitKey(wait)]: "" }));
      setApprovalScopes((current) => ({ ...current, [waitKey(wait)]: "" }));
    } catch (error) {
      setWaitError(error instanceof Error ? error.message : "Wait response failed.");
    } finally {
      setPendingWaits((current) => { const next = new Set(current); next.delete(waitKey(wait)); return next; });
    }
  }

  if (waits.length === 0) return null;
  return <section className={styles.card}>
    <div className={`${styles.cardHeader} flex flex-wrap items-baseline justify-between gap-[8px] mb-[8px]`}><h4>Question and approval waits</h4><span>{waits.length} open</span></div>
    {waits.map((wait) => (
      <div className={styles.waitRow} key={wait.wait_id}>
        <div><strong>{wait.kind === "approval" ? text("Approval needed", "需要授权") : text("Answer needed", "需要回答")}</strong><p>{wait.request?.prompt || text("This execution is waiting for your response.", "此执行正在等待你的回复。")}</p></div>
        <div className={styles.waitControls}>
          {wait.kind === "approval" ? (
            <SimpleSelect aria-label="Approval scope" placeholder="Choose approval scope" value={approvalScopes[waitKey(wait)] || ""} onValueChange={(value) => setApprovalScopes((current) => ({ ...current, [waitKey(wait)]: value }))} disabled={!onRespond || pendingWaits.has(waitKey(wait))} options={(wait.policy_snapshot?.allowed_scopes ?? ["once"]).map((scope) => ({ value: scope, label: scope }))} />
          ) : wait.kind === "form" ? (
            <Textarea aria-label="Form answer" value={waitValues[waitKey(wait)] || ""} onChange={(event) => setWaitValues((current) => ({ ...current, [waitKey(wait)]: event.target.value }))} placeholder={JSON.stringify(Object.fromEntries(Object.entries(wait.request?.schema || {}).map(([name, field]) => [name, field.default ?? ""])), null, 2)} disabled={!onRespond || pendingWaits.has(waitKey(wait))} />
          ) : wait.kind === "ask_many" || wait.request?.multi ? (
            <Textarea aria-label={`${wait.kind} answer`} value={waitValues[waitKey(wait)] || ""} onChange={(event) => setWaitValues((current) => ({ ...current, [waitKey(wait)]: event.target.value }))} placeholder={'["answer 1", ["answer 2"]]'} disabled={!onRespond || pendingWaits.has(waitKey(wait))} />
          ) : wait.request?.options?.length ? (
            <SimpleSelect aria-label={`${wait.kind} answer`} placeholder="Choose an answer" value={waitValues[waitKey(wait)] || ""} onValueChange={(value) => setWaitValues((current) => ({ ...current, [waitKey(wait)]: value }))} disabled={!onRespond || pendingWaits.has(waitKey(wait))} options={wait.request.options.map((option) => ({ value: option, label: option }))} />
          ) : (
            <Input aria-label={`${wait.kind} answer`} value={waitValues[waitKey(wait)] || ""} onChange={(event) => setWaitValues((current) => ({ ...current, [waitKey(wait)]: event.target.value }))} placeholder="Answer" disabled={!onRespond || pendingWaits.has(waitKey(wait))} />
          )}
          <Button variant="ghost" type="button" onClick={() => void respondWait(wait, "answer")} disabled={!onRespond || pendingWaits.has(waitKey(wait)) || (wait.kind === "approval" && !approvalScopes[waitKey(wait)])}>Answer</Button><Button variant="ghost" type="button" onClick={() => void respondWait(wait, "decline")} disabled={!onRespond || pendingWaits.has(waitKey(wait))}>Decline</Button>
        </div>
      </div>
    ))}
    {waitError && <div className={styles.formError} role="alert">{waitError}</div>}
  </section>;
}
