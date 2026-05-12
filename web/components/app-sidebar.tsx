"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState, useMemo } from "react";
import {
  PlusIcon,
  Squares2X2Icon,
  QueueListIcon,
  Cog6ToothIcon,
  InformationCircleIcon,
  XMarkIcon,
} from "@heroicons/react/20/solid";
import { cn } from "@/lib/utils";
import { useSessionStore } from "@/lib/session-store";
import { useWS } from "@/lib/ws";

export function AppSidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [menuOpen, setMenuOpen] = useState(false);

  const conversations = useSessionStore((s) => s.conversations);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const setCurrentConv = useSessionStore((s) => s.setCurrentConv);
  const clearLocal = useSessionStore((s) => s.clearConversations);
  const { send } = useWS();

  const convList = useMemo(() => {
    const arr = Object.values(conversations);
    arr.sort((a, b) => (b.created_at ?? 0) - (a.created_at ?? 0));
    return arr;
  }, [conversations]);

  function channelLabelFor(c: { title?: string; channel?: string | null; account_id?: string | null; preview?: string | null }): string {
    const t = (c.title || "").trim();
    const placeholder =
      !t ||
      t === "New conversation" ||
      t === "Untitled" ||
      /^(wechat|discord|telegram|slack)\s*[:：]\s*\S{8,}/i.test(t);
    let prefix = "";
    if (c.channel) {
      const brand =
        ({ wechat: "WeChat", discord: "Discord", telegram: "Telegram", slack: "Slack" } as
          Record<string, string>)[String(c.channel).toLowerCase()] || c.channel;
      prefix = c.account_id ? `${brand} (${c.account_id})` : brand;
    }
    let realTitle = placeholder ? "" : t;
    if (!realTitle && c.preview) {
      const pv = String(c.preview).trim();
      realTitle = pv.length > 30 ? pv.slice(0, 30) + "…" : pv;
    }
    if (prefix && realTitle) return `${prefix}: ${realTitle}`;
    if (prefix) return prefix;
    if (realTitle) return realTitle;
    return "Untitled";
  }

  function newChat() {
    setCurrentConv(null);
    router.push("/chat");
  }

  function switchTo(id: string) {
    setCurrentConv(id);
    send({ action: "load_session", session_id: id });
    router.push(`/s/${id}`);
  }

  function deleteConv(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    if (!confirm("Delete this conversation?")) return;
    send({ action: "delete_session", session_id: id });
    if (currentSessionId === id) {
      setCurrentConv(null);
      router.push("/chat");
    }
  }

  function clearAll() {
    if (!convList.length) return;
    if (!confirm(`Delete all ${convList.length} conversations?`)) return;
    send({ action: "clear_sessions" });
    clearLocal();
    router.push("/chat");
  }

  const chatActive = pathname === "/chat" || pathname === "/" || pathname.startsWith("/s/");
  const programsActive = pathname.startsWith("/programs");
  const memoryActive = pathname.startsWith("/memory");

  return (
    <aside
      className="flex h-screen flex-col border-r"
      style={{
        width: "var(--sidebar-width)",
        background: "var(--bg-secondary)",
        borderColor: "var(--border-color)",
      }}
    >
      <div className="flex h-12 items-center px-3">
        <div className="flex items-center gap-2 px-2 font-mono text-lg font-bold">
          <span style={{ color: "var(--accent-blue)" }}>{"{"}</span>
          <span style={{ color: "var(--accent-red)" }}>L</span>
          <span style={{ color: "var(--accent-blue)" }}>{"}"}</span>
        </div>
      </div>

      <div className="px-3 pb-2">
        <NavItem
          active={chatActive}
          onClick={newChat}
          icon={<PlusIcon className="h-[18px] w-[18px]" />}
          label="New chat"
        />
        <NavItem
          active={programsActive}
          onClick={() => router.push("/programs")}
          icon={<Squares2X2Icon className="h-[18px] w-[18px]" />}
          label="Programs"
        />
        <NavItem
          active={memoryActive}
          onClick={() => router.push("/memory")}
          icon={
            <span className="memory-icon-wrap">
              <QueueListIcon className="h-[18px] w-[18px]" />
            </span>
          }
          label="Memory"
        />
      </div>

      <div className="flex-1 overflow-y-auto">
        <Section title="Recents" onClear={convList.length ? clearAll : undefined}>
          {convList.length === 0 ? (
            <div
              className="px-2 py-1 text-[12px] italic opacity-60"
              style={{ color: "var(--text-muted)" }}
            >
              (empty)
            </div>
          ) : (
            <ul className="space-y-0.5">
              {convList.map((c) => {
                const active = c.id === currentSessionId;
                return (
                  <li key={c.id}>
                    <div
                      onClick={() => switchTo(c.id)}
                      className={cn(
                        "group flex h-8 cursor-pointer items-center gap-2 rounded-md px-2 text-[12px] transition-colors"
                      )}
                      style={{
                        background: active ? "var(--bg-tertiary)" : "transparent",
                        color: active ? "var(--text-bright)" : "var(--text-primary)",
                      }}
                      onMouseEnter={(e) => {
                        if (!active)
                          e.currentTarget.style.background = "var(--bg-hover)";
                      }}
                      onMouseLeave={(e) => {
                        if (!active) e.currentTarget.style.background = "transparent";
                      }}
                    >
                      <span className="flex-1 truncate">{channelLabelFor(c)}</span>
                      <button
                        onClick={(e) => deleteConv(c.id, e)}
                        className="opacity-0 transition-opacity group-hover:opacity-60 hover:opacity-100"
                        title="Delete"
                      >
                        <XMarkIcon className="h-3 w-3" />
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </Section>
      </div>

      <div className="relative border-t" style={{ borderColor: "var(--border-color)" }}>
        <button
          onClick={() => setMenuOpen((v) => !v)}
          className="flex w-full items-center gap-2 px-3 py-3 text-left transition-colors"
          style={{ background: "transparent" }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
          onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
        >
          <div
            className="flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold"
            style={{ background: "var(--accent-blue)", color: "#fff" }}
          >
            A
          </div>
          <div className="flex-1 overflow-hidden">
            <div className="truncate text-[13px]" style={{ color: "var(--text-bright)" }}>
              Agentic
            </div>
            <div className="truncate text-[11px]" style={{ color: "var(--text-muted)" }}>
              Local instance
            </div>
          </div>
        </button>

        {menuOpen && (
          <div
            className="absolute bottom-full left-3 right-3 mb-1 overflow-hidden rounded-lg border py-1 shadow-lg"
            style={{
              background: "var(--bg-tertiary)",
              borderColor: "var(--border-color)",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <Link
              href="/settings/providers"
              onClick={() => setMenuOpen(false)}
              className="flex items-center gap-2 px-3 py-2 text-[13px] transition-colors"
              style={{ color: "var(--text-primary)" }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
            >
              <Cog6ToothIcon className="h-4 w-4" />
              Settings
            </Link>
            <div className="my-1 border-t" style={{ borderColor: "var(--border-color)" }} />
            <a
              href="https://github.com/Fzkuji/OpenProgram"
              target="_blank"
              rel="noopener"
              onClick={() => setMenuOpen(false)}
              className="flex items-center gap-2 px-3 py-2 text-[13px] transition-colors"
              style={{ color: "var(--text-primary)" }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
            >
              <InformationCircleIcon className="h-4 w-4" />
              About
            </a>
          </div>
        )}
      </div>
    </aside>
  );
}

function NavItem({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <div
      onClick={onClick}
      className="group flex h-9 cursor-pointer items-center gap-2 rounded-md px-2 text-[13px] transition-colors"
      style={{
        background: active ? "var(--bg-tertiary)" : "transparent",
        color: active ? "var(--text-bright)" : "var(--text-primary)",
      }}
      onMouseEnter={(e) => {
        if (!active) e.currentTarget.style.background = "var(--bg-hover)";
      }}
      onMouseLeave={(e) => {
        if (!active) e.currentTarget.style.background = "transparent";
      }}
    >
      <span style={{ color: active ? "var(--text-bright)" : (label === "New chat" ? "var(--text-primary)" : "var(--text-bright)") }}>
        {icon}
      </span>
      <span className="flex-1 truncate">{label}</span>
    </div>
  );
}

function Section({
  title,
  children,
  onClear,
}: {
  title: string;
  children: React.ReactNode;
  onClear?: () => void;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div className="px-3 py-2">
      <div
        className="flex items-center justify-between px-2 py-1 text-[11px] uppercase tracking-wide"
        style={{ color: "var(--text-muted)" }}
      >
        <button onClick={() => setOpen((v) => !v)} className="flex-1 text-left">
          {title}
        </button>
        {onClear && (
          <button
            onClick={onClear}
            className="ml-2 text-[10px] opacity-60 hover:opacity-100"
          >
            Clear
          </button>
        )}
      </div>
      {open && <div className="mt-1 px-1">{children}</div>}
    </div>
  );
}
