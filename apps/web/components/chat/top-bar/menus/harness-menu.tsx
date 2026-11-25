"use client";

/**
 * Harness chip in the composer's right cluster, before the model chip: the
 * engine this conversation's turns run in. OpenProgram is its own engine
 * (the default); an outside agent (Codex) runs the model, tools and
 * approvals itself. A harness not installed or not signed in on the
 * conversation's machine is listed disabled with the reason.
 *
 * The model, effort, Agent and permission chips follow the chosen harness:
 * `useHarness()` is what they read. With Codex the Agent chip lists Codex's
 * own choices (`HarnessAgentBadge`): its default, the profiles in its
 * config.toml, and personalities for a model that takes one.
 */
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { buttonVariants } from "@/components/ui/button";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import { SolarIcon } from "@/components/solar-icons";
import { MenuRow } from "@/components/ui/menu/menu-parts";
import {
  GROUP_LABEL,
  MENU_ITEM_STATES,
  MENU_PANEL,
  MENU_SEPARATOR,
  itemCls,
} from "@/components/ui/menu/menu-styles";
import { useSessionAgentSettings } from "@/components/chat/composer/state/use-session-agent-settings";
import { useTranslation } from "@/lib/i18n";
import { closeAllPopovers } from "@/lib/runtime-bridge/ui";
import { showToast } from "@/lib/format-utils/toast";
import { chooseHarnessAgent, ensureChatSession, switchSessionHarness } from "@/lib/agents/session-harness";
import {
  OPENPROGRAM_HARNESS,
  agentChoices,
  agentName,
  defaultModel,
  harnessOf,
  unavailable,
  type Harness,
} from "@/lib/models/harnesses";
import { harnessQueryKey, listHarnesses } from "@/lib/models/harness-listing";
import { cn } from "@/lib/utils";

import { useBoundChat } from "../bound-chat";
import { AgentMark } from "../chips/model-mark";

/** OpenProgram's own engine's glyph. */
const OPENPROGRAM_GLYPH = "programming";

export interface HarnessState {
  /** The branch's harness (`"codex"`), or null for OpenProgram's own engine. */
  harness: string | null;
  /** The harness's agent choice, or null for its default. */
  agent: string | null;
  /** The listing of the conversation's machine (once read). */
  harnesses: Harness[] | undefined;
  /** The chosen harness's entry in that listing. */
  entry: Harness | undefined;
  provider?: string;
  model?: string;
}

/** The pane's harness. `read` asks the conversation's machine for its
 *  harnesses (a menu opening, or a harness in use whose models are named). */
export function useHarness(read = false): HarnessState {
  const { sessionId } = useBoundChat();
  const chat = useSessionAgentSettings().chat;
  const harness = chat?.harness ?? harnessOf(chat?.provider);
  const { data } = useQuery({
    queryKey: harnessQueryKey(sessionId),
    queryFn: () => listHarnesses(sessionId),
    enabled: read || !!harness,
    staleTime: 5 * 60_000,
  });
  return {
    harness: harness ?? null,
    agent: chat?.harness_agent ?? null,
    harnesses: data,
    entry: harness ? data?.find((h) => h.id === harness) : undefined,
    provider: chat?.provider,
    model: chat?.model,
  };
}

/** One top-bar dropdown open at a time. */
function useExclusiveOpen(): [boolean, (next: boolean) => void] {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const close = () => setOpen(false);
    window.addEventListener("topbar-close-menus", close);
    return () => window.removeEventListener("topbar-close-menus", close);
  }, []);
  return [open, (next: boolean) => {
    if (next) {
      window.dispatchEvent(new Event("topbar-close-menus"));
      closeAllPopovers();
    }
    setOpen(next);
  }];
}

function HarnessMark({ harness }: { harness: string | null }) {
  return harness
    ? <AgentMark harness={harness} />
    : <SolarIcon name={OPENPROGRAM_GLYPH} size={16} fit="fill" className="shrink-0" aria-hidden="true" />;
}

