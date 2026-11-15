/**
 * Where a project's folder stands, said the same way everywhere.
 *
 * The project menu, the Projects page and the file tree all show a
 * project whose folder moved or changed. They share one reading of the
 * server's location state, one message per case, and the two ways to
 * repair it: point the project at its new folder, or confirm that the
 * folder now at its recorded place is the project's own.
 */
import { wsRequest } from "@/lib/net/ws/ws-request";

export interface ProjectLocation {
  id: string;
  path?: string;
  location_state?: string;
  location_revision?: number;
  migration_error?: string;
  path_missing?: boolean;
  path_replaced?: boolean;
}

export type LocationProblem =
  | "missing"
  | "replaced"
  | "unconfirmed"
  | "migrating"
  | "unmigrated"
  | "error";

export function locationProblem(project: ProjectLocation | null | undefined): LocationProblem | null {
  if (!project) return null;
  const state = project.location_state ?? "";
  if (project.path_replaced || state === "replaced") return "replaced";
  if (state === "migrating") return "migrating";
  if (state === "pending") {
    return project.migration_error === "directory identity unavailable" ? "unconfirmed" : "unmigrated";
  }
  if (state === "error") return "error";
  if (project.path_missing || state === "missing") return "missing";
  return null;
}

/** A folder still sits at the recorded place and one click can claim it. */
export function canConfirmInPlace(problem: LocationProblem | null): boolean {
  return problem === "replaced" || problem === "unconfirmed";
}

export function locationMessage(
  problem: LocationProblem,
  project: ProjectLocation,
  text: (en: string, zh: string) => string,
): string {
  switch (problem) {
    case "replaced":
      return text(
        "The folder at this location is not the one this project was registered with.",
        "这个位置上的文件夹和项目登记的不是同一个。",
      );
    case "unconfirmed":
      return text(
        "This project was registered before folders carried an identity. Confirm the folder once.",
        "这个项目登记得较早，需要确认一次文件夹。",
      );
    case "migrating":
      return text("Migrating conversations. Tasks start after completion.", "正在迁移对话。完成前不能启动任务。");
    case "unmigrated":
      return text(
        "This legacy conversation has not migrated. Reconnect the original drive to finish.",
        "这条旧对话尚未迁移。请接回原来的磁盘后再完成。",
      );
    case "error":
      return text(project.migration_error || "Conversation migration failed.", project.migration_error || "对话迁移失败。");
    case "missing":
      return text(
        "The folder has moved or been deleted. Point the project at its new location.",
        "文件夹已被移动或删除。请把项目指向它的新位置。",
      );
  }
}

export interface RelocateReply {
  ok: boolean;
  project_id?: string;
  error?: string | null;
}

/** Point ``project`` at ``path``; the server checks it before taking it. */
export async function relocateProject(
  project: ProjectLocation,
  path: string,
  sessionId?: string | null,
): Promise<RelocateReply> {
  const reply = await wsRequest<RelocateReply>(
    "relocate_project",
    {
      ...(sessionId ? { session_id: sessionId } : {}),
      project_id: project.id,
      path,
      expected_path: project.path,
      expected_revision: project.location_revision,
      replace_identity: true,
    },
    "project_relocated",
  );
  return reply ?? { ok: false, error: "no reply" };
}

/** Claim the folder now at the project's recorded path as its own. */
export function confirmProjectFolder(project: ProjectLocation, sessionId?: string | null): Promise<RelocateReply> {
  return relocateProject(project, project.path ?? "", sessionId);
}

/** Tell every view that shows the project to read it again. */
export function notifyProjectLocationChanged(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event("project-changed"));
}
