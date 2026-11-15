"use client";

import { useEffect, useRef, useState } from "react";
import type { ITheme, Terminal } from "@xterm/xterm";
import { TerminalSquare, X } from "lucide-react";
import { TerminalContextMenu } from "@/components/center-tabs/pages/terminal-context-menu";
import { useTranslation } from "@/lib/i18n";
import { terminalResourceApi,
  type TerminalResource, type TerminalReply } from "@/lib/desktop/terminal-resources";
import { processIsActive, type ManagedProcess } from "@/lib/net/execution/process-client";
import styles from "./terminal-resources.module.css";
import { Button } from "@/components/ui/button";

export function terminalTheme(): ITheme {
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
export function TerminalResourceView({ resource, sessionId, active = true, onHide }: {
  resource: TerminalResource; sessionId: string;
  /** False while the view is mounted but not shown: reading output pauses
   *  and resumes from the same cursor once it is shown again. */
  active?: boolean;
  onHide: () => void;
}) {
  const { text } = useTranslation();
  const host = useRef<HTMLDivElement>(null);
  const [confirmClose, setConfirmClose] = useState(false);
  const [retry, setRetry] = useState(0);
  const [viewReady, setViewReady] = useState(false);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const inputUnconfirmed = useRef(false);
  const [error, setError] = useState("");
  const xterm = useRef<Terminal | null>(null);
  const activeRef = useRef(active);
  activeRef.current = active;
  // The next read, parked while the view is not shown.
  const parked = useRef<(() => void) | null>(null);
  useEffect(() => {
    if (!active || !parked.current) return;
    const resume = parked.current;
    parked.current = null;
    resume();
  }, [active]);
  const api = terminalResourceApi();
  const identity = { terminal_id: resource.terminal_id, generation: resource.generation };
  const send = (action: string, extra: Record<string, unknown> = {}, admitted: () => boolean = () => true): Promise<TerminalReply> => {
    const result = queue.current.then(async () => {
      if (!admitted() || (action === "input" && inputUnconfirmed.current)) throw Error("terminal_input_blocked");
      try {
        const reply = api ? await api.resource(action, { ...identity, ...extra })
          : { ok: false, error: "terminal_unavailable" };
        if (action === "input" && !reply.ok) inputUnconfirmed.current = true;
        return reply;
      } catch (reason) {
        if (action === "input") inputUnconfirmed.current = true;
        throw reason;
      }
    });
    queue.current = result.catch(() => undefined);
    return result;
  };
  const control = async (action: string, extra: Record<string, unknown> = {}) => {
    try {
      const result = await send(action, extra);
      if (!result?.ok) setError(result?.error || "terminal_unavailable");
      else { setError(""); setConfirmClose(false); }
    } catch { setError("terminal_result_unconfirmed"); }
  };

  useEffect(() => {
    if (!api || !host.current) return;
    setError("");
    setViewReady(false);
    let disposed = false, terminal: Terminal | undefined, inputBlocked = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let resize: ResizeObserver | undefined, theme: MutationObserver | undefined;
    let cursor = 0, initialRead = true;
    const input = (data: string) => {
      if (disposed || inputBlocked) return;
      void send("input", { data }, () => !disposed && !inputBlocked).then(reply => {
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
      xterm.current = terminal;
      terminal.onData(input);
      terminal.focus();
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
          if (initialRead) { inputUnconfirmed.current = false; initialRead = false; setViewReady(true); }
          terminal.options.disableStdin = inputBlocked || reply.status !== "running";
          timer = setTimeout(read, reply.has_more ? 0 : 350);
        } catch {
          if (!disposed) { setError("terminal_read_unavailable"); terminal.options.disableStdin = true; }
        }
      };
      const read = () => {
        if (disposed) return;
        if (activeRef.current) void poll();
        else parked.current = read;
      };
      read();
    })().catch(() => { if (!disposed) setError("terminal_view_unavailable"); });
    return () => {
      disposed = true; clearTimeout(timer); resize?.disconnect(); theme?.disconnect(); terminal?.dispose();
      xterm.current = null;
      parked.current = null;
      // Hide only detaches this viewer, never closes/releases the native terminal.
    };
    // Metadata updates must not detach/replay the existing terminal view.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, resource.terminal_id, resource.generation, retry]);
  return <section className={styles.viewer}
    aria-label={text("Terminal resource", "终端资源")}>
    <header><TerminalSquare size={16} /><strong>{resource.preset === "claude" ? "Claude Code" : text("Terminal", "终端")}</strong>
      <span>{resource.status} · PID {resource.pid ?? "—"}</span>
      <Button type="button" variant="ghost" size="icon-sm" onClick={onHide} aria-label={text("Hide terminal view", "隐藏终端视图")}><X size={16} /></Button>
    </header>
    <p className={styles.location}>{text("Starting directory", "起始目录")}: {resource.start_cwd}</p>
    <TerminalContextMenu terminal={() => xterm.current} onClose={() => xterm.current?.focus()}
      canPaste={resource.status === "running" && viewReady && !error}
      onPaste={() => void navigator.clipboard.readText().then(data => { if (data) void control("input", { data }); })}>
      <div className="flex-1 min-h-0 overflow-hidden" ref={host} />
    </TerminalContextMenu>
    {error && <div role="alert"><p>{error} · {text("Input was not retried.", "没有自动重发输入。")}</p>
      <Button type="button" variant="elevated" size="sm" onClick={() => setRetry(value => value + 1)}>{text("Reconnect view", "重新连接视图")}</Button></div>}
    <footer>
      <span>{text("Pause the Agent with the conversation task control.", "需要暂停 Agent 时，请使用会话的任务暂停按钮。")}</span>
      <Button type="button" variant="elevated" size="sm" disabled={resource.status !== "running" || !viewReady || !!error} onClick={() => void control("input", { data: "\x03" })}>Ctrl+C</Button>
      <Button type="button" variant="elevated" size="sm" onClick={() => void control("share", { shared: !resource.shared, session_id: sessionId })}>
        {resource.shared ? text("Make private", "设为私有") : text("Share terminal", "共享终端")}</Button>
      <Button type="button" variant="elevated" size="sm" disabled={["closed", "exited"].includes(resource.status)} onClick={() => setConfirmClose(true)}>
        {text("Close terminal", "关闭终端")}</Button>
    </footer>
    {confirmClose && <div role="alert">
      <p>{text("Terminate this terminal and its running processes?", "终止此终端及其中运行的进程？")}</p>
      <Button type="button" variant="destructive" size="sm" onClick={() => void control("close")}>{text("Terminate terminal", "终止终端")}</Button>
      <Button type="button" variant="elevated" size="sm" onClick={() => setConfirmClose(false)}>{text("Cancel", "取消")}</Button>
    </div>}
  </section>;
}

/** A read-only mirror of the SAME native PTY for the chat preview: it reads
 *  output and never sends input, resizes the terminal or replays anything. */
export function TerminalMirror({ resource }: { resource: TerminalResource }) {
  const { text } = useTranslation();
  const host = useRef<HTMLDivElement>(null);
  const [error, setError] = useState("");
  const xterm = useRef<Terminal | null>(null);
  const api = terminalResourceApi();

  useEffect(() => {
    if (!api || !host.current) return;
    setError("");
    const identity = { terminal_id: resource.terminal_id, generation: resource.generation };
    let disposed = false, terminal: Terminal | undefined, cursor = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let resize: ResizeObserver | undefined;
    void (async () => {
      const [{ Terminal: XTerm }, { FitAddon }] = await Promise.all([import("@xterm/xterm"), import("@xterm/addon-fit")]);
      if (disposed || !host.current) return;
      terminal = new XTerm({ allowProposedApi: false, disableStdin: true, cursorBlink: false,
        fontSize: 11, scrollback: 1000, theme: terminalTheme() });
      const fit = new FitAddon(); terminal.loadAddon(fit); terminal.open(host.current); fit.fit();
      resize = new ResizeObserver(() => { if (!disposed) fit.fit(); }); resize.observe(host.current);
      xterm.current = terminal;
      const poll = async () => {
        if (disposed || !terminal) return;
        try {
          const reply = await api.resource("read", { ...identity, cursor });
          if (disposed) return;
          if (!reply.ok || reply.generation !== identity.generation) {
            setError(reply.error || "terminal_instance_changed"); return;
          }
          if (reply.truncated) terminal.writeln(text("[Older output was discarded]", "[较早的输出已被丢弃]"));
          if (reply.data) await new Promise<void>(resolve => terminal!.write(reply.data!, resolve));
          cursor = reply.next_cursor ?? cursor;
          timer = setTimeout(() => { void poll(); }, reply.has_more ? 0 : 500);
        } catch {
          if (!disposed) setError("terminal_read_unavailable");
        }
      };
      void poll();
    })().catch(() => { if (!disposed) setError("terminal_view_unavailable"); });
    return () => { disposed = true; clearTimeout(timer); resize?.disconnect(); terminal?.dispose(); xterm.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, resource.terminal_id, resource.generation]);

  return <div className={styles.mirror}>
    <TerminalContextMenu terminal={() => xterm.current}>
      <div className="absolute inset-[4px_6px]" ref={host} />
    </TerminalContextMenu>
    {error && <p className={styles.mirrorError} role="status">{text("Terminal output is unavailable", "终端输出不可用")}</p>}
  </div>;
}

/** A command the agent left running in the background, as a read-only
 *  terminal: its retained output, and a Stop that ends it. The agent types
 *  into it with write_stdin, never through this view. */
export function BackgroundTerminalView({ process, output, onStop, onHide }: {
  process: ManagedProcess; output: string; onStop: () => void; onHide: () => void;
}) {
  const { text } = useTranslation();
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<{ terminal: Terminal; written: string } | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!host.current) return;
    let disposed = false, resize: ResizeObserver | undefined;
    void (async () => {
      const [{ Terminal: XTerm }, { FitAddon }] = await Promise.all([import("@xterm/xterm"), import("@xterm/addon-fit")]);
      if (disposed || !host.current) return;
      const terminal = new XTerm({ allowProposedApi: false, disableStdin: true, cursorBlink: false,
        convertEol: !process.tty, fontSize: 13, scrollback: 5000, theme: terminalTheme() });
      const fit = new FitAddon(); terminal.loadAddon(fit); terminal.open(host.current); fit.fit();
      resize = new ResizeObserver(() => { if (!disposed) fit.fit(); }); resize.observe(host.current);
      view.current = { terminal, written: "" };
      setError("");
    })().catch(() => { if (!disposed) setError("terminal_view_unavailable"); });
    return () => { disposed = true; resize?.disconnect(); view.current?.terminal.dispose(); view.current = null; };
    // One view per command; output updates are appended below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [process.id]);

  useEffect(() => {
    const current = view.current;
    if (!current || output === current.written) return;
    // Retained output only grows, except when its oldest part is dropped.
    if (output.startsWith(current.written)) current.terminal.write(output.slice(current.written.length));
    else { current.terminal.reset(); current.terminal.write(output); }
    current.written = output;
  });

  const active = processIsActive(process);
  const state = active ? text("Running", "运行中")
    : process.status === "exited" ? text(`Exited ${process.exit_code ?? ""}`.trim(), `已退出 ${process.exit_code ?? ""}`.trim())
      : process.status === "stopped" ? text("Stopped", "已停止") : process.status;
  return <section className={styles.viewer} aria-label={text("Background terminal", "后台终端")}>
    <header><TerminalSquare size={16} /><strong>{text("Background terminal", "后台终端")}</strong>
      <span>{state}{process.pid ? ` · PID ${process.pid}` : ""}</span>
      <Button type="button" variant="ghost" size="icon-sm" onClick={onHide} aria-label={text("Hide terminal view", "隐藏终端视图")}><X size={16} /></Button>
    </header>
    <p className={styles.location}>{process.command}</p>
    {process.cwd && <p className={styles.location}>{text("Starting directory", "起始目录")}: {process.cwd}</p>}
    <TerminalContextMenu terminal={() => view.current?.terminal}>
      <div className="flex-1 min-h-0 overflow-hidden" ref={host} />
    </TerminalContextMenu>
    {error && <div role="alert"><p>{error}</p></div>}
    <footer>
      <span>{text("The agent started this command and reads its output.", "这条命令是 agent 启动的，它会读取输出。")}</span>
      <Button type="button" variant="destructive" size="sm" disabled={!active || process.can_stop === false} onClick={onStop}>{text("Stop", "停止")}</Button>
    </footer>
  </section>;
}
