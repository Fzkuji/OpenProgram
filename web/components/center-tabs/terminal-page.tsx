"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { TerminalSquare } from "lucide-react";

import { desktopBridge, desktopTerminalId } from "@/lib/desktop-bridge";
import { useTranslation } from "@/lib/i18n";
import { useCurrentProject } from "@/lib/state/files-shared";
import styles from "./center-tabs.module.css";

export function TerminalPage({ preset }: { preset: "shell" | "claude" }) {
  const { text } = useTranslation();
  const project = useCurrentProject();
  const hostRef = useRef<HTMLDivElement>(null);
  const bridge = desktopBridge();
  const api = bridge?.terminal;
  const id = useMemo(
    () => bridge ? desktopTerminalId(bridge, preset) : `terminal:web:${preset}`,
    [bridge, preset],
  );
  const [error, setError] = useState("");

  useEffect(() => {
    const host = hostRef.current;
    if (!api || !host || project === undefined) return;

    let disposed = false;
    let observer: ResizeObserver | undefined;
    let terminal: import("@xterm/xterm").Terminal | undefined;
    let input: import("@xterm/xterm").IDisposable | undefined;
    const unsubscribe = api.onData((payload) => {
      if (payload.id !== id || !terminal) return;
      if (payload.data) terminal.write(payload.data);
      if (payload.done) {
        terminal.writeln("");
        terminal.writeln(`[process exited${typeof payload.exitCode === "number" ? `: ${payload.exitCode}` : ""}]`);
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
        fontFamily: '"JetBrains Mono Variable", "SFMono-Regular", Menlo, monospace',
        fontSize: 13,
        lineHeight: 1.25,
        scrollback: 10_000,
        theme: {
          background: "#111214",
          foreground: "#d7dbe0",
          cursor: "#d7dbe0",
          selectionBackground: "#315b8d",
        },
      });
      const fit = new FitAddon();
      terminal.loadAddon(fit);
      terminal.open(host);
      fit.fit();
      input = terminal.onData((data) => api.write(id, data));

      const result = await api.start({
        id,
        cwd: project?.path,
        preset,
        cols: terminal.cols,
        rows: terminal.rows,
      });
      if (disposed) {
        terminal.dispose();
        return;
      }
      if (!result.ok) {
        setError(result.error ?? "terminal_start_failed");
        terminal.writeln(`Terminal unavailable: ${result.error ?? "terminal_start_failed"}`);
        return;
      }
      if (typeof result.pid === "number") host.dataset.processId = String(result.pid);
      observer = new ResizeObserver(() => {
        if (!terminal || disposed) return;
        fit.fit();
        api.resize(id, terminal.cols, terminal.rows);
      });
      observer.observe(host);
      terminal.focus();
    })().catch(() => {
      if (!disposed) setError("terminal_renderer_failed");
    });

    return () => {
      disposed = true;
      unsubscribe();
      observer?.disconnect();
      input?.dispose();
      terminal?.dispose();
      delete host.dataset.processId;
    };
  }, [api, id, preset, project]);

  if (!api) {
    return (
      <div className={styles.terminalEmpty}>
        {text("Local terminals require the desktop app.", "本地终端需要桌面应用。")}
      </div>
    );
  }

  return (
    <div className={styles.terminalPane}>
      <div className={styles.terminalHeader}>
        <TerminalSquare size={15} aria-hidden="true" />
        <strong>{preset === "claude" ? "Claude Code" : text("Terminal", "终端")}</strong>
        <span>{project?.path ?? text("Home directory", "主目录")}</span>
        {error ? <span className={styles.terminalError}>{error}</span> : null}
      </div>
      <div
        ref={hostRef}
        className={styles.terminalHost}
        aria-label={preset === "claude" ? "Claude Code terminal" : text("Terminal", "终端")}
      />
    </div>
  );
}
