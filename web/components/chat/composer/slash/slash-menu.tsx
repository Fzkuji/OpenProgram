"use client";

/**
 * Slash command popover rendered above the composer textarea.
 *
 * Pure presentation — every piece of menu state comes from the
 * caller's ``useSlashMenu`` hook. Extracted from composer/index.tsx
 * so the main file isn't carrying the matches list + scroll-into-view
 * + open/close animation classes inline.
 */
import type { SlashCommand } from "./slash-commands";
import styles from "../composer.module.css";

interface SlashMenuProps {
  visible: boolean;
  closing: boolean;
  matches: SlashCommand[];
  activeIndex: number;
  onPick: (cmd: SlashCommand) => void;
}

export function SlashMenu({
  visible,
  closing,
  matches,
  activeIndex,
  onPick,
}: SlashMenuProps) {
  if (!visible) return null;
  return (
    <div
      className={`${styles.slashMenu} ${closing ? styles.closing : styles.opening}`}
    >
      {matches.map((c, i) => (
        <div
          key={c.name}
          ref={
            // Scroll the keyboard-highlighted item into view when
            // arrow nav drives it off-screen. Mouse hover no
            // longer touches activeIndex (the CSS :hover state
            // alone provides hover feedback), so this fires
            // only on keyboard moves — no more jiggle when the
            // cursor drifts onto a bottom item.
            i === activeIndex
              ? (el) => el?.scrollIntoView({ block: "nearest" })
              : undefined
          }
          className={`${styles.slashMenuItem} ${i === activeIndex ? styles.slashMenuItemActive : ""}`}
          onMouseDown={(e) => {
            e.preventDefault();
            onPick(c);
          }}
        >
          <span className={styles.slashMenuName}>{c.name}</span>
          {c.args ? (
            <>
              {" "}
              <span className={styles.slashMenuArgs}>{c.args}</span>
            </>
          ) : null}
          <div className={styles.slashMenuDesc}>{c.description}</div>
        </div>
      ))}
    </div>
  );
}
