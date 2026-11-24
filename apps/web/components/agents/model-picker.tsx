"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { BrainIcon, CheckIcon, ChevronDownIcon } from "@/components/animated-icons";
import { SearchInput } from "@/components/ui/search-input";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { AgentButton } from "./agent-controls";
import { lastKept } from "@/lib/abilities/kept-read";
import { MODELS_KEY, watchAgentModels, type CatalogModel, type ModelList } from "@/lib/agents/agent-models";
import type { Agent, Text } from "./agent-types";
import styles from "./agents-page.module.css";
import { SimpleSelect } from "@/components/ui/select";
import { SettingsRow } from "@/components/settings/settings-ui";

export type { CatalogModel };

/** The model catalog: the kept one at once, read again behind it; a failed
 *  read keeps what is shown and is tried again quietly. The list holds the
 *  providers that answered; one that did not is asked again quietly.
 *  `loading` is true only until the first list arrives. */
export function useAgentModels() {
  const [list, setList] = useState<ModelList | null>(() => lastKept<ModelList>(MODELS_KEY) ?? null);
  useEffect(() => {
    const controller = new AbortController();
    watchAgentModels(setList, controller.signal);
    return () => controller.abort();
  }, []);
  return { models: list?.models ?? [], inherited: list?.inherited ?? null, loading: list === null };
}
export type ModelCatalog = ReturnType<typeof useAgentModels>;
export type ModelSelection = Pick<Agent, "model" | "thinking_effort">;
export function selectedModel(agent: ModelSelection, catalog: ModelCatalog) {
  const ref = !agent.model.id && !agent.model.provider ? catalog.inherited : agent.model;
  return catalog.models.find((model) => model.id === ref?.id && model.provider === ref?.provider);
}
/** `form`: label above each control (dialogs, the quick settings panel).
 *  `rows`: Settings rows, label left and control right (the full editor). */
