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
  ClockIcon,
  HistoryIcon,
} from "../animated-icons";
import { useTranslation } from "@/lib/i18n";
import { refreshFunctionsList } from "@/lib/state/functions-actions";
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
      <span className={sidebarNavLabelClass}>{label}</span>
      {action}
    </Link>
  );
}

export function SidebarPrimaryNav() {
  const pathname = usePathname();
  const { t } = useTranslation();
  const [abilityHref, setAbilityHref] = useState("/programs");
  useEffect(() => {
    try {
      const kind = sessionStorage.getItem("op.ability.kind");
      if (kind === "mcp") setAbilityHref("/mcp");
      else if (kind === "plugins" || kind === "skills" || kind === "programs") setAbilityHref(`/${kind}`);
    } catch { /* ignore */ }
  }, [pathname]);
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
        || pathname.startsWith("/plugin/")
        || pathname.startsWith("/mcp")
        || pathname.startsWith("/programs"),
      label: t("nav.ability"),
      icon: CpuIcon,
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
      icon: ClockIcon,
    },
  ];

  const refreshAction = (
    <span
      className={
        sidebarNavActionClass +
        " inline-flex size-[22px] items-center justify-center rounded-[5px]" +
        " [transition:background_0.15s,color_0.15s,opacity_0.15s]" +
        " hover:bg-bg-hover hover:text-text-bright hover:!opacity-100" +
        " active:bg-bg-tertiary" +
        (refreshing || refreshDone ? " !opacity-100" : "") +
        (refreshDone ? " !text-[#4ade80]" : "")
      }
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        doRefresh();
      }}
      title={t("sidebar.refresh")}
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

  return (
    <div className="flex flex-col gap-px shrink-0 px-[8px] pt-px">
      {items.map((item) => (
        <SidebarNavLink
          key={item.id}
          {...item}
          action={item.id === "navAbility" ? refreshAction : undefined}
        />
      ))}
    </div>
  );
}
