"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ClipboardPaste,
  Copy,
  Eraser,
  RotateCcw,
  Square,
  TerminalSquare,
} from "lucide-react";
import type { ITheme, Terminal as XTermTerminal } from "@xterm/xterm";

import {
  desktopBridge,
  desktopTerminalId,
  type DesktopTerminalApi,
} from "@/lib/desktop/desktop-bridge";
import { Button } from "@/components/ui/button";
import { useTranslation } from "@/lib/i18n";
import { useCurrentProject } from "@/lib/files/files-shared";
import styles from "./pages.module.css";
import { TerminalContextMenu } from "./terminal-context-menu";

const PROJECT_RESOLVE_GRACE_MS = 500;

type TerminalStatus = "waiting" | "starting" | "running" | "exited" | "error";

/** xterm takes colour strings, not CSS: resolve a terminal token (every
 *  theme defines them, styles/themes/fallback.css) to its computed colour. */
function resolveThemeColor(probe: HTMLSpanElement, token: string): string {
  probe.style.color = `var(${token})`;
  return getComputedStyle(probe).color;
}

function readTerminalTheme(): ITheme {
  const probe = document.createElement("span");
  probe.hidden = true;
  document.body.appendChild(probe);
  const color = (token: string) => resolveThemeColor(probe, token);
  const theme: ITheme = {
    background: color("--terminal-bg"),
    foreground: color("--terminal-fg"),
    cursor: color("--terminal-cursor"),
    cursorAccent: color("--terminal-bg"),
    selectionBackground: color("--terminal-selection"),
    black: color("--terminal-black"),
    red: color("--terminal-red"),
    green: color("--terminal-green"),
    yellow: color("--terminal-yellow"),
    blue: color("--terminal-blue"),
    magenta: color("--terminal-magenta"),
    cyan: color("--terminal-cyan"),
    white: color("--terminal-white"),
    brightBlack: color("--terminal-bright-black"),
    brightRed: color("--terminal-bright-red"),
    brightGreen: color("--terminal-bright-green"),
    brightYellow: color("--terminal-bright-yellow"),
    brightBlue: color("--terminal-bright-blue"),
    brightMagenta: color("--terminal-bright-magenta"),
    brightCyan: color("--terminal-bright-cyan"),
    brightWhite: color("--terminal-bright-white"),
  };
  probe.remove();
  return theme;
}

async function copySelection(terminal: XTermTerminal): Promise<void> {
  const selection = terminal.getSelection();
  if (!selection) return;
  await navigator.clipboard.writeText(selection);
}

async function pasteClipboard(api: DesktopTerminalApi, id: string): Promise<void> {
  const content = await navigator.clipboard.readText();
  if (content) api.write(id, content);
}

function clearTerminalView(terminal: XTermTerminal): void {
  terminal.clear();
  terminal.write("\x1b[2J\x1b[3J\x1b[H");
}

