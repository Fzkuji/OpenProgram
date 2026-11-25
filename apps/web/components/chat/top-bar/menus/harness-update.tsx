"use client";

/**
 * The model menu's rows for what a newer Codex would bring, after T3 Code:
 * the models Codex offers that the installed Codex does not list yet, shown
 * dimmed, and an Update row that runs the installer Codex came from (npm,
 * Homebrew, `codex update`…) on the conversation's machine. When the
 * installer is not known the rows stay, and their tip names the version to
 * install. After an update the listing is read again, so the new models
 * become ordinary rows.
 */
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { HoverTip } from "@/components/ui/tooltip";
import { MenuRow } from "@/components/ui/menu/menu-parts";
import { MENU_ITEM_STATES, itemCls } from "@/components/ui/menu/menu-styles";
import { useTranslation } from "@/lib/i18n";
import { showToast } from "@/lib/format-utils/toast";
import type { Harness } from "@/lib/models/harnesses";
import { harnessQueryKey, updateHarness } from "@/lib/models/harness-listing";
import { cn } from "@/lib/utils";

import { useBoundChat } from "../bound-chat";
import { AgentMark } from "../chips/model-mark";

export function HarnessUpdateRows({ harness }: { harness: Harness }) {
  const { text } = useTranslation();
  const { sessionId } = useBoundChat();
  const queryClient = useQueryClient();
  const [running, setRunning] = useState(false);
  const fresh = harness.newModels ?? [];
  if (!harness.latest || (!fresh.length && !harness.updatable)) return null;
  const versions = `${harness.version} → ${harness.latest}`;

  async function update() {
    if (running) return;
    setRunning(true);
    try {
      const result = await updateHarness(sessionId, harness.id);
      if (result.harness) {
        const updated = result.harness;
        queryClient.setQueryData<Harness[]>(harnessQueryKey(sessionId),
          (list) => list?.map((h) => (h.id === updated.id ? updated : h)));
      }
      if (!result.ok) {
        showToast(text(`Could not update ${harness.label}`, `${harness.label} 更新失败`)
          + (result.output ? `: ${result.output.split("\n").slice(-1)[0]}` : ""), { tone: "error" });
      }
    } catch (e) {
      showToast(text(`Could not update ${harness.label}: `, `${harness.label} 更新失败：`) + String(e), { tone: "error" });
    } finally {
      setRunning(false);
    }
  }

  const needs = text(`Needs ${harness.label} ${harness.latest}`, `需要 ${harness.label} ${harness.latest}`);
  return (
    <>
      {fresh.map((m) => (
        <HoverTip key={m.id} label={needs}>
          <DropdownMenuItem
            onSelect={(e) => { e.preventDefault(); if (harness.updatable) void update(); }}
            className={cn(itemCls(false), MENU_ITEM_STATES, "text-text-muted")}
          >
            <MenuRow icon={<AgentMark harness={harness.id} className="opacity-60" />} label={m.label} trail={harness.latest} checked={false} />
          </DropdownMenuItem>
        </HoverTip>
      ))}
      {harness.updatable ? (
        <DropdownMenuItem
          onSelect={(e) => { e.preventDefault(); void update(); }}
          className={cn(itemCls(false), MENU_ITEM_STATES)}
        >
          <MenuRow
            glyph="refresh"
            iconClassName={running ? "animate-spin" : undefined}
            label={text(`Update ${harness.label}`, `更新 ${harness.label}`)}
            trail={versions}
          />
        </DropdownMenuItem>
      ) : null}
    </>
  );
}
