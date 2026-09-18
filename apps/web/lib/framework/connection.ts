import { desktopBridge } from "@/lib/desktop/bridge-api";
import { executeInterface } from "./commands";

export function interfaceWindowId(): string {
  const native = desktopBridge()?.windowId;
  if (native) return native;
  const key = "openprogram.interface.window";
  let value = sessionStorage.getItem(key);
  if (!value) { value = crypto.randomUUID(); sessionStorage.setItem(key, value); }
  return value;
}

export async function receiveInterface(socket: WebSocket, data: Record<string, unknown>): Promise<void> {
  if (data.window_id !== interfaceWindowId() || typeof data.request_id !== "string") return;
  let result: unknown;
  try {
    if (typeof data.operation !== "string" || !Array.isArray(data.arguments)) throw new Error("invalid_interface_command");
    const value = await executeInterface(data.operation, data.arguments, data.page as import("./commands").PageAuthority | undefined);
    result = { ok: true, value: value ?? null };
  } catch (error) { result = { ok: false, error: error instanceof Error ? error.message : String(error) }; }
  if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({
    action: "framework_interface_result", request_id: data.request_id, result,
  }));
}
