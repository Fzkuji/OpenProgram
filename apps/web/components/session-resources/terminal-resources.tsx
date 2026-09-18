"use client";

import { useEffect, useRef, useState } from "react";
import type { ITheme, Terminal } from "@xterm/xterm";
import { TerminalSquare, X } from "lucide-react";
import { useTranslation } from "@/lib/i18n";
import { installTerminalResourceBridge, terminalResourceApi, useTerminalResources,
  type TerminalResource } from "@/lib/desktop/terminal-resources";
import styles from "./terminal-resources.module.css";

function terminalTheme(): ITheme {
  const probe = document.createElement("span");
  probe.hidden = true; document.body.append(probe);
  const keys = { background: "bg", foreground: "fg", cursor: "cursor", cursorAccent: "bg",
    selectionBackground: "selection", black: "black", red: "red", green: "green", yellow: "yellow",
    blue: "blue", magenta: "magenta", cyan: "cyan", white: "white", brightBlack: "bright-black",
    brightRed: "bright-red", brightGreen: "bright-green", brightYellow: "bright-yellow",
    brightBlue: "bright-blue", brightMagenta: "bright-magenta", brightCyan: "bright-cyan", brightWhite: "bright-white" };
  const theme: ITheme = {};
  // ITheme also contains array-valued fields (extendedAnsi). Only the exact
  // scalar color keys above may receive a computed CSS color string.
  for (const key of Object.keys(keys) as Array<keyof typeof keys>) {
    const token = keys[key];
    probe.style.color = `var(--terminal-${token})`;
    theme[key] = getComputedStyle(probe).color;
  }
  probe.remove(); return theme;
}

