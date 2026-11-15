"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { SearchInput } from "@/components/ui/search-input";
import { groupTools, TOOL_GROUPS, type ToolInfo } from "./tool-groups";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { jsonFetch } from "@/lib/net/fetch-client";
import { AgentButton } from "./agent-controls";
import { Button } from "@/components/ui/button";
import { policyMode, selectedProgramNames, type Agent, type Text, type ToolPolicy } from "./agent-types";
import styles from "./agents-page.module.css";
import { SimpleSelect } from "@/components/ui/select";
import { Checkbox } from "@/components/ui/checkbox";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";

type Kind = "programs" | "skills" | "mcp";
type Item = { name: string; description: string; group: string; available: boolean };
export function CapabilityPanel({ kind, draft, update, text }: { kind: Kind; draft: Agent; update: (patch: Partial<Agent>) => void; text: Text }) {
  const [open, setOpen] = useState(false);
  const opener = useRef<HTMLElement | null>(null);
  const [catalog, setCatalog] = useState<Item[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [presets, setPresets] = useState<Record<string, string[]>>({});
  // Translation changes need not restart an in-flight catalog request.
  const textRef = useRef(text); textRef.current = text;
  const noun = kind === "programs" ? "Programs" : kind === "skills" ? "Skills" : "MCP";
  // Chinese UI uses the same nouns as the Abilities hub tabs (程序 / 技能).
  const nounZh = kind === "programs" ? "程序" : kind === "skills" ? "技能" : "MCP";
  const mode = kind === "programs" ? draft.tools.mode === "automatic" ? "all" : draft.tools.mode : policyMode(draft[kind]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController(); let active = true;
    const options = { signal: controller.signal }; const tr = textRef.current;
    setLoading(true); setError(""); setCatalog([]);
    void (async () => {
      if (kind === "programs") {
        const [programs, tools, profiles] = await Promise.all([
          jsonFetch<Array<{ name: string; description?: string; category?: string }>>("/api/programs", options),
          jsonFetch<ToolInfo[]>("/api/tools", options),
          jsonFetch<{ profiles: Record<string, string[]> }>("/api/tool-profiles", options),
        ]);
        if (!active) return;
        setPresets(profiles.profiles || {});
        const labels = new Map<string, string>(TOOL_GROUPS.map(([id, en, zh]) => [id, tr(en, zh)]));
        setCatalog([
          ...groupTools(tools.filter((tool) => tool.source !== "mcp")).flatMap((group) => group.items.map((tool) => ({ name: tool.name, description: tool.description || "", group: `${tr("Tools", "工具")} · ${labels.get(group.name) || group.name}`, available: !tool.disabled }))),
          ...groupTools(tools.filter((tool) => tool.source === "mcp"), "server").flatMap((group) => group.items.map((tool) => ({ name: tool.name, description: tool.description || "", group: `${tr("Connected Services", "已连接服务")} · ${group.name}`, available: !tool.disabled }))),
          ...programs.map((program) => ({ name: program.name, description: program.description || "", group: program.category === "application" ? tr("Applications", "应用") : tr("Tools", "工具"), available: true })),
        ]);
      } else if (kind === "skills") {
        const skills = await jsonFetch<Array<{ name: string; description?: string; category?: string; enabled?: boolean }>>("/api/skills", options);
        if (active) setCatalog(skills.map((item) => ({ name: item.name, description: item.description || "", group: item.category || tr("Uncategorized", "未分类"), available: item.enabled !== false })));
      } else {
        const data = await jsonFetch<{ servers: Array<{ name: string; status?: string; connected?: boolean; enabled?: boolean }> }>("/api/mcp/servers", options);
        if (active) setCatalog(data.servers.map((item) => ({ name: item.name, description: item.status || "", group: tr("Connected Services", "已连接服务"), available: item.connected !== false && item.enabled !== false })));
      }
    })().catch((cause) => { if (active && cause.name !== "AbortError") setError(String(cause.message || cause)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [open, kind, attempt]);
  const selected = useMemo(() => {
    const names = mode === "all" ? catalog.filter((item) => item.available).map((item) => item.name)
      : mode === "none" ? [] : kind === "programs" ? selectedProgramNames(draft.tools, presets) : draft[kind].allowed;
    const disabled = kind === "programs" ? draft.tools.disabled || [] : draft[kind].disabled;
    return [...new Set(names)].filter((name) => !disabled.includes(name));
  }, [mode, catalog, draft, kind, presets]);
  const groups = useMemo(() => {
    const known = new Set(catalog.map((item) => item.name));
    const missing = selected.filter((name) => !known.has(name)).map((name) => ({ name, description: text("Stored reference is not in the current catalog", "已保存的引用不在当前目录中"), group: text("Missing or pattern references", "缺失引用或匹配规则"), available: false }));
    const map = new Map<string, Item[]>();
    for (const item of [...catalog, ...missing]) {
      if (search.trim() && !`${item.name} ${item.description}`.toLowerCase().includes(search.toLowerCase().trim())) continue;
      map.set(item.group, [...(map.get(item.group) || []), item]);
    }
    return [...map];
  }, [catalog, selected, search, text]);
  function browse() { opener.current = document.activeElement as HTMLElement; setSearch(""); setOpen(true); }
  function updateTools(tools: ToolPolicy) {
    update({ tools: { ...(draft.tools.web_search !== undefined ? { web_search: draft.tools.web_search } : {}), ...tools } });
  }
  function setMode(next: "all" | "selected" | "none") {
    if (next === "selected") { browse(); return; }
    if (kind === "programs") updateTools({ mode: next === "all" ? "automatic" : "none" });
    else if (kind === "skills") update({ skills: { allowed: [], disabled: next === "none" ? ["*"] : [], categories: [] } });
    else update({ mcp: { allowed: [], disabled: next === "none" ? ["*"] : [], required: next === "none" ? [] : draft.mcp.required } });
  }
  function toggle(name: string) {
    const allowed = selected.includes(name) ? selected.filter((entry) => entry !== name) : [...selected, name];
    if (kind === "programs") updateTools(allowed.length ? { mode: "selected", allowed, ...(draft.tools.disabled?.length ? { disabled: draft.tools.disabled.filter((entry) => entry !== "*") } : {}) } : { mode: "none" });
    else if (kind === "skills") update({ skills: { allowed, disabled: allowed.length ? draft.skills.disabled.filter((entry) => entry !== "*") : ["*"], categories: allowed.length ? draft.skills.categories || [] : [] } });
    else update({ mcp: { allowed, disabled: allowed.length ? draft.mcp.disabled.filter((entry) => entry !== "*") : ["*"], required: allowed.length ? draft.mcp.required.filter((entry) => entry !== name || allowed.includes(name)) : [] } });
  }
  function required(name: string) {
    const next = draft.mcp.required.includes(name) ? draft.mcp.required.filter((entry) => entry !== name) : [...draft.mcp.required, name];
    update({ mcp: { ...draft.mcp, required: next } });
  }
  const restrictions = kind === "programs" ? draft.tools.disabled || [] : [...draft[kind].disabled, ...(kind === "skills" ? draft.skills.categories || [] : [])];
  return <>
    <div className={`${styles.panelIntro} mb-[18px]`}><h3>{text(noun, nounZh)}</h3><p>{kind === "programs" ? text("Choose the Tools and Applications this Agent may call.", "选择此 Agent 可调用的工具与应用。") : kind === "skills" ? text("Choose which Skills this Agent may discover.", "选择此 Agent 可发现的技能。") : text("Choose allowed services and mark dependencies that must be available.", "选择允许的服务，并标记运行时必须可用的依赖。")}</p></div>
    <RadioGroup className={styles.modeGrid} value={mode} onValueChange={(value) => setMode(value as typeof mode)}>{(["all", "selected", "none"] as const).map((value) => <label key={value} className={mode === value ? styles.modeActive : styles.modeCard}><RadioGroupItem value={value} /><span><strong>{value === "all" ? text(`All ${noun}`, `全部${nounZh}`) : value === "selected" ? text("Selected scope", "指定范围") : text(`No ${noun}`, `不使用${nounZh}`)}</strong><small>{value === "all" ? text("Follow globally available entries.", "使用全局可用项。") : value === "selected" ? text("Choose explicit entries from the catalog.", "从目录选择明确的条目。") : text("Disable this capability.", "关闭此能力。")}</small></span></label>)}</RadioGroup>
    {mode !== "none" && restrictions.length ? <p className={styles.note}>{text("Stored name/category restrictions remain in effect:", "已保存的名称或分类限制继续生效：")} {restrictions.join(", ")}</p> : null}
    <section className={styles.scopeCard}><div><h4>{mode === "all" ? text(`All ${noun}`, `全部${nounZh}`) : mode === "none" ? text(`${noun} disabled`, `${nounZh}已禁用`) : kind === "programs" && draft.tools.preset ? text(`Access preset: ${draft.tools.preset}`, `访问预设：${draft.tools.preset}`) : text("Selected scope", "指定范围")}</h4><p>{kind === "mcp" ? text(`${draft.mcp.required.length} required services. Missing dependencies prevent startup.`, `${draft.mcp.required.length} 个必需服务，缺失时阻止启动。`) : text("Missing references stay until you remove and save them.", "缺失引用会保留，直到删除并保存。")}</p></div><AgentButton variant="elevated" onClick={browse}>{text(`Browse ${noun}…`, `浏览${nounZh}…`)}</AgentButton></section>
    <Dialog open={open} onOpenChange={setOpen}><DialogContent className={styles.catalogDialog} onCloseAutoFocus={(event) => { event.preventDefault(); if (opener.current?.isConnected) opener.current.focus(); }}><DialogHeader><DialogTitle>{text(`Manage ${noun}`, `管理${nounZh}`)}</DialogTitle><DialogDescription>{text("Changes remain in this Agent's draft. Removing every selection disables the capability.", "修改保留在此 Agent 的草稿中。取消全部选择会关闭该能力。")}</DialogDescription></DialogHeader>
      <SearchInput aria-label={text(`Search ${noun}`, `搜索${nounZh}`)} value={search} onChange={setSearch} placeholder={text(`Search ${noun}…`, `搜索${nounZh}…`)} />
      {kind === "programs" && Object.keys(presets).length ? <label className={styles.presetField}>{text("Access preset", "访问预设")}<SimpleSelect label={text("Access preset", "访问预设")} className="w-full" value={draft.tools.preset || ""} onValueChange={(value) => { if (value) updateTools({ mode: "selected", preset: value }); }} options={[{ value: "", label: text("Custom selection", "自定义选择") }, ...Object.keys(presets).map((name) => ({ value: name, label: name }))]} /></label> : null}
      <div className={styles.catalogBody}>{loading ? <p className={styles.catalogState} role="status">{text("Loading catalog…", "正在加载目录…")}</p> : error ? <div className={styles.catalogState} role="alert">{error}<AgentButton onClick={() => setAttempt((value) => value + 1)}>{text("Retry", "重试")}</AgentButton></div> : !groups.length ? <p className={styles.catalogState}>{text("No matching entries", "没有匹配项")}</p> : groups.map(([group, items]) => <section className={styles.catalogGroup} key={group}><h4>{group}<span>{items.length}</span></h4>{items.map((item) => <div className={styles.catalogRow} key={`${group}:${item.name}`}><label><Checkbox checked={selected.includes(item.name)} onCheckedChange={() => toggle(item.name)} /><span><strong>{item.name}</strong><small>{item.description || text("No description", "暂无描述")}</small></span></label><span className={item.available ? styles.available : styles.missing}>{item.available ? text("Available", "可用") : text("Missing", "缺失")}</span>{kind === "mcp" && selected.includes(item.name) ? <Button type="button" variant="elevated" size="xs" className="max-[680px]:col-span-full max-[680px]:justify-self-start" aria-pressed={draft.mcp.required.includes(item.name)} onClick={() => required(item.name)}>{draft.mcp.required.includes(item.name) ? text("Required", "必需") : text("Optional", "可选")}</Button> : null}</div>)}</section>)}</div>
      <DialogFooter><AgentButton onClick={() => setOpen(false)}>{text("Done", "完成")}</AgentButton></DialogFooter>
    </DialogContent></Dialog>
  </>;
}
