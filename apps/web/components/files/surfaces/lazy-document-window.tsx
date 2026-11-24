"use client";
import { lazy, Suspense, type ComponentProps } from "react";
const Window = lazy(() => import("./document-window").then((module) => ({ default: module.DocumentWindow })));

/** Opening a file loads its window; ordinary chat startup does not. */
export function DocumentWindow(props: ComponentProps<typeof Window>) {
  return <Suspense fallback={null}><Window {...props} /></Suspense>;
}
