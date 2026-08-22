"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useSkills, type Skill } from "@/lib/state/skills-store";
import { Switch } from "@/components/ui/switch";
import { SearchInput } from "@/components/ui/search-input";
import { ManageRow, managePageStyles as shared } from "@/components/ui/manage-page";
import { useTranslation } from "@/lib/i18n";
import { pushPath } from "@/lib/shallow-nav";
import {
  type AnimatedNavIconHandle,
  FileTextIcon,
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
  const { toggleSkill } = useSkills();
  const { text } = useTranslation();
  return (
    <div style={{ paddingLeft: depth * 16 }}>
      <ManageRow
        icon={<FileTextIcon size={16} />}
        name={skill.leaf || skill.name}
        description={skill.description}
        title={`${skill.description || skill.name}\n— ${skill.source}`}
        onClick={() =>
          pushPath(`/skills/${skill.name.split("/").map(encodeURIComponent).join("/")}`)
        }
        actions={
          <Switch
            checked={skill.enabled}
            onCheckedChange={(v) => toggleSkill(skill.name, v)}
            aria-label={skill.enabled
              ? text(`Disable ${skill.name}`, `禁用 ${skill.name}`)
              : text(`Enable ${skill.name}`, `启用 ${skill.name}`)}
          />
        }
      />
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
  const { text } = useTranslation();

  return (
    <div>
      <div
        onMouseEnter={() => folderIconRef.current?.startAnimation?.()}
        onMouseLeave={() => folderIconRef.current?.stopAnimation?.()}
        style={{ marginLeft: depth * 16 }}
        className={shared.groupRow}
      >
        <button
          type="button"
          className={shared.groupToggle}
          aria-expanded={isOpen}
          onClick={() => toggleExpanded(node.path)}
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
        <span className={shared.groupName}>{node.segment}</span>
        <span className={shared.rowCount}>{enabledCount}/{subSkills.length}</span>
        </button>
        <div className="ml-auto">
          <Switch
            checked={allOn}
            onCheckedChange={(v) => toggleBranch(subSkills.map((s) => s.name), v)}
            aria-label={allOn
              ? text(`Disable ${node.segment}`, `禁用 ${node.segment}`)
              : text(`Enable ${node.segment}`, `启用 ${node.segment}`)}
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

export function SkillsList({ externalFilter }: { externalFilter?: string } = {}) {
  const { text } = useTranslation();
  const { skills, toggleSkill } = useSkills();
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set<string>());
  const [filter, setFilter] = useState("");
  const filterValue = externalFilter !== undefined ? externalFilter : filter;

  // Server-driven full-text search when ?body=true so query hits the
  // actual SKILL.md content. Local name/description filter handles the
  // default empty-query / quick-typing case without a roundtrip.
  const [searchBody, setSearchBody] = useState(false);
  const [bodyHits, setBodyHits] = useState<Set<string> | null>(null);
  useEffect(() => {
    if (!searchBody || !filterValue.trim()) { setBodyHits(null); return; }
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(
          `/api/skills/_search?body=true&q=${encodeURIComponent(filterValue)}&limit=200`,
        );
        if (!r.ok) return;
        const data: { name: string }[] = await r.json();
        if (!cancelled) setBodyHits(new Set(data.map((s) => s.name)));
      } catch { /* ignore */ }
    })();
    return () => { cancelled = true; };
  }, [filterValue, searchBody]);

  const filtered = useMemo(() => {
    if (!filterValue.trim()) return skills;
    const q = filterValue.toLowerCase();
    return skills.filter((s) => {
      if (
        s.name.toLowerCase().includes(q) ||
        (s.description || "").toLowerCase().includes(q)
      ) return true;
      if (searchBody && bodyHits) return bodyHits.has(s.name);
      return false;
    });
  }, [skills, filterValue, searchBody, bodyHits]);

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
    if (!filterValue.trim()) return expanded;
    const all = new Set<string>();
    const walk = (n: TreeNode) => {
      if (n.path) all.add(n.path);
      n.children.forEach(walk);
    };
    walk(tree);
    return all;
  }, [expanded, filterValue, tree]);

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
        {externalFilter === undefined && (
        <SearchInput
          className="flex-1 min-w-0"
          value={filter}
          onChange={setFilter}
          placeholder={text("Search skills...", "搜索技能...")}
        />
        )}
        <button
          onClick={() => setSearchBody((v) => !v)}
          title={searchBody
            ? text("Searching name + description + body", "正在搜索名称、描述和正文")
            : text("Click to also search SKILL.md body", "点击后同时搜索 SKILL.md 正文")}
          className={
            "shrink-0 inline-flex items-center h-[var(--ui-button-h)] rounded-[var(--ui-button-radius)] px-2 text-[13px] leading-none " +
            (searchBody
              ? "bg-bg-hover text-nav-color-hover"
              : "text-[var(--text-secondary)] hover:bg-bg-hover hover:text-nav-color-hover")
          }
        >body</button>
        <button onClick={expandAll}
          title={text("Expand all", "全部展开")}
          className="shrink-0 inline-flex items-center justify-center h-[var(--ui-button-h)] min-w-[var(--ui-button-h)] rounded-[var(--ui-button-radius)] px-2 text-[13px] leading-none text-[var(--text-secondary)] hover:bg-bg-hover hover:text-nav-color-hover">⊕</button>
        <button onClick={collapseAll}
          title={text("Collapse all", "全部折叠")}
          className="shrink-0 inline-flex items-center justify-center h-[var(--ui-button-h)] min-w-[var(--ui-button-h)] rounded-[var(--ui-button-radius)] px-2 text-[13px] leading-none text-[var(--text-secondary)] hover:bg-bg-hover hover:text-nav-color-hover">⊖</button>
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
        <div className={shared.empty}>{text("No skills found.", "没有找到技能。")}</div>
      )}
    </div>
  );
}