export function ModelPicker({ draft, update, catalog, text, idPrefix = "agent", announceEffortError = true, layout = "form" }: {
  draft: ModelSelection; update: (patch: Partial<ModelSelection>) => void; catalog: ModelCatalog; text: Text; idPrefix?: string; announceEffortError?: boolean; layout?: "form" | "rows";
}) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const [search, setSearch] = useState("");
  const current = selectedModel(draft, catalog);
  const inherited = !draft.model.id && !draft.model.provider;
  const effortSupported = !draft.thinking_effort || Boolean(current?.thinking_levels?.includes(draft.thinking_effort));
  const filtered = useMemo(() => catalog.models.filter((model) =>
    `${model.name} ${model.provider} ${model.id}`.toLowerCase().includes(search.trim().toLowerCase())), [catalog.models, search]);
  function select(model: Agent["model"]) { update({ model }); setOpen(false); setSearch(""); }
  const modelName = inherited ? text("Last used model", "上次使用的模型") : current?.name || draft.model.id || draft.model.provider;
  const modelDetail = inherited ? catalog.inherited ? `${catalog.inherited.provider} / ${catalog.inherited.id}` : text("No model used yet", "还没有使用过模型") : `${draft.model.provider}${!current && !catalog.loading ? text(" · Unavailable", " · 不可用") : ""}`;
  const effort = <SimpleSelect label={text("Thinking effort", "思考强度")} id={`${idPrefix}-effort`} className={layout === "rows" ? undefined : "w-full max-w-none"} value={draft.thinking_effort} onValueChange={(value) => update({ thinking_effort: value })} aria-describedby={!effortSupported ? `${idPrefix}-effort-warning` : undefined} options={[
    { value: "", label: `${text("Inherit model default", "继承模型默认值")}${current?.default_thinking_level ? ` (${current.default_thinking_level})` : ""}` },
    ...(!effortSupported && draft.thinking_effort ? [{ value: draft.thinking_effort, label: `${draft.thinking_effort} — ${text("not supported by this model", "当前模型不支持")}` }] : []),
    ...(current?.thinking_levels || []).map((level) => ({ value: level, label: level })),
  ]} />;
  return <>
    {layout === "rows" ? <>
      <SettingsRow label={text("Model", "模型")} description={modelDetail} htmlFor={`${idPrefix}-model-picker`}>
        <AgentButton type="button" ref={trigger} id={`${idPrefix}-model-picker`} variant="elevated" icon={BrainIcon} onClick={() => setOpen(true)} aria-haspopup="dialog">{modelName}<ChevronDownIcon size={16} aria-hidden className="text-text-muted" /></AgentButton>
      </SettingsRow>
      <SettingsRow label={text("Thinking effort", "思考强度")} description={text("How long the model may think before it answers.", "模型回答前可以思考多久。")} htmlFor={`${idPrefix}-effort`}>{effort}</SettingsRow>
    </> : <div className={styles.modelSelection}><div className={styles.modelChoice}><label className={styles.fieldLabel} htmlFor={`${idPrefix}-model-picker`}>{text("Model", "模型")}</label>
    <AgentButton type="button" ref={trigger} id={`${idPrefix}-model-picker`} className={styles.modelTrigger} variant="elevated" icon={BrainIcon} onClick={() => setOpen(true)} aria-haspopup="dialog">
      <span><strong>{modelName}</strong><small>{modelDetail}</small></span><ChevronDownIcon size={16} aria-hidden />
    </AgentButton></div>
    <label className={styles.dialogField} htmlFor={`${idPrefix}-effort`}>{text("Thinking effort", "思考强度")}{effort}</label></div>}
    {!catalog.loading && !current ?<p className={styles.inlineError}>{text("This model is unavailable. Keep the reference or choose an enabled model before starting a conversation.", "此模型不可用。可保留引用，或在发起对话前选择已启用模型。")}</p> : null}
    {!effortSupported && !catalog.loading ? <p id={`${idPrefix}-effort-warning`} className={announceEffortError ? styles.inlineError : styles.note} role={announceEffortError ? "alert" : undefined}>{text("Choose a supported effort or inherit the model default. Your previous choice has been kept.", "请选择支持的思考强度或继承模型默认值；此前选择已保留。")}</p> : null}
    <Dialog open={open} onOpenChange={(value) => { setOpen(value); if (!value) setSearch(""); }}>
      <DialogContent className={styles.modelDialog} onCloseAutoFocus={(event) => { event.preventDefault(); trigger.current?.focus(); }}><DialogHeader><DialogTitle>{text("Choose model", "选择模型")}</DialogTitle><DialogDescription>{text("This changes only this Agent's draft.", "仅修改此 Agent 的草稿。")}</DialogDescription></DialogHeader>
        <SearchInput autoFocus aria-label={text("Search models", "搜索模型")} value={search} onChange={setSearch} placeholder={text("Search models…", "搜索模型…")} />
        <div className={`${styles.modelList} max-h-[min(460px,60vh)] overflow-auto`}>
          <AgentButton type="button" variant="ghost" icon={BrainIcon} className={styles.modelOption} onClick={() => select({ provider: "", id: "" })}><span><strong>{text("Last used model", "上次使用的模型")}</strong><small>{catalog.inherited ? `${catalog.inherited.provider} / ${catalog.inherited.id}` : text("No model used yet", "还没有使用过模型")}</small></span>{inherited ? <CheckIcon size={16} aria-hidden /> : null}</AgentButton>
          {catalog.loading ? null : filtered.length === 0 ? <p>{text("No matching enabled models", "没有匹配的已启用模型")}</p> : filtered.map((model) => <AgentButton type="button" variant="ghost" icon={BrainIcon} key={`${model.provider}:${model.id}`} className={styles.modelOption} onClick={() => select({ provider: model.provider, id: model.id })}><span><strong>{model.name || model.id}</strong><small>{model.provider} / {model.id}</small></span>{draft.model.provider === model.provider && draft.model.id === model.id ? <CheckIcon size={16} aria-hidden /> : null}</AgentButton>)}
          {!inherited && !current ? <p>{text("Current unavailable reference", "当前不可用引用")}: {draft.model.provider} / {draft.model.id}</p> : null}
        </div>
      </DialogContent>
    </Dialog>
  </>;
}
