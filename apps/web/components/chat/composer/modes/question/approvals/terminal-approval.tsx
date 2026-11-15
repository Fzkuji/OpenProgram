"use client";

/** The body of a terminal_use approval: which terminal tab, what state it is
 *  in, and exactly what the agent would type, laid out like every other
 *  approval. */
import { SolarIcon } from "@/components/solar-icons";
import { Badge } from "@/components/ui/badge";
import { useTerminalResources } from "@/lib/desktop/terminal-resources";
import { useTranslation } from "@/lib/i18n";

import { OperationCode } from "./operation-code";
import approvalStyles from "../../approval/approval-mode.module.css";
import questionStyles from "../question-mode.module.css";
import styles from "./computer-use-approval.module.css";

const AGENT_TERMINAL_PREFIX = "terminal-resource:";

type Args = Record<string, unknown>;

/** Control characters spelled out, so a carriage return or Ctrl-C is visible. */
function visibleInput(data: string): string {
  return data.replace(/\r\n|\r|\n/g, "⏎\n").replace(/\x03/g, "^C")
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, (c) => `\\x${c.charCodeAt(0).toString(16).padStart(2, "0")}`);
}

export function TerminalApproval({ args }: { args: Args }) {
  const { text } = useTranslation();
  const terminalId = typeof args.terminal_id === "string" ? args.terminal_id : "";
  const resource = useTerminalResources(s => s.rows[terminalId]);
  const action = typeof args.action === "string" ? args.action : "";
  const data = typeof args.data === "string" ? args.data : "";
  const yours = !!terminalId && !terminalId.startsWith(AGENT_TERMINAL_PREFIX);
  const name = resource?.preset === "claude" ? "Claude Code" : text("Terminal", "终端");
  const whose = yours ? text(`your ${name} tab`, `你的${name}标签页`) : text(`the agent's ${name} tab`, `agent 的${name}标签页`);
  const question = action === "input" ? text(`Type into ${whose}?`, `要在${whose}里输入吗？`)
    : action === "interrupt" ? text(`Interrupt the command in ${whose}?`, `要中断${whose}里的命令吗？`)
      : action === "close" ? text(`Close ${whose}?`, `要关闭${whose}吗？`)
        : text(`Use ${whose}?`, `要使用${whose}吗？`);
  const integration = resource?.shell_integration;
  const cwd = integration?.cwd?.value || resource?.start_cwd || "";
  const state = !resource ? text("Tab not found in this window", "这个窗口里找不到该标签页")
    : resource.status !== "running" ? text(`Shell ${resource.status}`, `Shell 已${resource.status === "exited" ? "退出" : "停止"}`)
      : integration?.cycle?.phase === "running" ? text("A command is running", "有命令正在运行")
        : integration?.status === "ready" ? text("Waiting at the prompt", "停在提示符")
          : text("Running", "运行中");
  const effect = yours
    ? text("You opened this tab, so it may hold your sign-ins and work in progress.",
      "这个标签页是你开的，里面可能有你的登录状态和正在做的事")
    : text("The agent opened this tab for its own work.", "这个标签页是 agent 自己开的");
  return (
    <>
      <div className={styles.head}>
        <SolarIcon name="programming" size={18} aria-hidden="true" />
        <div className={questionStyles.prompt}>{question}</div>
      </div>
      <div className={styles.badges}>
        <Badge variant="secondary">{state}</Badge>
        {cwd && <Badge variant="outline">{cwd}</Badge>}
        {resource?.human_input_pending && <Badge variant="outline">{text("You have unfinished input", "你有没输完的内容")}</Badge>}
      </div>
      {action === "input" && data ? <OperationCode value={visibleInput(data)} language="text" /> : null}
      <div className={questionStyles.prompt}>{effect}</div>
      <details className={approvalStyles.executionDetails}>
        <summary><SolarIcon name="alt-arrow-right" size={16} aria-hidden="true" />{text("Execution details", "查看执行详情")}</summary>
        <OperationCode value={JSON.stringify(args, null, 2)} language="json" />
      </details>
    </>
  );
}
