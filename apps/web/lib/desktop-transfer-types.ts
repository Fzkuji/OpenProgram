import type {
  DesktopTransferPayload,
  TabDropPlacement,
} from "@/lib/tab-transfer-journal";

export interface DesktopTransferReceipt {
  token: string;
  reason?: string;
  discardWindowState?: boolean;
  duplicateId?: string;
  sourceId?: string;
  destinationId?: string | null;
  payload?: DesktopTransferPayload;
}

export interface DesktopTabTransferApi {
  /** Synchronous — called on pointer/mouse down, never after dragstart. */
  prepare(payload: DesktopTransferPayload): string | null;
  inspect(token: string): Promise<
    | { token: string; status: string; sourceId: string; payload: DesktopTransferPayload }
    | null
  >;
  accept(token: string, placement: TabDropPlacement): Promise<
    | {
        token: string;
        status: string;
        sourceId: string;
        destinationId: string;
        payload: DesktopTransferPayload;
        placement: TabDropPlacement;
        recordIds: string[];
      }
    | null
  >;
  reject(
    token: string,
    reason: "duplicate" | "group-full" | "invalid",
    duplicateId?: string,
  ): Promise<{ reason: string; duplicateId?: string } | null>;
  status(token: string): Promise<
    { status: string; sourceId: string; destinationId: string | null } | null
  >;
  journalOpened(token: string, role: "source" | "destination"): Promise<boolean>;
  journalFinalized(
    token: string,
    role: "source" | "destination",
    ownerWindowId?: string,
  ): Promise<boolean>;
  destinationReady(token: string, ok: boolean): Promise<boolean>;
  sourceRemoved(token: string, ok: boolean, empty: boolean): Promise<boolean>;
  destinationUndone(token: string, ok: boolean): Promise<boolean>;
  cancel(token: string): Promise<boolean>;
  /** Drop-to-place: create the torn-off window at the drop point on release
   *  and reveal it at commit. Returns the new window id, or null if the
   *  transfer did not commit. */
  detach(token: string): Promise<string | null>;
  /** Pointer-drop hit test: id of another OpenProgram window under the
   *  cursor, or null. Read-only — no transfer state changes. */
  windowAtCursor(): Promise<string | null>;
  /** Hand a prepared token to another live window so it stages the
   *  incoming transfer itself (pointer drops have no DOM drop event). */
  deliver(token: string, targetWindowId: string): Promise<boolean>;
  onStageIncoming(cb: (detail: { token: string }) => void): () => void;
  /** Cross-window drop cue: subscribes to hover-enter/leave for a drag
   *  happening in ANOTHER window. cb(true) when this window becomes the
   *  hover target, cb(false) when it stops being it. Mirrors onStageIncoming. */
  onTransferHover(cb: (entering: boolean) => void): () => void;
  claimPending(windowId: string): Promise<string | null>;
  pendingTerminal(windowId: string): Promise<Array<{
    token: string;
    status: "committed" | "rolled-back";
    role: "source" | "destination";
    windowId: string;
    orphaned: boolean;
    discardWindowState?: boolean;
  }>>;
  onRemoveSource(cb: (detail: DesktopTransferReceipt) => void): () => void;
  onUndoDestination(cb: (detail: DesktopTransferReceipt) => void): () => void;
  onCommitted(cb: (detail: DesktopTransferReceipt) => void): () => void;
  onRejected(cb: (detail: DesktopTransferReceipt) => void): () => void;
  onRolledBack(cb: (detail: DesktopTransferReceipt) => void): () => void;
  onFinalizeOrphaned(cb: (detail: {
    token: string;
    status: "committed" | "rolled-back";
    role: "source" | "destination";
    windowId: string;
    orphaned: boolean;
    discardWindowState?: boolean;
  }) => void): () => void;
}

