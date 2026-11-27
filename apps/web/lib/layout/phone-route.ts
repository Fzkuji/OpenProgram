"use client";

/**
 * A page shown inside the phone app (Settings, Agents and the other
 * pages with a list and its content) under the app's own navigation bar
 * (components/phone/phone-app.tsx). The page's header does not draw a
 * second bar: on its content page, the rail's toggle names the list it
 * goes back to, and the app's Back shows that name and goes there.
 */

import { createContext } from "react";

export interface PhoneRouteBack {
  /** The list's name, e.g. "Settings". */
  label: string;
  /** Show the list again. */
  open: () => void;
}

/** Set inside the phone app: what Back goes to, or null for the app's own. */
export const PhoneRouteContext = createContext<((back: PhoneRouteBack | null) => void) | null>(null);

/** The title of the page header a rail toggle sits in. */
export const PageTitleContext = createContext<string | undefined>(undefined);
