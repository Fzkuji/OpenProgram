"use client";

import type { ReactNode } from "react";
import { ChevronLeft } from "lucide-react";

import { useTranslation } from "@/lib/i18n";

import { goBack } from "./phone-nav";

/** A page's navigation bar: Back with the previous page's name, the title
 *  (and a status line under it), and one button on the right. */
export function PhoneNavBar({ back, title, status, trailing }: {
  /** The previous page's name; no Back button without one. */
  back?: string;
  title?: ReactNode;
  status?: ReactNode;
  trailing?: ReactNode;
}) {
  const { text } = useTranslation();
  return (
    <header className="phone-app-nav">
      <div className="phone-app-nav-side">
        {back !== undefined ? (
          <button type="button" className="phone-app-back" onClick={goBack} aria-label={text(`Back to ${back}`, `返回${back}`)}>
            <ChevronLeft size={26} strokeWidth={2} aria-hidden="true" />
            <span>{back}</span>
          </button>
        ) : null}
      </div>
      <div className="phone-app-nav-title">
        {title ? <b>{title}</b> : null}
        {status ? <span>{status}</span> : null}
      </div>
      <div className="phone-app-nav-side phone-app-nav-trailing">{trailing}</div>
    </header>
  );
}
