"use client";

import { ArrowLeft, ArrowRight } from "lucide-react";
import { useTranslation } from "@/lib/i18n";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { navigationTarget } from "@/lib/tabs/navigation/selectors";
import styles from "./center-tabs.module.css";

export function PageNavigation() {
  const { text } = useTranslation();
  const navigate = useCenterTabs(s => s.navigateHistory);
  const back = useCenterTabs(s => navigationTarget(s, -1) !== null);
  const forward = useCenterTabs(s => navigationTarget(s, 1) !== null);
  return <div className={styles.sessionNavigation} role="group" aria-label={text("Page navigation", "页面导航")}>
    <button type="button" disabled={!back} title={text("Back", "后退")}
      aria-label={text("Back", "后退")} onClick={() => navigate(-1)}><ArrowLeft size={15} /></button>
    <button type="button" disabled={!forward} title={text("Forward", "前进")}
      aria-label={text("Forward", "前进")} onClick={() => navigate(1)}><ArrowRight size={15} /></button>
  </div>;
}
