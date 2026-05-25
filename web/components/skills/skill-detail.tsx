"use client";

import { useSkills } from "@/lib/skills-store";
import { Markdown } from "@/lib/markdown";
import { Button } from "@/components/ui/button";

export function SkillDetail() {
  const { detail, deleteSkill } = useSkills();
  if (!detail) {
    return (
      <div className="p-6 text-sm text-[var(--text-tertiary)]">
        Select a skill to view its SKILL.md and resources.
      </div>
    );
  }
  const canDelete = ["project", "user", "remote-cache"].includes(detail.source);
  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-start justify-between gap-3 border-b border-[var(--border)] px-6 py-4">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-[var(--text-bright)] truncate">
            {detail.name}
          </h2>
          {detail.description && (
            <p className="mt-1 text-sm text-[var(--text-secondary)]">{detail.description}</p>
          )}
          <div className="mt-2 text-xs text-[var(--text-tertiary)] break-all">{detail.path}</div>
        </div>
        {canDelete && (
          <Button
            variant="destructive"
            size="sm"
            onClick={() => {
              if (confirm(`Delete skill "${detail.name}"?`)) deleteSkill(detail.name);
            }}
          >
            Delete
          </Button>
        )}
      </div>
      <div className="flex-1 overflow-auto px-6 py-4">
        {detail.resources.length > 0 && (
          <div className="mb-4">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--text-tertiary)] mb-2">
              Resources
            </h3>
            <ul className="space-y-1 text-xs font-mono text-[var(--text-secondary)]">
              {detail.resources.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          </div>
        )}
        <div className="prose prose-invert max-w-none text-sm">
          <Markdown source={detail.body} />
        </div>
      </div>
    </div>
  );
}
