"use client";

/**
 * Agent chip in the composer's right cluster: which saved Agent this
 * conversation uses, and the menu to pick another.
 *
 * A draft keeps its pick as the pending `agentInvocation` and binds it
 * with the first message; an existing conversation switches on the
 * server (`POST /api/sessions/{id}/agent`) and takes the Agent's model.
 * The Agent's own settings stay on the Agents page, reached from the
 * menu's last row.
 */
import { useEffect, useState } from "react";

import { AgentAvatar } from "@/components/agents/agent-avatar";
import type { Agent } from "@/components/agents/agent-types";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { buttonVariants } from "@/components/ui/button";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import { setDraftAgent } from "@/lib/agents/start-conversation";
import { switchSessionAgent } from "@/lib/agents/session-agent";
import { conversationAgentId } from "@/lib/agents/settle-agent-invocation";
import { useAgents } from "@/lib/agents/agents-query";
import { useTranslation } from "@/lib/i18n";
import { navigate } from "@/lib/nav/navigate";
import { closeAllPopovers } from "@/lib/runtime-bridge/ui";
import { useSessionStore } from "@/lib/session-store";
import { cn } from "@/lib/utils";

import { useBoundChat } from "../bound-chat";
import { MenuCheck } from "@/components/ui/menu/menu-check";
import {
  CHECK_SLOT_PAD,
  GROUP_LABEL,
  MENU_ITEM_STATES,
  MENU_NOTE,
  MENU_NOTE_ERROR,
  MENU_PANEL,
  MENU_SEPARATOR,
  itemCls,
} from "@/components/ui/menu/menu-styles";

export function AgentConfigBadge({ id }: { id: string }) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const { text } = useTranslation();
  const { sessionId, chatKey } = useBoundChat();
  const invocation = useSessionStore((state) => chatKey
    ? state.composerSettingsBySession[chatKey]?.agentInvocation : undefined);
  // The draft's pick, else the conversation's own Agent (its list row's, or
  // the one its first turn bound until the list names it).
  const ownAgent = useSessionStore((state) => conversationAgentId(state, sessionId, chatKey));
  const { data: agents, refetch } = useAgents();

  // One top-bar dropdown open at a time (same event as the model chip).
  useEffect(() => {
    const close = () => setOpen(false);
    window.addEventListener("topbar-close-menus", close);
    return () => window.removeEventListener("topbar-close-menus", close);
  }, []);

  const fallback = agents?.find((agent) => agent.default);
  const currentId = ownAgent ?? fallback?.id;
  const current = agents?.find((agent) => agent.id === currentId);
  // Blank until the list arrives, so the id never flashes before the name.
  const label = invocation?.label || current?.name || (agents ? currentId : "") || "";
  const alwaysOn = !!current?.always_on?.enabled;

  function onOpenChange(next: boolean) {
    if (next) {
      window.dispatchEvent(new Event("topbar-close-menus"));
      closeAllPopovers();
      setError("");
      // Agents saved elsewhere since the last look show up in the list.
      void refetch();
    }
    setOpen(next);
  }

  async function pick(agent: Agent) {
    if (agent.id === currentId && !invocation?.trial) {
      setOpen(false);
      return;
    }
    try {
      if (sessionId && !invocation) {
        await switchSessionAgent(sessionId, agent.id);
      } else {
        let key = chatKey;
        if (!key) {
          const [{ useCenterTabs }, { newSession }] = await Promise.all([
            import("@/lib/tabs/center-tabs-store"), import("@/lib/runtime-bridge/session/conversations"),
          ]);
          key = useCenterTabs.getState().openDraftSessionTab();
          newSession(key);
        }
        await setDraftAgent(key, agent.id);
      }
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const chatAgents = (agents ?? []).filter((agent) => !agent.always_on?.enabled);
  const liveAgents = (agents ?? []).filter((agent) => agent.always_on?.enabled);
  const row = (agent: Agent) => (
    <DropdownMenuItem
      key={agent.id}
      onSelect={(e) => {
        e.preventDefault();
        void pick(agent);
      }}
      className={cn(itemCls(false), MENU_ITEM_STATES)}
    >
      <AgentAvatar agent={agent} size={16} fit="mark" className="shrink-0" />
      <span className="min-w-0 flex-1 truncate">{agent.name || agent.id}</span>
      {agent.id === currentId ? <MenuCheck /> : <span className={CHECK_SLOT_PAD} />}
    </DropdownMenuItem>
  );

  const tip = (
    <TipBody
      title={text(`Agent: ${label}`, `Agent：${label}`)}
      detail={invocation?.trial
        ? text("Draft trial; memory is read-only or off.", "草稿试运行；记忆只读或关闭。")
        : alwaysOn
          ? text("Always-on: this conversation wakes on its own. Click to change Agent.", "常驻：这个会话会自己唤醒。点击更换 Agent。")
          : text("The configuration this conversation runs with. Click to change Agent.", "这个会话使用的配置。点击更换 Agent。")}
    />
  );

  return (
    <DropdownMenu open={open} onOpenChange={onOpenChange} modal={false}>
      <HoverTip label={tip}>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            id={id}
            className={cn(buttonVariants({ variant: "elevated", size: "sm" })) + " runtime-badge agent-badge"}
          >
            {/* The slot keeps its width until the list names the Agent. */}
            {currentId ? <AgentAvatar agent={current ?? { id: currentId }} size={16} fit="mark" className="shrink-0" /> : <span className="size-[16px] shrink-0" aria-hidden="true" />}
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
        {/* Grouped by the always-on switch, as the Agents page is. */}
        <div className={GROUP_LABEL}>{liveAgents.length ? text("Chat", "对话") : text("Agent", "Agent")}</div>
        {chatAgents.map(row)}
        {liveAgents.length ? (
          <>
            <DropdownMenuSeparator className={MENU_SEPARATOR} />
            <div className={GROUP_LABEL}>{text("Always-on", "常驻")}</div>
            {liveAgents.map(row)}
          </>
        ) : null}
        {error ? <div className={cn(MENU_NOTE, MENU_NOTE_ERROR)}>{error}</div> : null}
        <DropdownMenuSeparator className={MENU_SEPARATOR} />
        <DropdownMenuItem
          onSelect={() => navigate("/agents")}
          className={cn(itemCls(false), MENU_ITEM_STATES)}
        >
          <span className="min-w-0 flex-1 truncate">{text("Manage Agents", "管理 Agents")}</span>
          <span className={CHECK_SLOT_PAD} />
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
