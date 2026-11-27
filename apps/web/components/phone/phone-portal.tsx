"use client";

import type { ReactNode } from "react";
import { createPortal } from "react-dom";

/** Framework7's popovers, sheets and popups, drawn at its root as it
 *  draws its own: inside a page's bar or a box they would sit under that
 *  box's clipping and blur. */
export function PhonePortal({ children }: { children: ReactNode }) {
  if (typeof document === "undefined") return null;
  const root = document.getElementById("framework7-root");
  return root ? createPortal(children, root) : null;
}
