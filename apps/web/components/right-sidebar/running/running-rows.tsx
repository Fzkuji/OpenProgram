"use client";

/** The Running panel's building blocks: status glyphs, the status pill and one row. */
import { type ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { SolarIcon, type SolarIconName } from "@/components/solar-icons";
import { BotGlyph } from "@/components/avatar/bot/bot-glyph";
import { type ToolTone } from "@/components/chat/messages/execution/tool-presentation";
import { Button } from "@/components/ui/button";
import { HoverTip } from "@/components/ui/tooltip";
import styles from "./running-panel.module.css";

export type Text = (en: string, zh: string) => string;

export type Tone = "warn" | "run" | "ok" | "err" | "muted";

const DOT: Record<Tone, string> = {
  warn: "--warn", run: "--neutral pulse-opacity", ok: "--ok", err: "--err", muted: "is-muted",
};

/** The small status dot in a tone, as the rows' status lines draw it. */
export function StatusDot({ tone }: { tone: Tone }) {
  return <span className={`indicator-dot sm ${DOT[tone]}`} aria-hidden="true" />;
}

export function Glyph({ name, tone, large }: { name: SolarIconName; tone: ToolTone | "run"; large?: boolean }) {
  return <span className={styles.glyph} data-tone={tone} data-large={large ? "1" : undefined} aria-hidden="true">
    <SolarIcon name={name} size={large ? 22 : 16} motionPreset="none" />
  </span>;
}

/** An agent: its character, flat, in the folder colour (a branch's own:
 *  its lane colour), a size up from the other rows. The main agent wears
 *  the conversation Agent's character, a sub-agent the one its name gives,
 *  as on the graph. */
export function AgentGlyph({ shape, small, color }: { shape: string; small?: boolean; color?: string | null }) {
  return <span className={styles.agentGlyph} data-small={small ? "1" : undefined} style={color ? { color } : undefined} aria-hidden="true">
    <BotGlyph shape={shape} size={small ? 16 : 22} />
  </span>;
}

export function Status({ tone, children, dot = true }: { tone: Tone; children: ReactNode; dot?: boolean }) {
  return <span className={styles.status} data-tone={tone}>
    {dot && <span className={`indicator-dot sm ${DOT[tone]}`} aria-hidden="true" />}
    <span className={styles.statusText}>{children}</span>
  </span>;
}

/** The rows under a row fold and unfold from the chevron after its name
 *  (the section headers' disclosure chevron) and with Left and Right. */
export interface RowDisclosure { open: boolean; onToggle: () => void; label: string }

/** ``sub``: a monospace line under the title (a program's command);
 *  ``extra``: what goes under the status (a program's progress bar). */
/** A row: its glyph, with a red count on the corner when something
 *  waits for the reader (the Dock's badge), the name, and one line of
 *  status coloured by its tone (running blue, failed red, else muted). */
export function Row({ glyph, title, badge, sub, status, tone, onOpen, extra, count = 0, disclosure }: {
  glyph: ReactNode; title: string; badge?: ReactNode; sub?: string; status: ReactNode; tone: Tone; onOpen: () => void; extra?: ReactNode;
  /** How many things wait for the reader here. */
  count?: number;
  disclosure?: RowDisclosure;
}) {
  // The chevron is part of the row button, not a button inside it: a click
  // that lands on it folds, any other click opens the row.
  const chevron = disclosure ? <HoverTip label={disclosure.label}>
    <span className={styles.disclosure} data-disclosure="" aria-hidden="true">
      <ChevronRight size={14} style={{ transform: disclosure.open ? "rotate(90deg)" : "none", transition: "transform var(--motion-medium) var(--ease-standard)" }} />
    </span>
  </HoverTip> : null;
  return <Button variant="ghost" className={styles.row} aria-expanded={disclosure ? disclosure.open : undefined}
    onClick={event => {
      if (disclosure && (event.target as Element).closest?.("[data-disclosure]")) disclosure.onToggle();
      else onOpen();
    }}
    onKeyDown={disclosure ? event => {
      if (event.altKey || event.metaKey || event.ctrlKey) return;
      if ((event.key === "ArrowLeft" && disclosure.open) || (event.key === "ArrowRight" && !disclosure.open)) { event.preventDefault(); disclosure.onToggle(); }
    } : undefined}>
    <span className="relative flex-none grid place-items-center">
      {glyph}
      {count > 0 && <span className={styles.countBadge}>{count > 99 ? "99+" : count}</span>}
    </span>
    <span className="grid gap-[1px] min-w-0 flex-1">
      {badge || chevron ? <span className="flex items-center gap-[6px] min-w-0"><span className={styles.name}>{title}</span>{chevron}{badge}</span> : <span className={styles.name}>{title}</span>}
      {sub && <span className={styles.sub}>{sub}</span>}
      {status ? <Status tone={tone} dot={false}>{status}</Status> : null}
      {extra}
    </span>
    <ChevronRight size={14} className={styles.chevron} aria-hidden="true" />
  </Button>;
}
