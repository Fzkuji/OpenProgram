"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useSkills, type Skill } from "@/lib/skills-store";
import { Switch } from "@/components/ui/switch";
import { useTranslation } from "@/lib/i18n";
import {
  type AnimatedNavIconHandle,
  FolderCodeIcon,
  FolderOpenIcon,
} from "@/components/animated-icons";

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
        // Tint the active row with the page-wide accent so light mode
        // shows a clear amber wash instead of the muddy grey that
        // ``var(--bg-selected)`` collapses to. ``color-mix`` keeps it
        // theme-aware — auto-flips with ``--accent-orange``'s dark /
        // light variants without a JS branch.
        background: active
          ? "color-mix(in srgb, var(--accent-orange) 12%, transparent)"
          : undefined,
      }}
      className={
        "group flex items-center gap-2 rounded-md border py-1.5 pr-3 cursor-pointer transition-colors " +
        (active
          ? "border-primary text-nav-color-hover"
          : "border-transparent hover:bg-bg-hover hover:text-nav-color-hover")
      }
      title={`${skill.description || skill.name}\n— ${skill.source}`}
    >
      <span
        className="text-[var(--text-tertiary)] shrink-0 inline-flex w-[14px] items-center justify-center"
        aria-hidden
      >◦</span>
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
  const folderIconRef = useRef<AnimatedNavIconHandle>(null);

  return (
    <div>
      <div
        role="button"
        onClick={() => toggleExpanded(node.path)}
        onMouseEnter={() => folderIconRef.current?.startAnimation?.()}
        onMouseLeave={() => folderIconRef.current?.stopAnimation?.()}
        style={{ paddingLeft: 8 + depth * 16 }}
        className="group flex items-center gap-2 py-2 pr-3 cursor-pointer rounded border border-transparent bg-[var(--bg-secondary)]/50 hover:bg-bg-hover hover:text-nav-color-hover select-none"
      >
        {/* Two states, both real pqoqubbw icons: collapsed = `folder-code`,
            expanded = `folder-open`. Each animates on row hover via the
            shared ref (only one is mounted at a time). */}
        {isOpen ? (
          <FolderOpenIcon
            ref={folderIconRef}
            size={16}
            className="text-[var(--text-tertiary)] shrink-0"
            aria-hidden
          />
        ) : (
          <FolderCodeIcon
            ref={folderIconRef}
            size={16}
            className="text-[var(--text-tertiary)] shrink-0"
            aria-hidden
          />
        )}
        <span className="text-sm font-semibold text-nav-color group-hover:text-nav-color-hover">
          {node.segment}
        </span>
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
  const { text } = useTranslation();
  const { skills, toggleSkill } = useSkills();
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set<string>());
  const [filter, setFilter] = useState("");

  // Server-driven full-text search when ?body=true so query hits the
  // actual SKILL.md content. Local name/description filter handles the
  // default empty-query / quick-typing case without a roundtrip.
  const [searchBody, setSearchBody] = useState(false);
  const [bodyHits, setBodyHits] = useState<Set<string> | null>(null);
  useEffect(() => {
    if (!searchBody || !filter.trim()) { setBodyHits(null); return; }
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(
          `/api/skills/_search?body=true&q=${encodeURIComponent(filter)}&limit=200`,
        );
        if (!r.ok) return;
        const data: { name: string }[] = await r.json();
        if (!cancelled) setBodyHits(new Set(data.map((s) => s.name)));
      } catch { /* ignore */ }
    })();
    return () => { cancelled = true; };
  }, [filter, searchBody]);

  const filtered = useMemo(() => {
    if (!filter.trim()) return skills;
    const q = filter.toLowerCase();
    return skills.filter((s) => {
      if (
        s.name.toLowerCase().includes(q) ||
        (s.description || "").toLowerCase().includes(q)
      ) return true;
      if (searchBody && bodyHits) return bodyHits.has(s.name);
      return false;
    });
  }, [skills, filter, searchBody, bodyHits]);

  // Split optional skills off so they live in a collapsible section
  // at the bottom — mirrors hermes' optional-skills/ idea.
  const requiredSkills = useMemo(
    () => filtered.filter((s) => !s.optional),
    [filtered],
  );
  const optionalSkills = useMemo(
    () => filtered.filter((s) => s.optional),
    [filtered],
  );
  const [showOptional, setShowOptional] = useState(false);

  const tree = useMemo(() => buildTree(requiredSkills), [requiredSkills]);
  const optionalTree = useMemo(() => buildTree(optionalSkills), [optionalSkills]);

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
          placeholder={text("Search skills...", "搜索技能...")}
          className="flex-1 min-w-0 rounded-[var(--ui-button-radius)] border border-[var(--border)] bg-[var(--bg-secondary)] h-[var(--ui-button-h)] px-2 text-sm outline-none transition-colors focus:border-[color:var(--accent-blue)]"
        />
        <button
          onClick={() => setSearchBody((v) => !v)}
          title={searchBody
            ? text("Searching name + description + body", "正在搜索名称、描述和正文")
            : text("Click to also search SKILL.md body", "点击后同时搜索 SKILL.md 正文")}
          className={
            "shrink-0 rounded px-1.5 py-1 text-[11px] " +
            (searchBody
              ? "bg-bg-hover text-nav-color-hover"
              : "text-[var(--text-secondary)] hover:bg-bg-hover hover:text-nav-color-hover")
          }
        >body</button>
        <button onClick={expandAll}
          title={text("Expand all", "全部展开")}
          className="shrink-0 rounded px-1.5 py-1 text-[11px] text-[var(--text-secondary)] hover:bg-bg-hover hover:text-nav-color-hover">⊕</button>
        <button onClick={collapseAll}
          title={text("Collapse all", "全部折叠")}
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
      {optionalSkills.length > 0 && (
        <div className="mt-6 border-t border-[var(--border)] pt-3">
          <button
            onClick={() => setShowOptional((v) => !v)}
            className="flex w-full items-center gap-2 text-xs text-[var(--text-secondary)] hover:text-nav-color-hover select-none"
          >
            <span className="w-3 text-center">{showOptional ? "▾" : "▸"}</span>
            <span>{text("Optional", "可选")} ({optionalSkills.length})</span>
          </button>
          {showOptional && (
            <div className="mt-2 space-y-1">
              {sortedChildren(optionalTree).map((c) =>
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
          )}
        </div>
      )}
      {skills.length === 0 && (
        <div className="text-sm text-[var(--text-tertiary)]">{text("No skills found.", "没有找到技能。")}</div>
      )}
    </div>
  );
}
