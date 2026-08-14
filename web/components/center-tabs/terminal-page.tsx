"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { TerminalSquare } from "lucide-react";

import { desktopBridge } from "@/lib/desktop-bridge";
import { useTranslation } from "@/lib/i18n";
import { useCurrentProject } from "@/lib/state/files-shared";
import styles from "./center-tabs.module.css";

export function TerminalPage() {
  const { text } = useTranslation();
  const project = useCurrentProject();
  const api = desktopBridge()?.terminal;
  const id = useMemo(() => `terminal:${desktopBridge()?.windowId ?? "web"}`, []);
  const [output, setOutput] = useState("");
  const [command, setCommand] = useState("");
  const [ready, setReady] = useState(false);
  const outputRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (!api || project === undefined) return;
    const unsubscribe = api.onData((payload) => {
      if (payload.id !== id) return;
      setOutput((value) => `${value}${payload.data}`.slice(-200_000));
      if (payload.done) setReady(false);
    });
    void api.start({ id, cwd: project?.path }).then((result) => {
      setReady(result.ok);
      if (!result.ok) setOutput(text(`Terminal unavailable: ${result.error}`, `终端不可用：${result.error}`));
    });
    return () => {
      unsubscribe();
      api.stop(id);
    };
  }, [api, id, project]);

  useEffect(() => {
    if (outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight;
  }, [output]);

  if (!api) {
    return <div className={styles.terminalEmpty}>{text("Terminal is available in the desktop app.", "终端仅在桌面应用中可用。")}</div>;
  }

  function submit() {
    if (!ready || !command.trim()) return;
    setOutput((value) => `${value}$ ${command}\n`.slice(-200_000));
    api!.write(id, `${command}\n`);
    setCommand("");
  }

  return (
    <div className={styles.terminalPane}>
      <div className={styles.terminalHeader}>
        <TerminalSquare size={15} aria-hidden="true" />
        <span>{project?.path ?? text("Home directory", "主目录")}</span>
      </div>
      <pre ref={outputRef} className={styles.terminalOutput} aria-live="polite">{output}</pre>
      <div className={styles.terminalInputRow}>
        <span>$</span>
        <input
          value={command}
          disabled={!ready}
          onChange={(event) => setCommand(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter") submit(); }}
          aria-label={text("Terminal command", "终端命令")}
          autoFocus
        />
      </div>
    </div>
  );
}