export function HarnessBadge({ id }: { id: string }) {
  const { text } = useTranslation();
  const { sessionId, chatKey } = useBoundChat();
  const [open, setOpen] = useExclusiveOpen();
  const state = useHarness(open);
  const label = state.harness ? state.entry?.label ?? harnessLabel(state.harness) : "OpenProgram";

  async function pick(target: string, entry?: Harness) {
    setOpen(false);
    if ((state.harness ?? OPENPROGRAM_HARNESS) === target) return;
    try {
      const sid = await ensureChatSession(sessionId, chatKey);
      if (!sid) return;
      await switchSessionHarness(sid, target, defaultModel(entry)?.id);
    } catch (e) {
      showToast(text("Could not switch harness: ", "切换 Harness 失败：") + String(e), { tone: "error" });
    }
  }

  const reason = (h: Harness) => {
    const why = unavailable(h);
    return why === "not_installed" ? text("Not installed", "未安装")
      : why === "not_signed_in" ? text("Not signed in", "未登录")
        : h.version;
  };

  return (
    <DropdownMenu open={open} onOpenChange={setOpen} modal={false}>
      <HoverTip
        label={<TipBody
          title={text(`Harness: ${label}`, `Harness：${label}`)}
          detail={text("The engine that runs this conversation's turns. Click to change.", "运行这个会话的引擎。点击更换。")}
        />}
      >
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            id={id}
            className={cn(buttonVariants({ variant: "elevated", size: "sm" })) + " runtime-badge agent-badge"}
          >
            <HarnessMark harness={state.harness} />
            <span className="badge-details">{label}</span>
          </button>
        </DropdownMenuTrigger>
      </HoverTip>
      <DropdownMenuContent
        side="top"
        align="end"
        sideOffset={9}
        onCloseAutoFocus={(e) => e.preventDefault()}
        className={cn(MENU_PANEL, "text-text-primary")}
      >
        <div className={GROUP_LABEL}>{text("Harness", "Harness")}</div>
        <DropdownMenuItem onSelect={() => void pick(OPENPROGRAM_HARNESS)} className={cn(itemCls(false), MENU_ITEM_STATES)}>
          <MenuRow glyph={OPENPROGRAM_GLYPH} label="OpenProgram" checked={!state.harness} />
        </DropdownMenuItem>
        {state.harnesses?.length ? <DropdownMenuSeparator className={MENU_SEPARATOR} /> : null}
        {(state.harnesses ?? []).map((h) => (
          <DropdownMenuItem
            key={h.id}
            disabled={!!unavailable(h)}
            onSelect={() => void pick(h.id, h)}
            className={cn(itemCls(false), MENU_ITEM_STATES)}
          >
            <MenuRow icon={<AgentMark harness={h.id} />} label={h.label} trail={reason(h) || undefined} checked={state.harness === h.id} />
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function harnessLabel(harness: string): string {
  return agentName(harness) ?? harness;
}

/** The Agent chip while a harness runs the conversation: the harness's own
 *  agent-like choices, stored on the branch like its other run settings. */
export function HarnessAgentBadge({ id, state }: { id: string; state: HarnessState }) {
  const { text } = useTranslation();
  const [open, setOpen] = useExclusiveOpen();
  const label = harnessAgentLabel(state, text);
  const runner = agentName(state.harness) ?? state.entry?.label ?? state.harness ?? "";

  return (
    <DropdownMenu open={open} onOpenChange={setOpen} modal={false}>
      <HoverTip
        label={<TipBody
          title={text(`Agent: ${label}`, `Agent：${label}`)}
          detail={text(`What ${runner} runs this conversation with. Click to change.`, `${runner} 运行这个会话所用的设置。点击更换。`)}
        />}
      >
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            id={id}
            className={cn(buttonVariants({ variant: "elevated", size: "sm" })) + " runtime-badge agent-badge"}
          >
            <AgentMark harness={state.harness} />
            {label ? <span className="badge-details">{label}</span> : null}
          </button>
        </DropdownMenuTrigger>
      </HoverTip>
      <DropdownMenuContent
        side="top"
        align="end"
        sideOffset={9}
        onCloseAutoFocus={(e) => e.preventDefault()}
        className={cn(MENU_PANEL, "text-text-primary")}
      >
        <HarnessAgentRows state={state} onPicked={() => setOpen(false)} />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** The harness agent a conversation runs with, as its chip and the phone's row name it. */
export function harnessAgentLabel(state: HarnessState, text: (en: string, zh: string) => string): string {
  const current = state.entry?.agents.find((a) => a.id === (state.agent ?? "default"));
  return current?.label ?? (state.entry ? text("Default", "默认") : "");
}

/** A harness's agent choices, grouped by kind: the Agent chip's menu and the phone's Agent row. */
export function HarnessAgentRows({ state, onPicked }: { state: HarnessState; onPicked: () => void }) {
  const { text } = useTranslation();
  const { sessionId, chatKey } = useBoundChat();
  const choices = agentChoices(state.entry, state.model);
  const currentId = state.agent ?? "default";

  async function pick(agent: string) {
    onPicked();
    if (agent === currentId) return;
    try {
      const sid = await ensureChatSession(sessionId, chatKey);
      if (sid) await chooseHarnessAgent(sid, agent);
    } catch (e) {
      showToast(text("Could not change the agent: ", "更换 Agent 失败：") + String(e), { tone: "error" });
    }
  }

  const named = choices.some((a) => a.kind === "agent");
  const groups: { title: string | null; kind: string }[] = [
    // Beside Claude Code's named agents, its default needs no heading of its own.
    { title: named ? null : text("Agent", "Agent"), kind: "default" },
    { title: text("Profiles", "Profiles"), kind: "profile" },
    { title: text("Personality", "性格"), kind: "personality" },
    { title: text("Agents", "Agents"), kind: "agent" },
  ];
  const shown = groups.filter((g) => choices.some((a) => a.kind === g.kind));
  return (
    <>
      {shown.map((group, index) => (
        <div key={group.kind}>
          {index ? <DropdownMenuSeparator className={MENU_SEPARATOR} /> : null}
          {group.title ? <div className={GROUP_LABEL}>{group.title}</div> : null}
          {choices.filter((a) => a.kind === group.kind).map((a) => (
            <DropdownMenuItem key={a.id} onSelect={() => void pick(a.id)} className={cn(itemCls(false), MENU_ITEM_STATES)}>
              <MenuRow label={a.label} checked={a.id === currentId} />
            </DropdownMenuItem>
          ))}
        </div>
      ))}
    </>
  );
}
