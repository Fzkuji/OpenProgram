"use client";

/** The body of a computer_use approval, laid out like every other approval:
 *  the question, the step and what it operates as badges, what approving
 *  means, and the raw arguments folded into the details. It asks about one
 *  application, the whole screen of this computer, or the user's own
 *  browser; a machine that was set up is operated without asking. */
import { SolarIcon } from "@/components/solar-icons";
import { Badge } from "@/components/ui/badge";
import { USER_BROWSERS } from "@/lib/computer-use/browsers";
import type { CallTarget } from "@/lib/computer-use/computer-use-call";
import { useComputerUseApps } from "@/lib/net/computer-use-apps";
import { useTranslation } from "@/lib/i18n";

import { OperationCode } from "./operation-code";
import { AUDIO_ACTIONS, computerUseStep, needsForeground, readsWhat, setArgs, str, type Args } from "./computer-use-step";
import approvalStyles from "../../approval/approval-mode.module.css";
import questionStyles from "../question-mode.module.css";
import styles from "./computer-use-approval.module.css";

export function ComputerUseApproval({ args, target, allowedScopes }: { args: Args; target: CallTarget; allowedScopes?: string[] }) {
  const { text } = useTranslation();
  const query = target.family === "window" ? str(args.app) : "";
  const apps = useComputerUseApps(query ? [query] : []);
  const app = query ? apps[query] : undefined;
  const screen = target.family === "screen";
  const browser = USER_BROWSERS[target.browser]?.label ?? "";
  // What is asked about, and the words for "only this one" in the scope line.
  const name = screen ? text("the whole screen of this computer", "这台电脑的整个屏幕")
    : browser ? text(`your ${browser}`, `你的 ${browser}`)
    : app?.name || query || text("the app", "这个应用");
  const only = screen ? text("Only for the whole screen.", "只针对整个屏幕。")
    : browser ? text(`Only for ${browser}.`, `只针对 ${browser}。`) : text("Only for this app.", "只针对这个应用。");
  const step = computerUseStep(args, text, screen ? "screen" : browser ? "page" : "window");
  const reads = readsWhat(args, text);
  const audio = AUDIO_ACTIONS.has(str(args.action));
  const forward = !!app?.platform && needsForeground(args, app.platform);
  const scopes = allowedScopes ?? [];
  const scopeLine = scopes.includes("session")
    ? text(`${only} After "Allow this conversation", using ${name} again in this conversation is not asked.`,
      `${only}“允许此对话”后，本对话里再操作${name}不再询问`)
    : scopes.includes("always")
      ? text(`${only} After "Always allow", ${name} is not asked again in this project.`,
        `${only}“始终允许”后，本项目里再操作${name}不再询问`)
      : text("This approval covers this one step.", "这次批准只针对这一步");
  const effect = screen
    ? text("Uses your mouse pointer and keyboard while it runs; OpenProgram's windows and refused apps stay out of reach.",
      "运行时会使用你的鼠标指针和键盘；OpenProgram 的窗口和被拒绝的应用不会被操作")
    : browser
      ? text("Works in your own browser, signed in as you.", "在你自己的浏览器里进行，用的是你的登录状态")
    : audio
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
          : <SolarIcon name={browser ? "global" : "monitor"} size={18} aria-hidden="true" />}
        <div className={questionStyles.prompt}>{screen || browser ? text(`Allow operating ${name}?`, `允许操作${name}？`)
          : text(`Allow operating “${name}”?`, `允许操作「${name}」？`)}</div>
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
