// Public desktop bridge API. Implementations are grouped by responsibility.

export type {
  DesktopBrowserDataApi,
  DesktopBrowserImportApi,
  DesktopBrowserImportBookmark,
  DesktopBrowserImportProfile,
  DesktopBrowserImportResult,
  DesktopBrowserImportSource,
  DesktopContextMenuItem,
  DesktopDownloadEntry,
  DesktopDownloadsApi,
  DesktopHistoryApi,
  DesktopHistoryEntry,
  DesktopMainMenuApi,
  DesktopNativeMenuApi,
  DesktopTerminalApi,
  DesktopThemeApi,
  DesktopUpdateApi,
  DesktopUpdateRelease,
  DesktopUpdateState,
  DesktopVisibleWebView,
  DesktopWebTabApi,
  DesktopWebTabBounds,
  DesktopWebTabFindResult,
  DesktopWebTabState,
} from "@/lib/desktop/desktop-bridge-types";

export type {
  DesktopTabTransferApi,
  DesktopTransferReceipt,
} from "@/lib/desktop/desktop-transfer-types";
export {
  type DesktopReopenState,
  type DesktopBridge,
  desktopBridge,
} from "./bridge-api";
export {
  registerVisibleWebTabBounds,
  removeVisibleWebTabBounds,
  setWebTabReady,
  isWebTabReady,
  waitForWebTabReady,
  setDesktopSplitLayoutAvailable,
  isDesktopSplitLayoutAvailable,
  ensureWebView,
} from "./bridge-state";
export {
  displayPathOf,
  reconcileRestoredWebUrl,
  restoreRetainedWebViews,
  retryRestoreWebTab,
  destroyStaleWebViews,
} from "./bridge-restoration";
export {
  desktopTerminalId,
  destroyStaleTerminals,
} from "./bridge-terminals";
export {
  installDesktopMenuHandlers,
} from "./bridge-menu";
export {
  subscribeWebTabPopups,
  subscribeBrowserHumanInput,
} from "./bridge-events";
export {
  visibleWebTab,
  finalizeWebTabPreview,
  finalizeBoundWebTabActivation,
  type TurnSurfaceRef,
  type TurnWindowRef,
  type BrowserPageInventoryItem,
  type BrowserPageInventoryTabEntry,
  type BrowserPageInventorySnapshot,
  browserPageInventory,
  surfaceRefForChat,
  surfaceOriginForChat,
  restorePriorActiveTabAfterFailedWebOpen,
  closeAgentWebTabResult,
} from "./bridge-surfaces";
export {
  type AcceptedTransfer,
  acceptedTransfers,
  serializeWebViewBookkeeping,
  applyWebViewBookkeeping,
  forgetTransferredWebView,
  transferRecoveryHandlers,
  placementForDropIntent,
  buildTransferPayload,
  stageIncomingTransfer,
  handleRemoveSource,
  handleUndoDestination,
  handleTransferCommitted,
  handleTransferRolledBack,
  handleTransferRejected,
  handleFinalizeOrphaned,
  installTabTransferHandlers,
  finalizeOrphanTransferJournal,
  recoverPendingTabTransfers,
} from "./bridge-transfer";
