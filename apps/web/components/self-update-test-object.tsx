"use client";

import { useEffect, useRef, useState } from "react";
import { RenameDialog } from "./chat/rename-dialog";
import { getSocket } from "@/lib/runtime-bridge/state";

type Fixture = {
  nonce: string; session_id: string; object_id: string; initial_title: string;
  title: string; deadline: number; socket: WebSocket;
  phase: "initial" | "renamed" | "restored"; value: string;
};

/** A real rename control bound only to one check's ephemeral backend object. */
export function SelfUpdateTestObject() {
  const active = useRef<Fixture | null>(null);
  const [fixture, setFixture] = useState<Fixture | null>(null);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const clear = () => { clearTimeout(timer); active.current = null; setFixture(null); };
    const control = (event: Event) => {
      const d = (event as CustomEvent).detail;
      if (d?.mode === "abandon") {
        if (active.current?.nonce === d.nonce) clear();
        return;
      }
      const socket = getSocket();
      if (active.current || d?.mode !== "open" || window.openprogramDesktop?.windowId !== "main" ||
          !socket || socket.readyState !== WebSocket.OPEN || !/^[0-9a-f]{64}$/.test(d.nonce ?? "") ||
          !/^[A-Za-z0-9_-]{1,256}$/.test(d.session_id ?? "") ||
          !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(d.object_id ?? "") ||
          [d.initial_title, d.title].some((value) => typeof value !== "string" || !value.trim() || [...value].length > 120) ||
          !Number.isFinite(d.deadline) || d.deadline <= Date.now() / 1000 || d.deadline > Date.now() / 1000 + 60 ||
          location.pathname !== `/s/${d.session_id}` || document.querySelector('[role="dialog"]')) return;
      const next: Fixture = { nonce: d.nonce, session_id: d.session_id, object_id: d.object_id,
        initial_title: d.initial_title, title: d.title, deadline: d.deadline, socket,
        phase: "initial", value: d.initial_title };
      active.current = next;
      setFixture(next);
      timer = setTimeout(clear, Math.max(1, d.deadline * 1000 - Date.now()));
    };
    const receive = (event: Event) => {
      const message = (event as CustomEvent).detail;
      const d = message?.data, current = active.current;
      if (message?.type !== "self_update_test_object" || !current || !d?.ok ||
          d.nonce !== current.nonce || d.object_id !== current.object_id ||
          getSocket() !== current.socket || location.pathname !== `/s/${current.session_id}` ||
          Date.now() / 1000 >= current.deadline) return;
      if (!((current.phase === "initial" && d.phase === "renamed" && d.title === current.title) ||
          (current.phase === "renamed" && d.phase === "restored" && d.title === current.initial_title))) return;
      const next: Fixture = { ...current, phase: d.phase, value: d.title };
      active.current = next;
      setFixture(next);
    };
    window.addEventListener("op:self-update-test-object", control);
    window.addEventListener("op:ws-message", receive);
    return () => {
      clear();
      window.removeEventListener("op:self-update-test-object", control);
      window.removeEventListener("op:ws-message", receive);
    };
  }, []);
  function send(op: "rename" | "restore", title: string) {
    const current = active.current;
    if (!current || current !== fixture || getSocket() !== current.socket ||
        current.socket.readyState !== WebSocket.OPEN || Date.now() / 1000 >= current.deadline ||
        location.pathname !== `/s/${current.session_id}` ||
        (op === "rename" ? current.phase !== "initial" || title !== current.title
          : current.phase !== "renamed" || title !== current.initial_title)) return;
    current.socket.send(JSON.stringify({ action: "self_update_test_object", nonce: current.nonce,
      object_id: current.object_id, op, title }));
  }
  if (!fixture) return null;
  return <>
    <span id="selfUpdateTestObjectState" hidden data-nonce={fixture.nonce}
      data-object-id={fixture.object_id} data-phase={fixture.phase} data-title={fixture.value} />
    {fixture.phase !== "restored" ? <RenameDialog id="selfUpdateTestObjectDialog" preserveFocus
      initial={fixture.initial_title} onSubmit={(title) => send("rename", title)}
      onCancel={() => send("restore", fixture.initial_title)} /> : null}
  </>;
}
