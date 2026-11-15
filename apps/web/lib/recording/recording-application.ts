/**
 * Demonstration recording is the built-in application `workflow_recording`
 * (openprogram/programs/applications/workflow_recording). The composer
 * offers it only while that application is enabled; disabled, the server's
 * recording routes answer 404 and a message naming a recording is refused.
 */
import type { BuiltinApplication } from "@/lib/net/applications";

export const RECORDING_APPLICATION = "workflow_recording";

/** Whether the listed built-in applications have workflow recording enabled. */
export function recordingEnabled(builtin: readonly BuiltinApplication[]): boolean {
  return builtin.some((application) => application.id === RECORDING_APPLICATION && application.enabled);
}
