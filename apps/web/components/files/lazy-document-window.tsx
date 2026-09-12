"use client";
import { lazy, Suspense, type ComponentProps } from "react";
import { useTranslation } from "@/lib/i18n";
const Window = lazy(() => import("./document-window").then((module) => ({ default: module.DocumentWindow })));

/** Opening a file loads its window; ordinary chat startup does not. */
export function DocumentWindow(props: ComponentProps<typeof Window>) {
  const { text } = useTranslation();
  return <Suspense fallback={<div role="status">{text("Loading…", "加载中…")}</div>}><Window {...props} /></Suspense>;
}