/** Attaches a view to the SAME native PTY. Never creates a shell or replays input. */
function TerminalResourceView({ resource, sessionId, onHide }: {
  resource: TerminalResource; sessionId: string; onHide: () => void;
}) {
  const { text } = useTranslation();
  const host = useRef<HTMLDivElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const [error, setError] = useState("");
  const api = terminalResourceApi();
  const identity = { terminal_id: resource.terminal_id, generation: resource.generation };
  const control = async (action: string, extra: Record<string, unknown> = {}) => {
    try {
      const result = await api?.resource(action, { ...identity, ...extra });
      if (!result?.ok) setError(result?.error || "terminal_unavailable");
    } catch { setError("terminal_result_unconfirmed"); }
  };
  useEffect(() => {
    dialog.current?.showModal();
    return () => dialog.current?.close();
  }, []);
  useEffect(() => {
    if (!api || !host.current) return;
    let disposed = false, terminal: Terminal | undefined, inputBlocked = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let resize: ResizeObserver | undefined, theme: MutationObserver | undefined;
    let cursor = 0, writeQueue = Promise.resolve();
    const input = (data: string) => {
      writeQueue = writeQueue.then(async () => {
        if (disposed || inputBlocked) return;
        const reply = await api.resource("input", { ...identity, data });
        if (!reply.ok) throw Error(reply.error || "terminal_input_unconfirmed");
      }).catch(() => {
        inputBlocked = true;
        if (!disposed) { setError("terminal_input_unconfirmed"); if (terminal) terminal.options.disableStdin = true; }
      });
    };
    void (async () => {
      const [{ Terminal: XTerm }, { FitAddon }] = await Promise.all([import("@xterm/xterm"), import("@xterm/addon-fit")]);
      if (disposed || !host.current) return;
      terminal = new XTerm({ allowProposedApi: false, disableStdin: true, cursorBlink: true,
        fontSize: 13, scrollback: 5000, theme: terminalTheme() });
      const fit = new FitAddon(); terminal.loadAddon(fit); terminal.open(host.current); fit.fit();
      terminal.onData(input);
      const fitView = () => {
        if (disposed || !terminal) return;
        fit.fit();
        void api.resource("resize", { ...identity, cols: terminal.cols, rows: terminal.rows }).catch(() => {});
      };
      resize = new ResizeObserver(fitView); resize.observe(host.current); fitView();
      theme = new MutationObserver(() => { if (terminal && !disposed) terminal.options.theme = terminalTheme(); });
      theme.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "style", "data-theme"] });
      const poll = async () => {
        if (disposed || !terminal) return;
        try {
          const reply = await api.resource("read", { ...identity, cursor });
          if (disposed) return;
          if (!reply.ok || reply.generation !== identity.generation) {
            terminal.options.disableStdin = true; setError(reply.error || "terminal_instance_changed"); return;
          }
          if (reply.truncated) terminal.writeln(text("[Older output was discarded]", "[较早的输出已被丢弃]"));
          if (reply.data) await new Promise<void>(resolve => terminal!.write(reply.data!, resolve));
          if (disposed) return;
          cursor = reply.next_cursor ?? cursor;
          terminal.options.disableStdin = inputBlocked || reply.status !== "running";
          timer = setTimeout(() => { void poll(); }, reply.has_more ? 0 : 350);
        } catch {
          if (!disposed) { setError("terminal_read_unavailable"); terminal.options.disableStdin = true; }
        }
      };
      void poll();
    })().catch(() => { if (!disposed) setError("terminal_view_unavailable"); });
    return () => {
      disposed = true; clearTimeout(timer); resize?.disconnect(); theme?.disconnect(); terminal?.dispose();
      // Hide only detaches this viewer, never closes/releases the native terminal.
    };
    // Metadata updates must not detach/replay the existing terminal view.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, resource.terminal_id, resource.generation]);
  return <dialog className={styles.viewer} ref={dialog} onCancel={event => { event.preventDefault(); onHide(); }}
    aria-label={text("Terminal resource", "终端资源")}>
    <header><TerminalSquare size={16} /><strong>{text("Terminal", "终端")}</strong>
      <span>{resource.status} · PID {resource.pid ?? "—"}</span>
      <button type="button" onClick={onHide} aria-label={text("Hide terminal view", "隐藏终端视图")}><X size={16} /></button>
    </header>
    <p className={styles.location}>{text("Starting directory", "起始目录")}: {resource.start_cwd}</p>
    <div className={styles.screen} ref={host} />
    {error && <p role="alert">{error} · {text("Input was not retried. Reopen the view to inspect before typing again.", "没有自动重发输入。请重新打开视图检查，再决定是否继续输入。")}</p>}
    <footer>
      <span>{text("Pause the Agent with the conversation task control.", "需要暂停 Agent 时，请使用会话的任务暂停按钮。")}</span>
      <button type="button" disabled={resource.status !== "running"} onClick={() => void control("input", { data: "\x03" })}>Ctrl+C</button>
      <button type="button" onClick={() => void control("share", { shared: !resource.shared, session_id: sessionId })}>
        {resource.shared ? text("Make private", "设为私有") : text("Share terminal", "共享终端")}</button>
      <button type="button" disabled={["closed", "exited"].includes(resource.status)} onClick={() => void control("close")}>
        {text("Close terminal", "关闭终端")}</button>
    </footer>
  </dialog>;
}

export function TerminalResourceList({ sessionId }: { sessionId: string | null }) {
  const { text } = useTranslation();
  const { rows, available, error } = useTerminalResources();
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => { installTerminalResourceBridge(); }, []);
  useEffect(() => { setSelected(null); }, [sessionId]);
  if (!available || !sessionId) return null;
  const owned = Object.values(rows).filter(row => row.session_ids.includes(sessionId));
  const resource = owned.find(row => row.terminal_id === selected);
  if (!owned.length && !error) return null;
  return <section className={styles.resources} aria-label={text("Terminal resources", "终端资源")}>
    <h3>{text("Terminals", "终端")}</h3>
    {error && <p role="status">{text("Terminal status is unavailable.", "暂时无法获取终端状态。")}</p>}
    {owned.map(row => <button key={row.terminal_id} type="button" className={styles.row}
      data-resource-kind="terminal" onClick={() => setSelected(row.terminal_id)}>
      <TerminalSquare size={16} /><span><strong>{row.preset === "claude" ? "Claude Code" : text("Terminal", "终端")}</strong>
        <small>{row.status} · {row.shared ? text("Shared", "共享") : text("Private", "私有")} · {row.start_cwd}</small></span>
    </button>)}
    {resource && <TerminalResourceView key={resource.generation} resource={resource} sessionId={sessionId} onHide={() => setSelected(null)} />}
  </section>;
}
