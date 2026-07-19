export const RIGHT_RAIL_WIDTH = 49;
export const RIGHT_PANEL_DEFAULT = 320;
export const RIGHT_PANEL_MIN = 280;
export const RIGHT_PANEL_MAX = 560;
export const RIGHT_PANEL_GAP = 8;
export const RIGHT_PANEL_KEY_STEP = 16;

export type RightPanelState = { open: boolean; view: string };
export type RightPanelAction =
  | { type: "select"; view: string }
  | { type: "escape" };

export function clampPanelWidth(width: number): number {
  return Math.max(RIGHT_PANEL_MIN, Math.min(RIGHT_PANEL_MAX, width));
}

export function panelWidthAfterKey(width: number, key: string): number | null {
  if (key === "ArrowLeft") return clampPanelWidth(width + RIGHT_PANEL_KEY_STEP);
  if (key === "ArrowRight") return clampPanelWidth(width - RIGHT_PANEL_KEY_STEP);
  if (key === "Home") return RIGHT_PANEL_MIN;
  if (key === "End") return RIGHT_PANEL_MAX;
  return null;
}

type ResizeKeyEvent = {
  key: string;
  preventDefault: () => void;
};

export function handlePanelResizeKey(
  event: ResizeKeyEvent,
  width: number,
  commit: (nextWidth: number) => void,
): boolean {
  const next = panelWidthAfterKey(width, event.key);
  if (next === null) return false;
  event.preventDefault();
  commit(next);
  return true;
}

export function resolveRightPanelAction(
  state: RightPanelState,
  action: RightPanelAction,
): RightPanelState & { focusView: string | null } {
  if (action.type === "escape") {
    return state.open
      ? { ...state, open: false, focusView: state.view }
      : { ...state, focusView: null };
  }
  if (state.open && state.view === action.view) {
    return { ...state, open: false, focusView: action.view };
  }
  return { open: true, view: action.view, focusView: null };
}
