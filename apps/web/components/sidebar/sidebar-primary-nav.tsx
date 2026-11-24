"use client";

import { useEffect, useRef, useState } from "react";
import type {
  ForwardRefExoticComponent,
  ReactNode,
  RefAttributes,
} from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { RefreshCw } from "lucide-react";
import {
  type AnimatedNavIconHandle,
  type AnimatedNavIconProps,
  BotIcon,
  CpuIcon,
  BlocksIcon,
  CalendarDaysIcon,
  HistoryIcon,
} from "../animated-icons";
import { useTranslation } from "@/lib/i18n";
import { refreshFunctionsList } from "@/lib/abilities/functions-actions";
import { useChanged } from "@/lib/net/ws/changes";
import { usePendingInbox } from "@/lib/agents/pending-inbox";
import { prefetchPage, prefetchPagesWhenIdle, type PrefetchPage } from "@/lib/abilities/page-prefetch";
import {
  sidebarNavActionClass,
  sidebarNavIconClass,
  sidebarNavItemActiveClass,
  sidebarNavItemClass,
  sidebarNavLabelClass,
} from "./nav-classes";

type NavIcon = ForwardRefExoticComponent<
  AnimatedNavIconProps & RefAttributes<AnimatedNavIconHandle>
>;

function SidebarNavLink({
  href,
  id,
  active,
  label,
  icon: Icon,
  className = "",
  action,
  prefetch,
}: {
  href: string;
  id: string;
  active: boolean;
  label: string;
  icon: NavIcon;
  className?: string;
  action?: ReactNode;
  /** The page whose first reads start when the pointer or focus arrives. */
  prefetch?: PrefetchPage;
}) {
  const iconRef = useRef<AnimatedNavIconHandle>(null);
  return (
    <Link
      href={href}
      className={
        sidebarNavItemClass +
        " rail-row" +
        className +
        (active ? " " + sidebarNavItemActiveClass : "")
      }
      id={id}
      onMouseEnter={() => {
        iconRef.current?.startAnimation?.();
        if (prefetch) void prefetchPage(prefetch);
      }}
      onFocus={() => { if (prefetch) void prefetchPage(prefetch); }}
      onMouseLeave={() => iconRef.current?.stopAnimation?.()}
    >
      <span className={sidebarNavIconClass}>
        <Icon ref={iconRef} size={20} aria-hidden="true" />
      </span>
      {/* What follows the icon is laid out at the open sidebar's width in
          both states and fades with it (styles/base/layout.css .rail-row):
          a row with an action keeps the label and the action together. */}
      {action ? (
        <span className="sidebar-nav-text flex min-w-0 items-center gap-[12px]">
          <span className={sidebarNavLabelClass}>{label}</span>
          {action}
        </span>
      ) : (
        <span className={sidebarNavLabelClass}>{label}</span>
      )}
    </Link>
  );
}

