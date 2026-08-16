"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { BotIcon, BoxesIcon, WrenchIcon } from "@/components/animated-icons";
import { Button } from "@/components/ui/button";
import { useTranslation } from "@/lib/i18n";

import styles from "./agents-page.module.css";

type ToolPolicy = {
  mode?: "automatic" | "selected" | "none";
  preset?: string;
  allowed?: string[];
  disabled?: string[];
};
type Agent = { id: string; name?: string; default?: boolean; tools?: ToolPolicy };
type Program = { name: string; description?: string; category?: string };
type Tool = { name: string; description?: string; disabled?: boolean };
type Mode = "automatic" | "selected" | "none";

function initialMode(policy?: ToolPolicy): Mode {
  if (policy?.mode) return policy.mode;
  return policy?.allowed?.length || policy?.preset ? "selected" : "automatic";
}

export function AgentsPage() {
  const { text } = useTranslation();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [programs, setPrograms] = useState<Program[]>([]);
  const [tools, setTools] = useState<Tool[]>([]);
  const [presets, setPresets] = useState<Record<string, string[]>>({});
  const [mode, setMode] = useState<Mode>("automatic");
  const [preset, setPreset] = useState("__custom__");
  const [allowed, setAllowed] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const selectedAgent = agents.find((agent) => agent.id === selectedId) ?? agents[0];

  const applyAgent = useCallback((agent: Agent | undefined) => {
    if (!agent) return;
    setMode(initialMode(agent.tools));
    setPreset(agent.tools?.preset || "__custom__");
    setAllowed(agent.tools?.allowed || []);
    setSaved(false);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([
      fetch("/api/agents", { signal: controller.signal }).then((response) => response.json()),
      fetch("/api/programs", { signal: controller.signal }).then((response) => response.json()),
      fetch("/api/tools", { signal: controller.signal }).then((response) => response.json()),
      fetch("/api/tool-profiles", { signal: controller.signal }).then((response) => response.json()),
    ]).then(([agentData, programData, toolData, presetData]) => {
      const nextAgents = Array.isArray(agentData?.agents) ? agentData.agents : [];
      setAgents(nextAgents);
      setPrograms(Array.isArray(programData) ? programData : []);
      setTools(Array.isArray(toolData) ? toolData : []);
      setPresets(presetData?.profiles || {});
      const first = nextAgents.find((agent: Agent) => agent.default) || nextAgents[0];
      if (first) {
        setSelectedId(first.id);
        applyAgent(first);
      }
    }).catch(() => undefined);
    return () => controller.abort();
  }, [applyAgent]);

  const selectedNames = useMemo(() => {
    if (mode === "automatic") {
      return new Set([
        ...tools.filter((tool) => !tool.disabled).map((tool) => tool.name),
        ...programs.map((program) => program.name),
      ]);
    }
    if (mode === "none") return new Set<string>();
    return new Set(preset === "__custom__" ? allowed : presets[preset] || []);
  }, [allowed, mode, preset, presets, programs, tools]);

  const groups = [
    {
      label: text("Functions", "函数"),
      icon: <WrenchIcon size={17} />,
      rows: tools,
    },
    {
      label: text("Agentic Functions", "Agentic 函数"),
      icon: <BotIcon size={17} />,
      rows: programs.filter((program) => program.category !== "app"),
    },
    {
      label: text("Applications", "应用"),
      icon: <BoxesIcon size={17} />,
      rows: programs.filter((program) => program.category === "app"),
    },
  ];

  function selectAgent(agent: Agent) {
    setSelectedId(agent.id);
    applyAgent(agent);
  }

  function toggleName(name: string) {
    setPreset("__custom__");
    setAllowed((names) => names.includes(name)
      ? names.filter((item) => item !== name)
      : [...names, name]);
    setSaved(false);
  }

  async function save() {
    if (!selectedAgent) return;
    setSaving(true);
    const toolsPolicy = mode === "automatic"
      ? { mode: "automatic" as const }
      : mode === "none"
        ? { mode: "none" as const }
        : preset === "__custom__"
          ? { mode: "selected" as const, allowed }
          : { mode: "selected" as const, preset };
    const response = await fetch(`/api/agents/${encodeURIComponent(selectedAgent.id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tools: toolsPolicy }),
    });
    if (response.ok) {
      const payload = await response.json();
      setAgents((rows) => rows.map((agent) => agent.id === selectedAgent.id ? payload.agent : agent));
      setSaved(true);
    }
    setSaving(false);
  }

  return (
    <div className="main">
      <div className={styles.page}>
        <header className={styles.header}>
          <div>
            <h1>{text("Agents", "Agents")}</h1>
            <p>{text("Configure which Programs each Agent may discover and call.", "配置每个 Agent 可以发现和调用哪些 Programs。")}</p>
          </div>
          <Button size="sm" onClick={save} disabled={!selectedAgent || saving}>
            {saving ? text("Saving…", "保存中…") : saved ? text("Saved", "已保存") : text("Save", "保存")}
          </Button>
        </header>

        <div className={styles.layout}>
          <aside className={styles.agentList} aria-label={text("Agent list", "Agent 列表")}>
            {agents.map((agent) => (
              <button
                key={agent.id}
                className={agent.id === selectedAgent?.id ? styles.agentActive : styles.agentRow}
                onClick={() => selectAgent(agent)}
              >
                <span className={styles.avatar}><BotIcon size={18} /></span>
                <span><strong>{agent.name || agent.id}</strong><small>{agent.id}</small></span>
                {agent.default ? <em>{text("Default", "默认")}</em> : null}
              </button>
            ))}
          </aside>

          <main className={styles.panel}>
            <div className={styles.tab}>{text("Tools & Programs", "工具与 Programs")}</div>
            <section className={styles.modeGrid}>
              {([
                ["automatic", text("Automatic discovery", "自动发现"), text("All globally available Programs; schemas load only when needed.", "允许全部全局可用 Programs，schema 仅在需要时加载。")],
                ["selected", text("Selected scope", "指定范围"), text("Use an Access preset or an explicit selection.", "使用 Access preset 或明确选择。")],
                ["none", text("No Programs", "不使用 Programs"), text("The Agent runs without callable Programs.", "Agent 不获得可调用 Programs。")],
              ] as const).map(([value, title, description]) => (
                <label key={value} className={mode === value ? styles.modeActive : styles.modeCard}>
                  <input type="radio" name="tool-mode" checked={mode === value} onChange={() => { setMode(value); setSaved(false); }} />
                  <span><strong>{title}</strong><small>{description}</small></span>
                </label>
              ))}
            </section>

            <section className={styles.presetRow}>
              <label htmlFor="access-preset">{text("Access preset", "Access preset")}</label>
              <select
                id="access-preset"
                value={preset}
                disabled={mode !== "selected"}
                onChange={(event) => { setPreset(event.target.value); setSaved(false); }}
              >
                <option value="__custom__">{text("Custom selection", "自定义选择")}</option>
                {Object.keys(presets).sort().map((name) => <option key={name} value={name}>{name}</option>)}
              </select>
              <span>{selectedNames.size} {text("Programs selected", "个 Programs 已选择")}</span>
            </section>

            <div className={styles.catalog}>
              {groups.map((group) => (
                <section key={group.label} className={styles.group}>
                  <h2>{group.icon}{group.label}<span>{group.rows.length}</span></h2>
                  {group.rows.map((row) => (
                    <label key={row.name} className={styles.programRow}>
                      <input
                        type="checkbox"
                        checked={selectedNames.has(row.name)}
                        disabled={mode !== "selected" || preset !== "__custom__"}
                        onChange={() => toggleName(row.name)}
                      />
                      <span><strong>{row.name}</strong><small>{row.description || text("No description", "暂无描述")}</small></span>
                    </label>
                  ))}
                </section>
              ))}
            </div>
          </main>
        </div>
      </div>
    </div>
  );
}
