"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useSkills, type Skill } from "@/lib/skills-store";
import { Switch } from "@/components/ui/switch";

// --- tree node model -----------------------------------------------------

type TreeNode = {
  segment: string;          // single path segment for this node
  path: string;             // full path from root
  skill: Skill | null;      // non-null iff this node *is* a SKILL.md
  children: Map<string, TreeNode>;
};

function buildTree(skills: Skill[]): TreeNode {
  const root: TreeNode = { segment: "", path: "", skill: null, children: new Map() };
  for (const s of skills) {
    const segments = (s.path_segments && s.path_segments.length > 0)
      ? s.path_segments
      : s.name.split("/");
    let cur = root;
    let pathSoFar = "";
    segments.forEach((seg, i) => {
      pathSoFar = pathSoFar ? pathSoFar + "/" + seg : seg;
      let child = cur.children.get(seg);
      if (!child) {
        child = { segment: seg, path: pathSoFar, skill: null, children: new Map() };
        cur.children.set(seg, child);
      }
      if (i === segments.length - 1) {
        child.skill = s;
      }
      cur = child;
    });
  }
  return root;
}

function sortedChildren(node: TreeNode): TreeNode[] {
  const arr = Array.from(node.children.values());
  // Folders (no skill or with children) before leaf skills, then alpha.
  arr.sort((a, b) => {
    const aFolder = a.children.size > 0 ? 0 : 1;
    const bFolder = b.children.size > 0 ? 0 : 1;
    if (aFolder !== bFolder) return aFolder - bFolder;
    return a.segment.localeCompare(b.segment);
  });
  return arr;
}

// --- rendering -----------------------------------------------------------

function SkillLeaf({ skill, depth }: { skill: Skill; depth: number }) {
  const router = useRouter();
  const { toggleSkill } = useSkills();
  const active = false; // selection-vs-route highlighting now lives in the URL
  return (
    <div
      role="button"
      onClick={() => router.push(`/skills/${skill.name.split("/").map(encodeURIComponent).join("/")}`)}
      style={{
        paddingLeft: 8 + depth * 16,
        // Tint the active row with the same accent the border uses so
        // light mode shows a clear blue wash instead of the muddy grey
        // var(--bg-selected) gives us.
        background: active ? "rgba(56, 134, 229, 0.12)" : undefined,
      }}
      className={
        "group flex items-center gap-2 rounded-md border py-1.5 pr-3 cursor-pointer transition-colors " +
        (active
          ? "border-primary text-nav-color-hover"
          : "border-transparent hover:bg-bg-hover hover:text-nav-color-hover")
      }
      title={`${skill.description || skill.name}\n— ${skill.source}`}
    >
      <span className="text-[var(--text-tertiary)] shrink-0" aria-hidden>◦</span>
      <div className="flex-1 min-w-0">
        <span
          className={
            "truncate block " +
            (active
              ? "text-nav-color-hover font-medium"
              : "text-nav-color group-hover:text-nav-color-hover")
          }
        >
          {skill.leaf || skill.name}
        </span>
        {skill.description && (
          <p className="text-xs text-[var(--text-secondary)] truncate">{skill.description}</p>
        )}
      </div>
      <div onClick={(e) => e.stopPropagation()} className="shrink-0">
        <Switch
          checked={skill.enabled}
          onCheckedChange={(v) => toggleSkill(skill.name, v)}
        />
      </div>
    </div>
  );
}

