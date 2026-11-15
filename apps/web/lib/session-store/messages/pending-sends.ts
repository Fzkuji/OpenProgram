import type { ChatMsg } from "../types";
import { validMessageTimestamp } from "./message-timestamp.ts";

/** How far a loaded user turn's time may trail its optimistic row's
 *  (client and server clocks differ). */
const SAME_TURN_SKEW_MS = 15_000;

/** A user row whose send never got its ACK: still waiting, or failed. */
export function isUnsentUserRow(row: { role?: string; status?: string }): boolean {
  return row.role === "user" && (row.status === "pending" || row.status === "error");
}

/**
 * Settle optimistic user rows against a freshly loaded transcript. A row
 * the backend did accept (the load holds a user turn with its text, sent
 * no earlier than it) is dropped, the loaded turn stands in for it. A row
 * still waiting that is not the send in flight lost its ACK: it shows as
 * not sent. The send in flight keeps waiting.
 */
export function settlePendingSends(rows: ChatMsg[], inFlightId: string | undefined): ChatMsg[] {
  if (!rows.some(isUnsentUserRow)) return rows;
  const accepted = rows.filter((row) => row.role === "user" && !isUnsentUserRow(row));
  const lastAccepted = accepted[accepted.length - 1];
  const used = new Set<string>();
  const out: ChatMsg[] = [];
  for (const row of rows) {
    if (!isUnsentUserRow(row)) {
      out.push(row);
      continue;
    }
    const text = row.content.trim();
    const match = accepted.find((candidate) => {
      if (used.has(candidate.id) || candidate.content.trim() !== text) return false;
      if (validMessageTimestamp(row.timestamp) && validMessageTimestamp(candidate.timestamp)) {
        return candidate.timestamp >= row.timestamp - SAME_TURN_SKEW_MS;
      }
      return candidate === lastAccepted;
    });
    if (match) {
      used.add(match.id);
      continue;
    }
    out.push(row.status === "pending" && row.id !== inFlightId
      ? { ...row, status: "error", rawType: "error", errorReason: "not_sent" }
      : row);
  }
  return out;
}
