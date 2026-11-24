"use client";

import { useEffect, useRef, useState } from "react";
import type { ITheme, Terminal } from "@xterm/xterm";
import { TerminalSquare, X } from "lucide-react";
import { TerminalContextMenu } from "@/components/center-tabs/pages/terminal-context-menu";
import { useTranslation } from "@/lib/i18n";
import { terminalResourceApi,
  type TerminalResource, type TerminalReply } from "@/lib/desktop/terminal-resources";
import { followTerminalOutput, outputFor, type OutputFollower, type OutputRead } from "@/lib/desktop/terminal-output";
import { processIsActive, type ManagedProcess } from "@/lib/net/execution/process-client";
import styles from "./terminal-resources.module.css";
import { Button } from "@/components/ui/button";

/** How soon a view that could not be drawn, or whose input may have been
 *  lost, connects again; and the longest wait between failed reads. Output
 *  itself is read when the native host says the terminal wrote some
 *  (lib/desktop/terminal-output.ts), never on a timer. */
const RECONNECT_MS = 3000;
const RECONNECT_MAX_MS = 10000;

let xtermChunks: Promise<[typeof import("@xterm/xterm"), typeof import("@xterm/addon-fit")]> | null = null;
/** The terminal's code, fetched once. Started as soon as the resource list
 *  shows a terminal, so opening one draws it without waiting for the chunk. */