function TreeBranch({
  node,
  depth,
  expanded,
  toggleExpanded,
  toggleBranch,
}: {
  node: TreeNode;
  depth: number;
  expanded: Set<string>;
  toggleExpanded: (path: string) => void;
  toggleBranch: (paths: string[], enabled: boolean) => void;
}) {
  const isOpen = expanded.has(node.path);
  const children = sortedChildren(node);
  const subSkills: Skill[] = [];
  const walk = (n: TreeNode) => {
    if (n.skill) subSkills.push(n.skill);
    n.children.forEach(walk);
  };
  walk(node);
  const enabledCount = subSkills.filter((s) => s.enabled).length;
  const allOn = enabledCount === subSkills.length && subSkills.length > 0;

  return (
    <div>
      <div
        role="button"
        onClick={() => toggleExpanded(node.path)}
        style={{ paddingLeft: 8 + depth * 16 }}
        className="group flex items-center gap-2 py-2 pr-3 cursor-pointer rounded border border-transparent hover:bg-bg-hover hover:text-nav-color-hover select-none"
      >
        <span className="text-[var(--text-tertiary)] w-3 text-center">
          {isOpen ? "▾" : "▸"}
        </span>
        <span className="text-nav-color group-hover:text-nav-color-hover text-sm">{node.segment}</span>
        <span className="text-[11px] text-[var(--text-tertiary)]">{enabledCount}/{subSkills.length}</span>
        <div className="ml-auto" onClick={(e) => e.stopPropagation()}>
          <Switch
            checked={allOn}
            onCheckedChange={(v) => toggleBranch(subSkills.map((s) => s.name), v)}
          />
        </div>
      </div>
      {isOpen && (
        <div className="mt-1 space-y-1">
          {children.map((c) =>
            c.skill && c.children.size === 0 ? (
              <SkillLeaf key={c.path} skill={c.skill} depth={depth + 1} />
            ) : (
              <TreeBranch
                key={c.path}
                node={c}
                depth={depth + 1}
                expanded={expanded}
                toggleExpanded={toggleExpanded}
                toggleBranch={toggleBranch}
              />
            )
          )}
        </div>
      )}
    </div>
  );
}

export function SkillsList() {
  const { skills, toggleSkill } = useSkills();
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set<string>());
  const [filter, setFilter] = useState("");

  const filtered = useMemo(() => {
    if (!filter.trim()) return skills;
    const q = filter.toLowerCase();
    return skills.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        (s.description || "").toLowerCase().includes(q)
    );
  }, [skills, filter]);

  const tree = useMemo(() => buildTree(filtered), [filtered]);

  // Auto-expand all when filtering so matches are visible.
  const effectiveExpanded = useMemo(() => {
    if (!filter.trim()) return expanded;
    const all = new Set<string>();
    const walk = (n: TreeNode) => {
      if (n.path) all.add(n.path);
      n.children.forEach(walk);
    };
    walk(tree);
    return all;
  }, [expanded, filter, tree]);

  const toggleExpanded = (path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const toggleBranch = (names: string[], enabled: boolean) => {
    for (const n of names) toggleSkill(n, enabled);
  };

  const expandAll = () => {
    const all = new Set<string>();
    const walk = (n: TreeNode) => {
      if (n.path) all.add(n.path);
      n.children.forEach(walk);
    };
    walk(tree);
    setExpanded(all);
  };
  const collapseAll = () => setExpanded(new Set());

  const rootChildren = sortedChildren(tree);

  return (
    <div>
      <div className="mb-3 flex items-center gap-1">
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Search skills..."
          className="flex-1 min-w-0 rounded border border-[var(--border)] bg-[var(--bg-secondary)] px-2 py-1 text-sm"
        />
        <button onClick={expandAll}
          title="Expand all"
          className="shrink-0 rounded px-1.5 py-1 text-[11px] text-[var(--text-secondary)] hover:bg-bg-hover hover:text-nav-color-hover">⊕</button>
        <button onClick={collapseAll}
          title="Collapse all"
          className="shrink-0 rounded px-1.5 py-1 text-[11px] text-[var(--text-secondary)] hover:bg-bg-hover hover:text-nav-color-hover">⊖</button>
      </div>
      <div className="space-y-1">
        {rootChildren.map((c) =>
          c.skill && c.children.size === 0 ? (
            <SkillLeaf key={c.path} skill={c.skill} depth={0} />
          ) : (
            <TreeBranch
              key={c.path}
              node={c}
              depth={0}
              expanded={effectiveExpanded}
              toggleExpanded={toggleExpanded}
              toggleBranch={toggleBranch}
            />
          )
        )}
      </div>
      {skills.length === 0 && (
        <div className="text-sm text-[var(--text-tertiary)]">No skills found.</div>
      )}
    </div>
  );
}
