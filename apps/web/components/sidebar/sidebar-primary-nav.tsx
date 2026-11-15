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
  InboxIcon,
} from "../animated-icons";
import { useTranslation } from "@/lib/i18n";
import { refreshFunctionsList } from "@/lib/abilities/functions-actions";
import { usePendingInbox } from "@/lib/agents/pending-inbox";
import { Badge } from "@/components/ui/badge";
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
}: {
  href: string;
  id: string;
  active: boolean;
  label: string;
  icon: NavIcon;
  className?: string;
  action?: ReactNode;
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
      onMouseEnter={() => iconRef.current?.startAnimation?.()}
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
  // Reports from always-on Agents still waiting for the user (/pending).
  const { items: pending } = usePendingInbox();
  const [refreshing, setRefreshing] = useState(false);
  const [refreshDone, setRefreshDone] = useState(false);
  const refreshSvgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    let cancelled = false;
    const refreshVisible = () => {
      if (!cancelled && document.visibilityState === "visible") {
        void refreshFunctionsList();
      }
    };
    const id = window.setInterval(refreshVisible, 30_000);
    document.addEventListener("visibilitychange", refreshVisible);
    return () => {
      cancelled = true;
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", refreshVisible);
    };
  }, []);

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
  }> = [
    {
      href: "/pending",
      id: "navPending",
      active: pathname.startsWith("/pending"),
      label: text("Pending", "待处理"),
      icon: InboxIcon,
    },
    {
      href: "/agents",
      id: "navAgents",
      active: pathname.startsWith("/agents"),
      label: t("nav.agents"),
      icon: BotIcon,
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
    },
    {
      href: "/applications",
      id: "navApplications",
      active: pathname.startsWith("/applications"),
      label: text("Applications", "应用"),
      icon: BlocksIcon,
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
    },
    {
      href: "/scheduler",
      id: "navScheduler",
      active: pathname.startsWith("/scheduler"),
      label: t("nav.scheduler"),
      icon: CalendarDaysIcon,
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

  const pendingCount = pending.length > 0 ? (
    <Badge className="ml-auto h-[18px] min-w-[18px] px-[6px] text-fs-2xs tabular-nums" aria-label={text(`${pending.length} waiting`, `${pending.length} 项待处理`)}>
      {pending.length > 99 ? "99+" : pending.length}
    </Badge>
  ) : undefined;

  return (
    <div className="flex flex-col gap-px shrink-0 px-[8px] pt-px">
      {items.map((item) => (
        <SidebarNavLink
          key={item.id}
          {...item}
          action={item.id === "navAbility" ? refreshAction : item.id === "navPending" ? pendingCount : undefined}
        />
      ))}
    </div>
  );
}
