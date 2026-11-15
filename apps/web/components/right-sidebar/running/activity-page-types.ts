/** The contract between the Activity panel and its second-level pages. */
import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import type { ExecutionSnapshot } from "@/lib/execution/execution-debugger";
import type { ActivityRun } from "@/lib/net/execution/activity-runs-client";
import type { useExecutionDebugger } from "@/lib/execution/use-execution-debugger";
import type { useManagedProcesses } from "@/lib/execution/use-managed-processes";
import type { useConversationWork } from "@/lib/chat/conversation-work";

export type ActivityTone = "muted" | "run" | "warn" | "err" | "ok";
export type Translate = (english: string, chinese: string) => string;

export type ActivityPageTarget =
  | { kind: "agent"; scopeKey: string }
  | { kind: "program"; processId: string }
  | { kind: "command"; callId: string }
  | { kind: "routine"; taskId: string }
  | { kind: "work"; workId: string };

export interface ActivityScope {
  branchId?: string; key: string; head: string | null; name: string; active: boolean;
  color: string | null; updatedAt: number | null; members: Set<string> | null;
}
export interface ActivityWaitDecision {
  /** Empty when the chat has no card for this wait. */
  answerLabel: string; question: string; choices: string[];
  /** The status words: waiting for an answer or for approval. */
  waitLabel: string;
}

/** Built once per render by RunningPanel from the code level 1 already has. */
/** A swipe that drags the page aside: how far it has been pulled, then the
 *  lift, with the speed it was moving at in px a second. */
export interface PagePull { move: (distance: number) => void; end: (distance: number, speed: number) => void }

export interface ActivityPanelContext {
  sessionId: string;
  text: Translate;
  now: number;
  /** The panel is on screen; pages poll only then. */
  active: boolean;
  connected: boolean;
  controller: ReturnType<typeof useExecutionDebugger>;
  processes: ReturnType<typeof useManagedProcesses>;
  work: ReturnType<typeof useConversationWork>["work"];
  refreshWork: () => void;
  scopes: ActivityScope[];
  scopeOf: (key: string) => ActivityScope;
  scopeHeadText: (scope: ActivityScope) => { kind: string; title: string; status: string; tone: ActivityTone };
  waitingFor: (execution: ExecutionSnapshot) => ActivityWaitDecision | null;
  answerInChat: (execution: ExecutionSnapshot) => void;
  switchTo: (scope: ActivityScope) => void;
  openAgentConversation: (jobId: string) => void;
  alwaysStatus: () => { label: string; tone: ActivityTone };
  act: (run: () => Promise<unknown>, failed: string, key?: string) => Promise<boolean>;
  pendingKey: string | null;
  actionError: string | null;
  copy: (key: string, value: string) => void;
  copiedKey: string | null;
  /** Back to level 1, restoring its scroll and focus. */
  close: () => void;
  /** The page follows a swipe towards the overview, when it has one under it. */
  pull?: PagePull;
}

export interface ActivityPageProps<K extends ActivityPageTarget["kind"]> {
  target: Extract<ActivityPageTarget, { kind: K }>;
  context: ActivityPanelContext;
}

export type ActivityMark =
  | { kind: "bar"; left: number; width: number; flatEnd: boolean; tone: ActivityTone }
  | { kind: "tick"; left: number; tone: ActivityTone };

export interface ActivityRowProps {
  /** Stable within the page. Open state and focus are keyed by it. */
  id: string;
  /** Absent on one-line rows, which show only the status. */
  title?: ReactNode;
  status?: ReactNode;
  tone?: ActivityTone;
  glyph?: ReactNode;
  /** Multiples of 16. */
  indent?: number;
  /** With no time and no mark the right half is blank above a run list and
   *  keeps the grid inside one. Pass "" to draw the grid anywhere. */
  time?: string;
  mark?: ActivityMark | null;
  defaultOpen?: boolean;
  /** Called only while open. Absent: the row has no chevron and does not open. */
  children?: () => ReactNode;
}
export interface ActivityKeyValueProps {
  label: string; value: string; mono?: boolean;
  /** Lines before its own Show all; default 8. */
  clamp?: number;
  indent?: number;
}
export interface ActivityActionRowProps {
  /** A line icon, drawn 18. */
  icon: LucideIcon; label: string; note?: string;
  disabled?: boolean; busy?: boolean; indent?: number;
  onClick: () => void;
}
export interface ActivityPageFrameProps {
  /** The head's glyph, 22 wide. The page head has no kind line, so a plain string draws nothing. */
  kind: ReactNode;
  title: ReactNode;
  status?: ReactNode;
  tone?: ActivityTone;
  /** Extra head content, such as a progress bar. */
  headExtra?: ReactNode;
  /** The axis the page's plots use; null hides the Timeline half. */
  axis: import("./activity-page-geometry").ActivityAxis | null;
  children: ReactNode;
}

export type ActivityRunSource =
  | { kind: "branch"; head: string | null }
  | { kind: "task"; taskId: string };

export interface RunListProps {
  source: ActivityRunSource;
  context: ActivityPanelContext;
  /** Rendered above the first day label. */
  before?: ReactNode;
  /** Replaces the insides of a run; used for reports. Return null for the default. */
  renderInside?: (run: ActivityRun) => ReactNode | null;
  /** The axis the list computed, for the frame. */
  onAxis?: (axis: import("./activity-page-geometry").ActivityAxis) => void;
  /** What to call a report whose message kept no text. */
  reportName?: (run: ActivityRun) => string | null;
}

export interface AlwaysOnRowProps { context: ActivityPanelContext; scope: ActivityScope }
export interface ReportInsideProps { context: ActivityPanelContext; run: ActivityRun }
