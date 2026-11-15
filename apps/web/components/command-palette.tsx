"use client";

// Cmd/Ctrl+K: jump to a session or a page, or start a new chat, without
// reaching for the sidebar. Sessions are matched on their title, preview and
// project, the same fields the History page searches.

import { useEffect, useMemo, useState, type ComponentType } from "react";
import { defaultFilter } from "cmdk";
import {
  Blocks,
  Bot,
  Brain,
  CalendarDays,
  Cpu,
  FolderKanban,
  History,
  MessageSquare,
  MessageSquarePlus,
  Settings,
  type LucideProps,
} from "lucide-react";
import {
  CommandDialog,
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "@/components/ui/command";
import { activityTs, displayTitle } from "@/components/sidebar/sessions/helpers";
import { ChannelLogo } from "@/components/channels/channel-logo";
import { channelBrand } from "@/lib/channels/channel-brands";
import { desktopBridge } from "@/lib/desktop/desktop-bridge";
import { useTranslation } from "@/lib/i18n";
import { navigate } from "@/lib/nav/navigate";
import { useSessionStore } from "@/lib/session-store";
import { formatRelativeTime } from "@/lib/format-utils/format";
import { openNewChat, openSession } from "@/lib/tabs/navigation/open-session";
import { isPaletteShortcut, paletteSessions } from "@/lib/command-palette";

type Icon = ComponentType<LucideProps>;

// Items carry their searchable words in `keywords`; the item value is an id
// and must not take part in matching.
const filterByKeywords = (_value: string, search: string, keywords?: string[]) =>
  defaultFilter((keywords ?? []).join(" "), search);

export function CommandPalette() {
  const { t, text, locale } = useTranslation();
  const [open, setOpen] = useState(false);
  const conversations = useSessionStore((s) => s.conversations);
  const runningTasks = useSessionStore((s) => s.runningTasks);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!isPaletteShortcut(event)) return;
      event.preventDefault();
      setOpen((value) => !value);
    };
    // Capture, so a focused editor cannot swallow the shortcut first.
    window.addEventListener("keydown", onKeyDown, true);
    // A focused native web tab never delivers the key here; the desktop shell
    // forwards it instead.
    const offWebTab = desktopBridge()?.webTab.onCommand?.((command) => {
      if (command.command === "palette") setOpen((value) => !value);
    });
    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      offWebTab?.();
    };
  }, []);

  const sessions = useMemo(
    () => (open ? paletteSessions(Object.values(conversations)) : []),
    [open, conversations],
  );

  const pages: Array<{ id: string; label: string; path: string; icon: Icon }> = [
    { id: "agents", label: t("nav.agents"), path: "/agents", icon: Bot },
    { id: "skills", label: t("nav.skills"), path: "/skills", icon: Cpu },
    { id: "applications", label: text("Applications", "应用"), path: "/applications", icon: Blocks },
    { id: "projects", label: t("nav.projects"), path: "/projects", icon: FolderKanban },
    { id: "history", label: t("nav.history"), path: "/chats", icon: History },
    { id: "memory", label: t("nav.memory"), path: "/memory", icon: Brain },
    { id: "scheduler", label: t("nav.scheduler"), path: "/scheduler", icon: CalendarDays },
    { id: "settings", label: t("settings.title"), path: "/settings/general", icon: Settings },
  ];

  const run = (action: () => void) => {
    setOpen(false);
    action();
  };

  return (
    <CommandDialog
      open={open}
      onOpenChange={setOpen}
      title={text("Command palette", "命令面板")}
      description={text("Search sessions and pages", "搜索会话和页面")}
    >
      <Command filter={filterByKeywords} loop>
        <CommandInput placeholder={text("Search sessions and pages…", "搜索会话和页面…")} />
        <CommandList>
          <CommandEmpty>{text("No matches", "没有匹配项")}</CommandEmpty>
          <CommandGroup heading={text("Actions", "操作")}>
            <CommandItem
              value="action:new-chat"
              keywords={[t("nav.new_chat"), "new chat", "新会话"]}
              onSelect={() => run(() => { openNewChat(); })}
            >
              <MessageSquarePlus />
              <span>{t("nav.new_chat")}</span>
            </CommandItem>
            {pages.map((page) => (
              <CommandItem
                key={page.id}
                value={`page:${page.id}`}
                keywords={[page.label, page.id]}
                onSelect={() => run(() => navigate(page.path))}
              >
                <page.icon />
                <span>{page.label}</span>
              </CommandItem>
            ))}
          </CommandGroup>
          {sessions.length > 0 && (
            <CommandGroup heading={text("Sessions", "会话")}>
              {sessions.map((conv) => {
                const title = displayTitle(conv) || text("Untitled", "未命名");
                return (
                  <CommandItem
                    key={conv.id}
                    value={`session:${conv.id}`}
                    keywords={[title, conv.preview || "", conv.project || "", channelBrand(conv.channel)]}
                    onSelect={() => run(() => openSession(conv.id))}
                  >
                    {conv.channel ? <ChannelLogo channel={conv.channel} size={16} /> : <MessageSquare />}
                    <span className="min-w-0 truncate">
                      {conv.channel ? <span className="sr-only">{channelBrand(conv.channel)}: </span> : null}
                      {title}
                    </span>
                    {runningTasks[conv.id] ? (
                      <CommandShortcut>{text("Running", "运行中")}</CommandShortcut>
                    ) : (
                      <CommandShortcut>{formatRelativeTime(activityTs(conv), locale)}</CommandShortcut>
                    )}
                  </CommandItem>
                );
              })}
            </CommandGroup>
          )}
        </CommandList>
      </Command>
    </CommandDialog>
  );
}
