"use client";

/** The body of a computer_use approval, laid out like every other approval:
 *  the question, the step and app as badges, what approving means, and the
 *  raw arguments folded into the details. */
import { SolarIcon } from "@/components/solar-icons";
import { Badge } from "@/components/ui/badge";
import { useComputerUseApps } from "@/lib/net/computer-use-apps";
import { useTranslation } from "@/lib/i18n";

import { OperationCode } from "./operation-code";
import { AUDIO_ACTIONS, computerUseStep, needsForeground, readsWhat, setArgs, str, type Args } from "./computer-use-step";
import approvalStyles from "../../approval/approval-mode.module.css";
import questionStyles from "../question-mode.module.css";
import styles from "./computer-use-approval.module.css";

export function ComputerUseApproval({ args, allowedScopes }: { args: Args; allowedScopes?: string[] }) {
  const { text } = useTranslation();
  const query = str(args.app);
  const apps = useComputerUseApps(query ? [query] : []);
  const app = query ? apps[query] : undefined;
  const name = app?.name || query || text("the app", "这个应用");
  const step = computerUseStep(args, text);
  const reads = readsWhat(args, text);
  const audio = AUDIO_ACTIONS.has(str(args.action));
  const forward = !!app?.platform && needsForeground(args, app.platform);
  const scopes = allowedScopes ?? [];
  const scopeLine = scopes.includes("session")
    ? text(`Only for this app. After "Allow this conversation", using ${name} again in this conversation is not asked.`,
      `只针对这个应用。“允许此对话”后，本对话里再操作${name}不再询问`)
    : scopes.includes("always")
      ? text(`Only for this app. After "Always allow", ${name} is not asked again in this project.`,
        `只针对这个应用。“始终允许”后，本项目里再操作${name}不再询问`)
      : text("This approval covers this one step.", "这次批准只针对这一步");
  const effect = audio
    ? text("Only this app's own sound is recorded: not the microphone and not other apps.",
      "只录这个应用自己发出的声音，不录麦克风，也不录其他应用")
    : forward
      ? str(args.action) === "hover" && app?.platform === "linux"
        ? text("Moves your mouse pointer onto the window and leaves it there.",
          "会把你的鼠标指针移到这个窗口上，并停在那里")
        : text("Brings the window to the front for this step, then gives focus and your mouse pointer back.",
          "这一步会把窗口短暂切到前台，完成后把焦点和鼠标指针还给你")
      : text("Runs in the background: your mouse stays put, the keyboard stays yours, and no window comes to the front.",
        "在后台进行：不移动你的鼠标、不占用键盘，也不会把窗口切到前台");
  return (
    <>
      <div className={styles.head}>
        {app?.icon
          ? <img className={styles.icon} src={app.icon} alt="" aria-hidden="true" />
          : <SolarIcon name="monitor" size={18} aria-hidden="true" />}
        <div className={questionStyles.prompt}>{text(`Allow operating “${name}”?`, `允许操作「${name}」？`)}</div>
      </div>
      <div className={styles.badges}>
        <Badge variant="secondary">{step.verb}{step.target ? ` · ${step.target}` : ""}</Badge>
        {reads && <Badge variant="outline">{reads}</Badge>}
        {app?.bundle_id && <Badge variant="outline">{app.bundle_id}</Badge>}
      </div>
      <div className={questionStyles.prompt}>{effect} {scopeLine}</div>
      <details className={approvalStyles.executionDetails}>
        <summary><SolarIcon name="alt-arrow-right" size={16} aria-hidden="true" />{text("Execution details", "查看执行详情")}</summary>
        <OperationCode value={JSON.stringify(setArgs(args), null, 2)} language="json" />
      </details>
    </>
  );
}
