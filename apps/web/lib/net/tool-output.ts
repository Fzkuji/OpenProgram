import { wsRequest } from "@/lib/net/ws-request";

export interface FullToolOutputResponse {
  session_id?: string;
  message_id?: string;
  node_id?: string;
  request_id?: string;
  result?: unknown;
  error?: string;
}

export function fetchFullToolOutput(
  sessionId: string,
  messageId: string,
  nodeId: string,
): Promise<FullToolOutputResponse | null> {
  const requestId = crypto.randomUUID();
  return wsRequest<FullToolOutputResponse>(
    "get_full_tool_output",
    {
      session_id: sessionId,
      message_id: messageId,
      node_id: nodeId,
      request_id: requestId,
    },
    "full_tool_output",
    (data) => data.request_id === requestId
      && data.session_id === sessionId
      && data.message_id === messageId
      && data.node_id === nodeId,
  );
}
