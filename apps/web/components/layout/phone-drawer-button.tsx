"use client";

/**
 * The header button that slides the sidebar drawer in, shown only in the
 * phone layout (app/styles/base/phone.css hides `.phone-only` above
 * 760px). Kept apart from phone-shell.tsx so page headers that show it do
 * not pull in the session store.
 */

import { PanelLeftOpenIcon } from "../animated-icons";
import { sidebarToggleClass } from "../sidebar/nav-classes";
import { useTranslation } from "@/lib/i18n";
import { isPhoneViewport } from "@/lib/layout/phone";
import { sidebarDrawer } from "@/lib/layout/sidebar-drawer";

export function PhoneDrawerButton() {
  const { text } = useTranslation();
  return (
    <button
      type="button"
      className={`${sidebarToggleClass} phone-only phone-header-button`}
      aria-label={text("Show sidebar", "显示侧边栏")}
      onClick={() => {
        if (isPhoneViewport()) sidebarDrawer.open();
      }}
    >
      <PanelLeftOpenIcon size={20} />
    </button>
  );
}