export function TerminalPage({ preset }: { preset: "shell" | "claude" }) {
  const { text } = useTranslation();
  const project = useCurrentProject();
  const hostRef = useRef<HTMLDivElement>(null);
  const terminalRef = useRef<XTermTerminal | null>(null);
  const bridge = desktopBridge();
  const api = bridge?.terminal;
  const id = useMemo(
    () => bridge ? desktopTerminalId(bridge, preset) : `terminal:web:${preset}`,
    [bridge, preset],
  );
  const [error, setError] = useState("");
  const [status, setStatus] = useState<TerminalStatus>("waiting");
  const [exitCode, setExitCode] = useState<number | undefined>();
  const [hasSelection, setHasSelection] = useState(false);
  const [restartNonce, setRestartNonce] = useState(0);
  const [startCwd, setStartCwd] = useState<string | null | undefined>(undefined);

  useEffect(() => {
    if (startCwd !== undefined) return;
    if (project !== undefined) {
      setStartCwd(project?.path ?? null);
      return;
    }
    const timer = window.setTimeout(() => {
      setStartCwd((current) => current === undefined ? null : current);
    }, PROJECT_RESOLVE_GRACE_MS);
    return () => window.clearTimeout(timer);
  }, [project, startCwd]);

  useEffect(() => {
    const host = hostRef.current;
    if (!api || !host || startCwd === undefined) return;

    let disposed = false;
    let resizeObserver: ResizeObserver | undefined;
    let themeObserver: MutationObserver | undefined;
    let terminal: XTermTerminal | undefined;
    let input: import("@xterm/xterm").IDisposable | undefined;
    let selection: import("@xterm/xterm").IDisposable | undefined;
    setStatus("starting");
    setError("");
    setExitCode(undefined);
    setHasSelection(false);

    const unsubscribe = api.onData((payload) => {
      if (payload.id !== id || !terminal) return;
      if (payload.data) terminal.write(payload.data);
      if (payload.done) {
        terminal.writeln("");
        terminal.writeln(`[process exited${typeof payload.exitCode === "number" ? `: ${payload.exitCode}` : ""}]`);
        terminal.options.disableStdin = true;
        terminal.options.cursorBlink = false;
        setExitCode(payload.exitCode);
        setStatus("exited");
      }
    });

    void (async () => {
      const [{ Terminal }, { FitAddon }] = await Promise.all([
        import("@xterm/xterm"),
        import("@xterm/addon-fit"),
      ]);
      if (disposed) return;

      terminal = new Terminal({
        allowProposedApi: false,
        cursorBlink: true,
        cursorStyle: "bar",
        // xterm draws on a canvas and needs the resolved stack, not var().
        fontFamily: getComputedStyle(document.documentElement).getPropertyValue("--font-mono").trim() || "monospace",
        fontSize: 13,
        lineHeight: 1.25,
        scrollback: 10_000,
        theme: readTerminalTheme(),
      });
      terminalRef.current = terminal;
      const fit = new FitAddon();
      terminal.loadAddon(fit);
      terminal.open(host);
      fit.fit();
      input = terminal.onData((data) => api.write(id, data));
      selection = terminal.onSelectionChange(() => setHasSelection(terminal?.hasSelection() ?? false));
      terminal.attachCustomKeyEventHandler((event) => {
        if (event.type !== "keydown" || !terminal) return true;
        const key = event.key.toLowerCase();
        const copy = key === "c" && (event.metaKey || (event.ctrlKey && event.shiftKey));
        const paste = key === "v" && (event.metaKey || (event.ctrlKey && event.shiftKey));
        const interrupt = key === "c"
          && event.ctrlKey
          && !event.shiftKey
          && !event.altKey
          && !event.metaKey;
        if (interrupt) {
          // Chromium's native edit accelerator can consume Ctrl+C before
          // xterm turns it into ETX. Terminal copy remains Ctrl+Shift+C.
          api.write(id, "\x03");
          return false;
        }
        if (copy) {
          void copySelection(terminal).catch(() => setError("clipboard_write_failed"));
          return false;
        }
        if (paste) {
          void pasteClipboard(api, id).catch(() => setError("clipboard_read_failed"));
          return false;
        }
        if (key === "k" && event.metaKey) {
          clearTerminalView(terminal);
          return false;
        }
        return true;
      });

      const result = await api.start({
        id,
        cwd: startCwd ?? undefined,
        preset,
        cols: terminal.cols,
        rows: terminal.rows,
      });
      if (disposed) {
        terminal.dispose();
        return;
      }
      if (!result.ok) {
        const code = result.error ?? "terminal_start_failed";
        setError(code);
        setStatus("error");
        terminal.options.disableStdin = true;
        terminal.options.cursorBlink = false;
        terminal.writeln(`Terminal unavailable: ${code}`);
        return;
      }
      if (typeof result.pid === "number") host.dataset.processId = String(result.pid);
      setStatus("running");

      resizeObserver = new ResizeObserver(() => {
        if (!terminal || disposed) return;
        fit.fit();
        api.resize(id, terminal.cols, terminal.rows);
      });
      resizeObserver.observe(host);

      const applyTheme = () => {
        if (terminal && !disposed) terminal.options.theme = readTerminalTheme();
      };
      themeObserver = new MutationObserver(applyTheme);
      themeObserver.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ["data-theme", "class", "style"],
      });
      themeObserver.observe(document.head, { childList: true, subtree: true, characterData: true });
      terminal.focus();
    })().catch(() => {
      if (!disposed) {
        setError("terminal_renderer_failed");
        setStatus("error");
      }
    });

    return () => {
      disposed = true;
      unsubscribe();
      resizeObserver?.disconnect();
      themeObserver?.disconnect();
      input?.dispose();
      selection?.dispose();
      terminal?.dispose();
      if (terminalRef.current === terminal) terminalRef.current = null;
      delete host.dataset.processId;
    };
  }, [api, id, preset, restartNonce, startCwd]);

  const focusTerminal = () => terminalRef.current?.focus();
  const clearTerminal = () => {
    const terminal = terminalRef.current;
    if (!terminal) return;
    clearTerminalView(terminal);
    terminal.focus();
  };
  const copyTerminal = () => {
    const terminal = terminalRef.current;
    if (!terminal) return;
    void copySelection(terminal)
      .then(() => setError(""))
      .catch(() => setError("clipboard_write_failed"))
      .finally(() => terminal.focus());
  };
  const pasteTerminal = () => {
    if (!api || status !== "running") return;
    void pasteClipboard(api, id)
      .then(() => setError(""))
      .catch(() => setError("clipboard_read_failed"))
      .finally(focusTerminal);
  };
  const restartTerminal = () => {
    if (!api || startCwd === undefined) return;
    api.stop(id);
    setStatus("starting");
    setError("");
    setExitCode(undefined);
    setRestartNonce((value) => value + 1);
  };
  const stopTerminal = () => {
    if (!api || status !== "running") return;
    api.stop(id);
    terminalRef.current?.writeln("\r\n[process stopped]");
    if (terminalRef.current) {
      terminalRef.current.options.disableStdin = true;
      terminalRef.current.options.cursorBlink = false;
    }
    setStatus("exited");
    setExitCode(undefined);
    focusTerminal();
  };

  if (!api) {
    return (
      <div className={styles.terminalEmpty}>
        {text("Local terminals require the desktop app.", "本地终端需要桌面应用。")}
      </div>
    );
  }

  const statusText = status === "waiting"
    ? text("Waiting", "等待")
    : status === "starting"
      ? text("Starting", "启动中")
      : status === "running"
        ? text("Running", "运行中")
        : status === "error"
          ? text("Error", "错误")
          : typeof exitCode === "number"
            ? text(`Exited ${exitCode}`, `已退出 ${exitCode}`)
            : text("Stopped", "已停止");
  const pathLabel = startCwd ?? text("Home directory", "主目录");
  const errorText = error === "clipboard_write_failed"
    ? text("Clipboard copy failed", "复制到剪贴板失败")
    : error === "clipboard_read_failed"
      ? text("Clipboard paste failed", "读取剪贴板失败")
      : error === "terminal_renderer_failed"
        ? text("Terminal renderer failed", "终端渲染失败")
        : error;

  return (
    <div className={styles.terminalPane}>
      <div className={styles.terminalHeader}>
        <TerminalSquare size={16} aria-hidden="true" />
        <strong>{preset === "claude" ? "Claude Code" : text("Terminal", "终端")}</strong>
        <span className={`${styles.terminalStatus} ${styles[`terminalStatus_${status}`]}`} role="status">
          <i aria-hidden="true" />
          <span className={styles.terminalStatusText}>{statusText}</span>
        </span>
        {errorText
          ? <span className={styles.terminalError} aria-live="polite">{errorText}</span>
          : <span className={styles.terminalPath}>{pathLabel}</span>}
        <div className={styles.terminalActions} aria-label={text("Terminal actions", "终端操作")}>
          <Button type="button" variant="ghost" size="icon-sm" onClick={copyTerminal} disabled={!hasSelection} aria-label={text("Copy selection", "复制选择内容")} title={text("Copy selection", "复制选择内容")}>
            <Copy className="size-4" aria-hidden="true" />
          </Button>
          <Button type="button" variant="ghost" size="icon-sm" onClick={pasteTerminal} disabled={status !== "running"} aria-label={text("Paste", "粘贴")} title={text("Paste", "粘贴")}>
            <ClipboardPaste className="size-4" aria-hidden="true" />
          </Button>
          <Button type="button" variant="ghost" size="icon-sm" onClick={clearTerminal} disabled={!terminalRef.current} aria-label={text("Clear terminal", "清屏")} title={text("Clear terminal", "清屏")}>
            <Eraser className="size-4" aria-hidden="true" />
          </Button>
          <Button type="button" variant="ghost" size="icon-sm" onClick={restartTerminal} disabled={status === "starting" || startCwd === undefined} aria-label={text("Restart process", "重启进程")} title={text("Restart process", "重启进程")}>
            <RotateCcw className="size-4" aria-hidden="true" />
          </Button>
          <Button type="button" variant="ghost" size="icon-sm" onClick={stopTerminal} disabled={status !== "running"} aria-label={text("Stop process", "停止进程")} title={text("Stop process", "停止进程")}>
            <Square aria-hidden="true" />
          </Button>
        </div>
      </div>
      <TerminalContextMenu terminal={() => terminalRef.current} onPaste={pasteTerminal}
        canPaste={status === "running"} onClose={focusTerminal}>
        <div
          ref={hostRef}
          className={styles.terminalHost}
          aria-label={preset === "claude" ? "Claude Code terminal" : text("Terminal", "终端")}
          onPointerDown={focusTerminal}
        />
      </TerminalContextMenu>
    </div>
  );
}