export function SidebarPrimaryNav() {
  const pathname = usePathname();
  const { t, text } = useTranslation();
  const [abilityHref, setAbilityHref] = useState("/programs");
  useEffect(() => {
    try {
      const kind = sessionStorage.getItem("op.ability.kind");
      if (kind === "mcp") setAbilityHref("/mcp");
      else if (kind === "plugins" || kind === "skills" || kind === "programs") setAbilityHref(`/${kind}`);
    } catch { /* ignore */ }
  }, [pathname]);
  // What always-on Agents and scheduled tasks left for the user (the Tasks page's Inbox).
  const { items: inbox } = usePendingInbox();
  const [refreshing, setRefreshing] = useState(false);
  const [refreshDone, setRefreshDone] = useState(false);
  const refreshSvgRef = useRef<SVGSVGElement>(null);

  // The Worker's `programs:changed` frame refreshes the function list
  // (lib/net/ws/use-ws.ts); `useChanged` reads it once more after the socket
  // comes back, since a frame sent while it was down is lost.
  useChanged("programs", () => refreshFunctionsList());

  // Every page's first reads and code, once, when the app is idle after start.
  useEffect(() => prefetchPagesWhenIdle(), []);

  function doRefresh() {
    if (refreshing) return;
    setRefreshing(true);
    void refreshFunctionsList();
    const svg = refreshSvgRef.current;
    if (!svg) {
      setTimeout(() => {
        setRefreshing(false);
        setRefreshDone(true);
        setTimeout(() => setRefreshDone(false), 800);
      }, 600);
      return;
    }
    const finish = () => {
      svg.removeEventListener("animationend", finish);
      setRefreshing(false);
      setRefreshDone(true);
      setTimeout(() => setRefreshDone(false), 800);
    };
    svg.addEventListener("animationend", finish);
    setTimeout(() => setRefreshing(false), 1200);
  }

  const items: Array<{
    href: string;
    id: string;
    active: boolean;
    label: string;
    icon: NavIcon;
    className?: string;
    prefetch?: PrefetchPage;
  }> = [
    {
      href: "/tasks",
      id: "navTasks",
      active: pathname.startsWith("/tasks"),
      label: t("nav.tasks"),
      icon: CalendarDaysIcon,
      prefetch: "tasks",
    },
    {
      href: "/agents",
      id: "navAgents",
      active: pathname.startsWith("/agents"),
      label: t("nav.agents"),
      icon: BotIcon,
      prefetch: "agents",
    },
    {
      href: abilityHref,
      id: "navAbility",
      active:
        pathname.startsWith("/skills")
        || pathname.startsWith("/plugins")
        || pathname.startsWith("/mcp")
        || pathname.startsWith("/programs"),
      label: t("nav.ability"),
      icon: CpuIcon,
      prefetch: abilityHref.slice(1) as PrefetchPage,
    },
    {
      href: "/applications",
      id: "navApplications",
      active: pathname.startsWith("/applications"),
      label: text("Applications", "应用"),
      icon: BlocksIcon,
      prefetch: "applications",
    },
    {
      href: "/chats",
      id: "navHistory",
      active:
        pathname.startsWith("/chats")
        || pathname.startsWith("/projects")
        || pathname.startsWith("/memory")
        || pathname.startsWith("/history"),
      label: t("nav.history"),
      icon: HistoryIcon,
      prefetch: "history",
    },
  ];

  const refreshAction = (
    <span
      className={
        sidebarNavActionClass +
        " inline-flex size-(--ui-button-h-xs) items-center justify-center rounded-xs" +
        " [transition:background_0.15s,color_0.15s,opacity_0.15s]" +
        " hover:bg-bg-hover hover:text-text-bright hover:!opacity-100" +
        " active:bg-bg-tertiary" +
        (refreshing || refreshDone ? " !opacity-100" : "") +
        (refreshDone ? " !text-[color:var(--status-refresh-done)]" : "")
      }
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        doRefresh();
      }}
      data-tip={t("sidebar.refresh")}
      aria-label={t("sidebar.refresh")}
    >
      {refreshDone ? (
        <span aria-hidden="true">&#10003;</span>
      ) : (
        <RefreshCw
          ref={refreshSvgRef}
          size={16}
          strokeWidth={2}
          className={refreshing ? "animate-spin-refresh" : ""}
        />
      )}
    </span>
  );

  // A quiet number: how many Inbox items need the user; nothing at zero.
  const inboxCount = inbox.length > 0 ? (
    <span className="ml-auto text-fs-xs text-text-muted tabular-nums" aria-label={text(`${inbox.length} need you`, `${inbox.length} 项等你处理`)}>
      {inbox.length > 99 ? "99+" : inbox.length}
    </span>
  ) : undefined;

  return (
    <div className="flex flex-col gap-px shrink-0 px-[8px] pt-px">
      {items.map((item) => (
        <SidebarNavLink
          key={item.id}
          {...item}
          action={item.id === "navAbility" ? refreshAction : item.id === "navTasks" ? inboxCount : undefined}
        />
      ))}
    </div>
  );
}
