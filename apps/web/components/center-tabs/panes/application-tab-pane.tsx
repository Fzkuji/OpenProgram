"use client";

import { useEffect, useRef, useState } from "react";
import { applicationRequest, type ApplicationInstance, type ApplicationRun } from "@/lib/net/applications";

export function ApplicationTabPane({ instanceId }: { instanceId: string }) {
  const [instance, setInstance] = useState<ApplicationInstance | null>(null);
  const [error, setError] = useState("");
  const channel = useRef<MessageChannel | null>(null);
  const base = `/api/application-instances/${encodeURIComponent(instanceId)}`;
  useEffect(() => {
    let disposed = false;
    setInstance(null);
    setError("");
    applicationRequest<ApplicationInstance>(base).then(value => {
      if (!disposed) setInstance(value);
    }).catch(reason => { if (!disposed) setError(String(reason.message ?? reason)); });
    return () => { disposed = true; channel.current?.port1.close(); channel.current?.port2.close(); };
  }, [base]);

  function connect(frame: HTMLIFrameElement) {
    if (!instance || !frame.contentWindow) return;
    channel.current?.port1.close();
    channel.current?.port2.close();
    const connection = new MessageChannel();
    channel.current = connection;
    const definition = instance.application;
    connection.port1.onmessage = async ({ data }) => {
      if (!data || typeof data.id !== "string" || data.id.length > 128) return;
      const { id, method } = data;
      const params = data.params ?? {};
      try {
        let result: unknown;
        if (method === "state.load") result = await applicationRequest(`${base}/state`);
        else if (method === "state.save") result = await applicationRequest(`${base}/state`, "PUT", { value: params.value, version: params.version });
        else if (method === "runs.list") result = (await applicationRequest<ApplicationInstance>(base)).runs;
        else if (method === "operation.run") {
          if (typeof params.operation !== "string") throw new Error("An operation name is required");
          result = await applicationRequest(`${base}/operations/${encodeURIComponent(params.operation)}`, "POST", {
            digest: definition.digest, input: params.input, request_key: params.request_key,
          });
        } else if (["run.status", "run.cancel", "run.answer"].includes(method)) {
          if (typeof params.id !== "string" || !/^app_[a-f0-9]{32}$/.test(params.id)) throw new Error("Invalid run identifier");
          const runUrl = `/api/application-runs/${params.id}`;
          const run = await applicationRequest<ApplicationRun>(runUrl);
          if (run.instance_id !== instanceId) throw new Error("This run belongs to another application instance");
          if (method === "run.status") {
            const after = Number.isSafeInteger(params.after) && params.after >= 0 ? params.after : 0;
            result = await applicationRequest(`${runUrl}?after=${after}`);
          } else result = await applicationRequest(`${runUrl}/${method === "run.cancel" ? "cancel" : "answer"}`, "POST", {
            request_id: params.request_id, answer: params.answer,
          });
        } else throw new Error("Unsupported application request");
        connection.port1.postMessage({ id, result });
      } catch (reason) {
        connection.port1.postMessage({ id, error: reason instanceof Error ? reason.message : String(reason) });
      }
    };
    frame.contentWindow.postMessage({ type: "openprogram.application.connect" }, "*", [connection.port2]);
  }

  if (error) return <div role="alert" className="p-6 text-sm text-text-muted">{error}</div>;
  if (!instance) return <div className="p-6 text-sm text-text-muted">Loading…</div>;
  const definition = instance.application;
  return <iframe
    title={definition.display_title || definition.title}
    src={instance.ui_url}
    sandbox="allow-scripts"
    referrerPolicy="no-referrer"
    className="h-full w-full border-0"
    onLoad={event => connect(event.currentTarget)}
  />;
}
