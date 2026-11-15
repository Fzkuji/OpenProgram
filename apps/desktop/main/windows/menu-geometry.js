"use strict";

function contextMenuRequestedX(anchor, panelW) {
  return anchor.align === "end" ? anchor.right - panelW : anchor.x;
}

/** Context-menu panel top-left, clamped to an 8px margin inside the window. */
function clampContextMenuPanel(anchor, panelW, panelH) {
  const zoom = Number.isFinite(Number(anchor.zoom)) && Number(anchor.zoom) > 0
    ? Number(anchor.zoom)
    : 1;
  const margin = 8 * zoom;
  return {
    x: Math.min(
      Math.max(margin, contextMenuRequestedX(anchor, panelW)),
      Math.max(margin, anchor.winW - panelW - margin),
    ),
    y: Math.min(
      Math.max(margin, anchor.y),
      Math.max(margin, anchor.winH - panelH - margin),
    ),
  };
}

function cascadeMenuGeometry(anchor, winW, winH, zoom) {
  const scale = Number.isFinite(Number(zoom)) && Number(zoom) > 0 ? Number(zoom) : 1;
  const anchorX = Number(anchor && anchor.x) || 0;
  const anchorY = Number(anchor && anchor.y) || 0;
  const width = Math.max(1, Math.round(winW));
  const height = Math.max(1, Math.round(winH));
  const roomBelow = winH - anchorY;
  const useBelow = anchorY >= 0
    && anchorY < winH
    && roomBelow >= Math.min(120 * scale, winH);
  const top = useBelow ? Math.round(anchorY) : 0;
  const bounds = {
    x: 0,
    y: top,
    width,
    height: useBelow ? Math.max(1, Math.round(winH - top)) : height,
  };
  const maxX = Math.max(0, bounds.width / scale - 1);
  const maxY = Math.max(0, bounds.height / scale - 1);
  return {
    bounds,
    anchor: {
      x: Math.min(Math.max(0, anchorX / scale), maxX),
      y: Math.min(Math.max(0, (anchorY - top) / scale), maxY),
    },
  };
}

module.exports = {
  contextMenuRequestedX,
  clampContextMenuPanel,
  cascadeMenuGeometry,
};
