"use client";

/**
 * Per-node retry panel inside the execution DAG view.
 *
 * Opened from a TreeNodeRow's "modify" action — surfaces editable
 * params (flattened to dotted keys) and re-runs the node via the
 * ``retry_node`` WS action. Extracted from ``execution-dag.tsx`` so
 * the file isn't carrying two semi-related UI flows.
 */
import type React from "react";
import { useMemo, useState } from "react";

import { useSessionStore } from "@/lib/session-store";

import type { TNode } from "./types";
import { filteredParams, flattenParams, wsSend } from "./types";

export function RetryPanel({
  node,
  onClose,
}: {
  node: TNode;
  onClose: () => void;
}) {
  const fields = useMemo(() => {
    const out: { key: string; value: string; long: boolean }[] = [];
    flattenParams(filteredParams(node.params), "", out);
    return out;
  }, [node.params]);
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(fields.map((f) => [f.key, f.value])),
  );
  const sessionId = useSessionStore((s) => s.currentSessionId);

  function execute() {
    if (node.status === "running" || !node.path) return;
    const params: Record<string, unknown> = {};
    for (const f of fields) {
      const raw = values[f.key] ?? "";
      let parsed: unknown;
      try {
        parsed = JSON.parse(raw);
      } catch {
        parsed = raw;
      }
      const parts = f.key.split(".");
      let obj = params;
      for (let i = 0; i < parts.length - 1; i++) {
        if (typeof obj[parts[i]] !== "object" || obj[parts[i]] == null) {
          obj[parts[i]] = {};
        }
        obj = obj[parts[i]] as Record<string, unknown>;
      }
      obj[parts[parts.length - 1]] = parsed;
    }
    onClose();
    if (!sessionId) return;
    wsSend({
      action: "retry_node",
      node_path: node.path,
      session_id: sessionId,
      params,
    });
  }

  return (
    <div className="retry-panel" style={{ display: "block" }}>
      <div
        style={{
          marginBottom: 6,
          color: "var(--text-secondary)",
          fontSize: 11,
        }}
      >
        Modify <b>{node.name}</b> with:
      </div>
      {fields.length === 0 ? (
        <div
          style={{
            color: "var(--text-muted)",
            fontSize: 11,
            marginBottom: 6,
          }}
        >
          No editable parameters
        </div>
      ) : (
        fields.map((f) => (
          <div className="retry-field" key={f.key}>
            <label className="retry-field-label">{f.key}</label>
            {f.long ? (
              <textarea
                className="retry-field-input"
                value={values[f.key] ?? ""}
                onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) =>
                  setValues((v) => ({ ...v, [f.key]: e.target.value }))
                }
              />
            ) : (
              <input
                className="retry-field-input"
                value={values[f.key] ?? ""}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                  setValues((v) => ({ ...v, [f.key]: e.target.value }))
                }
              />
            )}
          </div>
        ))
      )}
      <div className="retry-panel-actions">
        <button className="retry-exec-btn" onClick={execute}>
          {"▶ Execute"}
        </button>
        <button className="retry-cancel-btn" onClick={onClose}>
          Cancel
        </button>
      </div>
    </div>
  );
}