export function loadXterm() {
  if (!xtermChunks) {
    xtermChunks = Promise.all([import("@xterm/xterm"), import("@xterm/addon-fit")]);
    // A chunk that failed to arrive is asked for again next time.
    xtermChunks.catch(() => { xtermChunks = null; });
  }
  return xtermChunks;
}

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
      if (!result?.ok) setError(action === "input" ? "terminal_input_unconfirmed" : result?.error || "terminal_unavailable");
      else { setError(""); setConfirmClose(false); }
    } catch { setError(action === "input" ? "terminal_input_unconfirmed" : "terminal_result_unconfirmed"); }
  };

  // Input that may not have arrived blocks more input: the reader is told,
  // and the view reconnects on its own a moment later. Nothing is re-sent.
  useEffect(() => {
    if (error !== "terminal_input_unconfirmed") return;
    const timer = setTimeout(() => setRetry(value => value + 1), RECONNECT_MS);
    return () => clearTimeout(timer);
  }, [error]);

  useEffect(() => {
    if (!api || !host.current) return;
    setError("");
    setViewReady(false);
    let disposed = false, terminal: Terminal | undefined, inputBlocked = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let resize: ResizeObserver | undefined, theme: MutationObserver | undefined;
    let follower: OutputFollower | undefined, stopPush: (() => void) | undefined;
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
      const [{ Terminal: XTerm }, { FitAddon }] = await loadXterm();
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
      // A read that fails is tried again a little later each time, without a
      // word; input waits until output flows again. A terminal that became
      // another instance is redrawn by its row, so this view stops reading.
      const failed = (reason: unknown): OutputRead => {
        if (disposed || !terminal) return "stop";
        console.warn("Terminal: could not read output", reason);
        terminal.options.disableStdin = true;
        return "failed";
      };
      const poll = async (): Promise<OutputRead> => {
        if (disposed || !terminal) return "stop";
        // Not shown: the read waits, from the same cursor, until it is.
        if (!activeRef.current) { parked.current = () => follower?.poke(); return "idle"; }
        try {
          const reply = await api.resource("read", { ...identity, cursor });
          if (disposed) return "stop";
          if (reply.ok && reply.generation !== identity.generation) {
            terminal.options.disableStdin = true;
            console.warn("Terminal: the terminal became another instance", reply.generation);
            return "stop";
          }
          if (!reply.ok) return failed(reply.error);
          if (reply.truncated) terminal.writeln(text("[Older output was discarded]", "[较早的输出已被丢弃]"));
          if (reply.data) await new Promise<void>(resolve => terminal!.write(reply.data!, resolve));
          if (disposed) return "stop";
          cursor = reply.next_cursor ?? cursor;
          if (initialRead) { inputUnconfirmed.current = false; initialRead = false; setViewReady(true); }
          terminal.options.disableStdin = inputBlocked || reply.status !== "running";
          return reply.has_more ? "more" : "idle";
        } catch (reason) { return failed(reason); }
      };
      follower = followTerminalOutput(poll, { maxDelay: RECONNECT_MAX_MS });
      // New output, or the terminal's state changed (it exited): read now.
      const pushed = (chunk: unknown) => {
        if (outputFor(chunk, identity.terminal_id, identity.generation)) follower?.poke();
      };
      const offData = api.onData?.(pushed);
      const offResource = api.onResource(resource => {
        if (resource?.terminal_id === identity.terminal_id && resource.generation === identity.generation) follower?.poke();
      });
      stopPush = () => { offData?.(); offResource(); };
      follower.poke();
    })().catch(reason => {
      // The view itself could not be drawn: it is drawn again shortly.
      if (disposed) return;
      console.warn("Terminal: could not draw the view", reason);
      timer = setTimeout(() => setRetry(value => value + 1), RECONNECT_MS);
    });
    return () => {
      disposed = true; clearTimeout(timer); follower?.dispose(); stopPush?.();
      resize?.disconnect(); theme?.disconnect(); terminal?.dispose();
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
    {error && <div role="alert"><p>{error === "terminal_input_unconfirmed"
      ? text("Your input may not have reached the terminal. It was not sent again.", "输入可能没有送达终端，没有自动重发。")
      : text("That did not go through.", "没有生效。")}</p></div>}
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

/** A mirror of the SAME native PTY for the chat preview: it reads output and
 *  never resizes the terminal or replays anything. `interactive` lets what is
 *  typed into it reach the terminal, in order, while the terminal runs. */
export function TerminalMirror({ resource, interactive = false }: { resource: TerminalResource; interactive?: boolean }) {
  const { text } = useTranslation();
  const host = useRef<HTMLDivElement>(null);
  // Bumped to draw the view again after it could not be drawn.
  const [attempt, setAttempt] = useState(0);
  // What the reader typed last did not reach the terminal.
  const [inputLost, setInputLost] = useState(false);
  const xterm = useRef<Terminal | null>(null);
  const api = terminalResourceApi();

  useEffect(() => {
    if (!api || !host.current) return;
    const identity = { terminal_id: resource.terminal_id, generation: resource.generation };
    let disposed = false, terminal: Terminal | undefined, cursor = 0, typing = Promise.resolve();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let resize: ResizeObserver | undefined;
    let follower: OutputFollower | undefined, stopPush: (() => void) | undefined;
    void (async () => {
      const [{ Terminal: XTerm }, { FitAddon }] = await loadXterm();
      if (disposed || !host.current) return;
      terminal = new XTerm({ allowProposedApi: false, disableStdin: !interactive, cursorBlink: interactive,
        fontSize: 11, scrollback: 1000, theme: terminalTheme() });
      const fit = new FitAddon(); terminal.loadAddon(fit); terminal.open(host.current); fit.fit();
      resize = new ResizeObserver(() => { if (!disposed) fit.fit(); }); resize.observe(host.current);
      xterm.current = terminal;
      if (interactive) terminal.onData((data) => {
        // One at a time, so keys reach the terminal in the order typed.
        typing = typing.then(async () => {
          if (disposed) return;
          const reply = await api.resource("input", { ...identity, data }).catch(() => null);
          if (!disposed) setInputLost(!reply?.ok);
        });
      });
      // A read that fails is tried again a little later each time; the
      // output read so far stays. Another instance is drawn by a new mirror.
      const failed = (reason: unknown): OutputRead => {
        if (disposed) return "stop";
        console.warn("Terminal: could not read output", reason);
        return "failed";
      };
      const poll = async (): Promise<OutputRead> => {
        if (disposed || !terminal) return "stop";
        try {
          const reply = await api.resource("read", { ...identity, cursor });
          if (disposed) return "stop";
          if (reply.ok && reply.generation !== identity.generation) return "stop";
          if (!reply.ok) return failed(reply.error);
          if (reply.truncated) terminal.writeln(text("[Older output was discarded]", "[较早的输出已被丢弃]"));
          if (reply.data) await new Promise<void>(resolve => terminal!.write(reply.data!, resolve));
          cursor = reply.next_cursor ?? cursor;
          if (interactive) terminal.options.disableStdin = reply.status !== "running";
          return reply.has_more ? "more" : "idle";
        } catch (reason) { return failed(reason); }
      };
      follower = followTerminalOutput(poll, { maxDelay: RECONNECT_MAX_MS });
      const offData = api.onData?.(chunk => {
        if (outputFor(chunk, identity.terminal_id, identity.generation)) follower?.poke();
      });
      const offResource = api.onResource(resource => {
        if (resource?.terminal_id === identity.terminal_id && resource.generation === identity.generation) follower?.poke();
      });
      stopPush = () => { offData?.(); offResource(); };
      follower.poke();
    })().catch(reason => {
      if (disposed) return;
      console.warn("Terminal: could not draw the view", reason);
      timer = setTimeout(() => setAttempt(value => value + 1), RECONNECT_MS);
    });
    return () => {
      disposed = true; clearTimeout(timer); follower?.dispose(); stopPush?.();
      resize?.disconnect(); terminal?.dispose(); xterm.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, resource.terminal_id, resource.generation, interactive, attempt]);

  return <div className={styles.mirror}>
    <TerminalContextMenu terminal={() => xterm.current}>
      <div className="absolute inset-[4px_6px]" ref={host} />
    </TerminalContextMenu>
    {inputLost && <p className={styles.mirrorError} role="status">{text("Your input may not have reached the terminal. It was not sent again.", "输入可能没有送达终端，没有自动重发。")}</p>}
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
  // Bumped once the view is drawn, so the output is written into it at once,
  // and to draw it again after it could not be drawn.
  const [, setDrawn] = useState(0);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!host.current) return;
    let disposed = false, resize: ResizeObserver | undefined, timer: ReturnType<typeof setTimeout> | undefined;
    void (async () => {
      const [{ Terminal: XTerm }, { FitAddon }] = await loadXterm();
      if (disposed || !host.current) return;
      const terminal = new XTerm({ allowProposedApi: false, disableStdin: true, cursorBlink: false,
        convertEol: !process.tty, fontSize: 13, scrollback: 5000, theme: terminalTheme() });
      const fit = new FitAddon(); terminal.loadAddon(fit); terminal.open(host.current); fit.fit();
      resize = new ResizeObserver(() => { if (!disposed) fit.fit(); }); resize.observe(host.current);
      view.current = { terminal, written: "" };
      setDrawn(value => value + 1);
    })().catch(reason => {
      if (disposed) return;
      console.warn("Terminal: could not draw the view", reason);
      timer = setTimeout(() => setAttempt(value => value + 1), RECONNECT_MS);
    });
    return () => { disposed = true; clearTimeout(timer); resize?.disconnect(); view.current?.terminal.dispose(); view.current = null; };
    // One view per command; output updates are appended below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [process.id, attempt]);

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
    <footer>
      <span>{text("The agent started this command and reads its output.", "这条命令是 agent 启动的，它会读取输出。")}</span>
      <Button type="button" variant="destructive" size="sm" disabled={!active || process.can_stop === false} onClick={onStop}>{text("Stop", "停止")}</Button>
    </footer>
  </section>;
}
