import { useEffect, useState } from "react";
import type { ProjectGroupSource } from "@/lib/project-groups";

/** Keep explicit row selection until the displayed chat/project context changes. */
export function useProjectSelection(projects: readonly ProjectGroupSource[], currentId: string | null, chatKey: string | null, pendingProjectId?: string) {
  const contextualId = pendingProjectId
    ?? projects.find(project => currentId && project.session_ids?.includes(currentId))?.id
    ?? projects.find(project => project.is_default)?.id;
  const context = `${chatKey ?? currentId ?? ""}:${contextualId ?? ""}`;
  const [selection, setSelection] = useState<{ context: string; id: string } | null>(null);
  useEffect(() => setSelection(null), [context]);
  return {
    selectedProjectId: selection?.context === context ? selection.id : currentId ? undefined : contextualId,
    selectProject: (id: string) => setSelection({ context, id }),
    clearProjectSelection: () => setSelection(null),
  };
}
