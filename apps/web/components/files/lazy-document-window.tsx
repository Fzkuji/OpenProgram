"use client";
import dynamic from "next/dynamic";
import { useTranslation } from "@/lib/i18n";
function Loading() {
  const { text } = useTranslation();
  return <div role="status">{text("Loading…", "加载中…")}</div>;
}
/** A file window needs browser storage and loads only after a file is opened. */
export const DocumentWindow = dynamic(() => import("./document-window").then((module) => module.DocumentWindow), {
  ssr: false,
  loading: Loading,
});
