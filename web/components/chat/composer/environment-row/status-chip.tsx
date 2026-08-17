"use client";

import { useEffect, useRef, useState } from "react";

import { MonitorIcon, type AnimatedNavIconHandle } from "@/components/animated-icons";
import { HoverTip } from "@/components/ui/tooltip";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { useSessionStore } from "@/lib/session-store";
import { closeAllPopovers } from "@/lib/runtime-bridge/ui";
import { useTranslation } from "@/lib/i18n";
import { ChannelMenu } from "../../top-bar/channel-menu";

/** Status chip — the old topbar StatusBadge chip form (tone-tinted
 *  chip + indicator dot + channel label, ChannelMenu popover), re-hosted
 *  in the env-chip row above the input box (Claude's "Local" position).
 *  Reads the same store slice the tab-strip StatusDot reads; renders
 *  the legacy `.status-badge` classes so the tone modifiers
 *  (connecting / disconnected / paused) come from the global sheet.
 *  The visible glyph is the Monitor icon (Claude's laptop) carrying
 *  the connection tone on its colour; a hidden `.indicator-dot` stays
 *  inside purely for the runtime bridge (see inline comment).
 *
 *  Exactly ONE instance may hold `id="statusBadge"`: the legacy ui.ts
 *  updaters (lib/runtime-bridge/ui.ts) guard on that id before pushing
 *  status into the store, and `setStatusDotHealth` looks up
 *  `.indicator-dot` inside it. In a split view both panes render a chip,
 *  so only the unbound (focused-following) one claims the id via
 *  `owningId`; the other renders the same chrome without it. */
export function StatusChip({ owningId = true }: { owningId?: boolean }) {
  const { text } = useTranslation();
  const statusBadge = useSessionStore((s) => s.statusBadge);
  const [open, setOpen] = useState(false);
  // Ref 挂上即进入受控模式：图标不再对自身 hover 自动播动画——
  // 环境 pill 的图标（monitor / folder）统一静态。
  const monitorIconRef = useRef<AnimatedNavIconHandle>(null);

  // Another top-bar-family dropdown opening closes this one, so only
  // one is ever open (same coordination event as the other chips).
  useEffect(() => {
    const close = () => setOpen(false);
    window.addEventListener("topbar-close-menus", close);
    return () => window.removeEventListener("topbar-close-menus", close);
  }, []);

  function onOpenChange(next: boolean) {
    if (next) {
      window.dispatchEvent(new Event("topbar-close-menus"));
      closeAllPopovers();
    }
    setOpen(next);
  }

  // Tone → the legacy `.status-badge` modifier (green default, yellow
  // connecting/paused, red disconnected) + the matching indicator-dot
  // colour class. `paused` wins over the raw tone, mirroring the type's
  // contract in lib/session-store/types.ts.
  const toneClass = statusBadge.paused
    ? " paused"
    : statusBadge.tone === "connecting"
      ? " connecting"
      : statusBadge.tone === "err"
        ? " disconnected"
        : statusBadge.tone === "warn"
          ? " paused"
          : "";
  const dotMod =
    statusBadge.tone === "ok"
      ? "--ok"
      : statusBadge.tone === "err"
        ? "--err"
        : "--warn";
  // Connection tone lives on the Monitor icon's colour (Claude's laptop
  // glyph): ok inherits the pill's ink; connecting / warn / paused go
  // yellow; disconnected goes red.
  const iconColor =
    statusBadge.tone === "err"
      ? "var(--accent-red)"
      : statusBadge.paused ||
          statusBadge.tone === "warn" ||
          statusBadge.tone === "connecting"
        ? "var(--accent-yellow)"
        : undefined;
  const label = statusBadge.label || text("Local", "本地");
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <HoverTip
        label={statusBadge.title || text("Conversation channel", "会话渠道")}
      >
        <PopoverTrigger asChild>
          <span
            {...(owningId ? { id: "statusBadge" } : {})}
            role="button"
            className={`status-badge${toneClass}`}
          >
            {/* Hidden dot kept ONLY for lib/runtime-bridge/ui.ts
                setStatusDotHealth(), which queries `#statusBadge
                .indicator-dot` and rewrites its className wholesale —
                the inline display:none survives that rewrite. The
                visible state is the Monitor icon above. */}
            <span
              className={`indicator-dot sm ${dotMod}`}
              style={{ display: "none" }}
              aria-hidden="true"
            />
            <MonitorIcon
              ref={monitorIconRef}
              size={14}
              style={{ color: iconColor }}
              aria-hidden="true"
            />
            <span className="badge-short">{label}</span>
          </span>
        </PopoverTrigger>
      </HoverTip>
      <PopoverContent
        side="top"
        align="start"
        sideOffset={6}
        className="w-auto border-0 bg-transparent p-0 shadow-none"
      >
        <ChannelMenu onClose={() => setOpen(false)} />
      </PopoverContent>
    </Popover>
  );
}
