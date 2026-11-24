"use client";

/** The Annotate button in the address pill, left of the bookmark star:
 *  it turns the page's annotate mode on and off. Where the page cannot be
 *  marked up it stays in place, unavailable, and its tip says why. */
import { SolarIcon } from "@/components/solar-icons";
import { cn } from "@/lib/utils";
import { useTranslation } from "@/lib/i18n";
import styles from "../browser.module.css";
import { addressStarButton, ToolbarTip } from "./browser-chrome";

export function AnnotateButton({
  active = false,
  unavailable,
  onToggle,
}: {
  active?: boolean;
  /** Why the page cannot be annotated here; the button is then inert. */
  unavailable?: string;
  onToggle?: () => void;
}) {
  const { text } = useTranslation();
  const label = text("Annotate", "批注");
  return (
    <ToolbarTip label={unavailable || label}>
      <button
        type="button"
        className={cn(addressStarButton(), styles.webAddressAnnotate)}
        data-on={active || undefined}
        aria-pressed={unavailable ? undefined : active}
        aria-disabled={unavailable ? true : undefined}
        aria-label={label}
        onClick={unavailable ? undefined : onToggle}
      >
        <SolarIcon name="pen" size={14} motionPreset={unavailable ? "none" : "pop"} />
      </button>
    </ToolbarTip>
  );
}
