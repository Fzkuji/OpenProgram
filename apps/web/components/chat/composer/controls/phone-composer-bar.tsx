"use client";

/**
 * The composer's controls on a touch phone (components/phone/): one row in
 * the input box, `+` to attach, one button naming the model, effort and
 * permission, and the mic and send that the box already has. The button
 * opens every setting of the turn in one Framework7 popup: a list a row
 * each, a row with choices opening them as a page of the popup (its smart
 * select), switches for the tools; the list's dock opens the same popup
 * for the next new chat (PhoneNewChatSettings). Paseo folds its
 * composer's controls the same way on a phone; the desktop keeps its two
 * rows of chips (controls-cluster.tsx).
 */

import { createContext, memo, useContext, useRef, useState } from "react";
import type { SmartSelect } from "framework7/types";
import { ChevronDown, Plus } from "lucide-react";
import {
  Block,
  BlockTitle,
  Button,
  Link,
  List,
  ListItem,
  Navbar,
  Page,
  Popup,
  Segmented,
  Toggle,
  View,
} from "framework7-react";

import { useChatModel } from "@/components/chat/top-bar";
import { ModelMark } from "@/components/chat/top-bar/chips/model-mark";
import { useModelPicker } from "@/components/chat/top-bar/menus/agent-selector";
import { useChatAgent } from "@/components/chat/top-bar/menus/agent-config-menu";
import { harnessNote, useHarness, useHarnessAgents, useHarnessPick } from "@/components/chat/top-bar/menus/harness-menu";
import { PhonePortal } from "@/components/phone/phone-portal";
import { formatEffortLabel } from "@/lib/effort/effort-color";
import { useTranslation } from "@/lib/i18n";
import { haptic } from "@/lib/mobile/bridge";
import { navigate } from "@/lib/nav/navigate";
import { SolarIcon } from "@/components/solar-icons";
import { OPENPROGRAM_HARNESS, harnessProvider, isHarnessProvider, listedModel, unavailable } from "@/lib/models/harnesses";
import { useScopedSessionId, useSessionScope } from "@/lib/session-store/session-scope";

import { useThinkingEffort } from "./effort/use-thinking-effort";
import { useBoundSetComposerSettings } from "../state/use-composer-settings";
import { useSessionAgentSettings } from "../state/use-session-agent-settings";
import { useModelAvailability } from "./toggles/use-model-availability";
import { usePermissionMode } from "./toggles/use-permission-mode";
import { useSandboxToggle } from "./toggles/use-sandbox-toggle";
import { useToolsToggles } from "./toggles/use-tools-toggles";
import { useUnattendedMode } from "./toggles/use-unattended-mode";
import type { ControlsClusterProps } from "./controls-cluster";

type Props = Pick<ControlsClusterProps,
  | "onPickImages"
  | "webSearchEnabled" | "toggleWebSearch"
  | "fastEnabled" | "fastSupported" | "toggleFast"
  | "unattended" | "toggleUnattended"
  | "sandboxEnabled" | "sandboxAvailable" | "toggleSandbox"
  | "chatAgent" | "chatModel" | "noEnabledModels"
  | "thinking" | "thinkingOptions" | "setThinking"> & {
  /** False while a saved chat is still being read: its settings are not
   *  known yet, so the chips show nothing rather than a new chat's. */
  settled: boolean;
};

export const PhoneComposerBar = memo(function PhoneComposerBar(props: Props) {
  const { text } = useTranslation();
  const [open, setOpen] = useState(false);
  const model = useChatModel(props.chatAgent.provider, props.chatAgent.model, open);
  const permission = usePermissionMode();
  const permissionLabel = permission.options.find((o) => o.value === permission.mode)?.label ?? permission.mode;
  const showEffort = !!props.chatModel && !props.noEnabledModels && props.thinkingOptions.length > 0;
  const modelText = [
    model.label || text("Choose a model", "选择模型"),
    showEffort && props.thinking ? formatEffortLabel(props.thinking) : null,
  ].filter(Boolean).join(" · ");
  const summary = `${modelText} · ${permissionLabel}`;

  return (
    // Framework7's parts again, inside the app's own composer.
    <div className="phone-composer-bar" data-f7 data-settling={props.settled && model.ready ? undefined : ""}>
      <Link iconOnly className="phone-composer-plus" aria-label={text("Add photos or files", "添加照片或文件")} onClick={props.onPickImages}>
        <Plus size={24} aria-hidden="true" />
      </Link>
      <Button
        tonal
        round
        small
        className="phone-composer-settings"
        disabled={!!props.chatAgent.locked}
        aria-label={text(`Settings: ${summary}`, `设置：${summary}`)}
        onClick={() => setOpen(true)}
      >
        <span className="phone-composer-mark" data-not-f7><ModelMark provider={model.markProvider} /></span>
        <span className="phone-composer-settings-text">{modelText}</span>
        {/* The permission as its shield alone: the popup names it. Full
            access shows in red, as a warning. */}
        <span className="phone-composer-permission" data-mode={permission.mode} data-not-f7>
          <SolarIcon name="shield-check" size={16} aria-hidden="true" />
        </span>
        <ChevronDown size={14} aria-hidden="true" />
      </Button>
      <PhoneSettingsPopup {...props} open={open} setOpen={setOpen} model={model} permission={permission}
        permissionLabel={permissionLabel} showEffort={showEffort} />
    </div>
  );
});

type MenuProps = Props & {
  open: boolean;
  setOpen: (open: boolean) => void;
  model: ReturnType<typeof useChatModel>;
  permission: ReturnType<typeof usePermissionMode>;
  permissionLabel: string;
  showEffort: boolean;
  /** The popup's heading: this conversation, or the next new chat. */
  heading?: string;
};

type Rows = Omit<MenuProps, "open" | "setOpen"> & { close: () => void };
const SettingsContext = createContext<Rows | null>(null);

/** Framework7's popup, its own View inside: the settings list, and a page
 *  for each row's choices. */
function PhoneSettingsPopup({ open, setOpen, ...rows }: MenuProps) {
  return (
    <PhonePortal>
      <SettingsContext.Provider value={{ ...rows, close: () => setOpen(false) }}>
        <Popup opened={open} onPopupClosed={() => setOpen(false)} swipeToClose push>
          <View url="/" routes={SETTINGS_ROUTES} />
        </Popup>
      </SettingsContext.Provider>
    </PhonePortal>
  );
}

/** The same popup for the next new chat, from the phone's list: it sets
 *  the draft that the enclosing scope names (``prepareNewChat``) without
 *  opening it. The controls read as the composer's do (composer/index.tsx). */
export function PhoneNewChatSettings({ open, setOpen }: {
  open: boolean;
  setOpen: (open: boolean) => void;
}) {
  const { text } = useTranslation();
  const sid = useScopedSessionId();
  const effort = useThinkingEffort();
  const chatAgent = useSessionAgentSettings().chat || NO_CHAT_AGENT;
  const { noEnabledModels } = useModelAvailability();
  const { webSearch, toggleWebSearch } = useToolsToggles();
  const fastByModel = useSessionScope((s) => s.settings.fastByModel);
  const fastDefault = useSessionScope((s) => s.settings.fast);
  const fastKey = `${chatAgent.provider ?? ""}:${chatAgent.model ?? ""}`;
  const fastEnabled = fastByModel?.[fastKey] ?? fastDefault;
  const setSettings = useBoundSetComposerSettings();
  // A draft has no session to tell yet: its first message carries these.
  const { unattended, toggleUnattended } = useUnattendedMode(null, () => false);
  const { sandbox, sandboxAvailable, toggleSandbox } = useSandboxToggle(sid, false);
  const model = useChatModel(chatAgent.provider, chatAgent.model, open);
  const permission = usePermissionMode();
  const permissionLabel = permission.options.find((o) => o.value === permission.mode)?.label ?? permission.mode;
  const blocked = noEnabledModels && !isHarnessProvider(chatAgent.provider);
  return (
    <PhoneSettingsPopup
      open={open}
      setOpen={setOpen}
      heading={text("New chat", "新对话")}
      onPickImages={() => {}}
      webSearchEnabled={webSearch}
      toggleWebSearch={toggleWebSearch}
      fastEnabled={fastEnabled}
      fastSupported={!!chatAgent.fast}
      toggleFast={() => setSettings({ fast: !fastEnabled, fastByModel: { ...fastByModel, [fastKey]: !fastEnabled } })}
      unattended={unattended}
      toggleUnattended={toggleUnattended}
      sandboxEnabled={sandbox}
      sandboxAvailable={sandboxAvailable}
      toggleSandbox={toggleSandbox}
      chatAgent={chatAgent}
      chatModel={chatAgent.model}
      noEnabledModels={blocked}
      thinking={effort.thinking}
      thinkingOptions={effort.options}
      setThinking={effort.set}
      settled
      model={model}
      permission={permission}
      permissionLabel={permissionLabel}
      showEffort={!!chatAgent.model && !blocked && effort.options.length > 0}
    />
  );
}

const NO_CHAT_AGENT: NonNullable<ReturnType<typeof useSessionAgentSettings>["chat"]> = {};

type Option = { value: string; label: string; disabled?: boolean };
type Group = { label?: string; options: Option[] };

/** A row whose choices open as a page of the popup: Framework7's smart
 *  select, over a select of the choices. */
function PickRow({ title, value, current, groups, onPick, disabled }: {
  title: string;
  value: string;
  /** What the row names when its value is none of the choices. */
  current?: string;
  groups: Group[];
  onPick: (value: string) => void;
  disabled?: boolean;
}) {
  const { text } = useTranslation();
  const pick = useRef(onPick);
  pick.current = onPick;
  const all = groups.flatMap((group) => group.options);
  // The smart select reads its choices as it opens and names the chosen one
  // when it starts: a new list or choice starts it again.
  const key = `${value}|${all.map((option) => option.value).join(",")}`;
  const option = (o: Option) => <option key={o.value} value={o.value} disabled={o.disabled}>{o.label}</option>;
  return (
    <ListItem
      key={key}
      title={title}
      smartSelect
      disabled={disabled}
      smartSelectParams={{
        openIn: "page",
        closeOnSelect: true,
        // The check at the end of the row, as iOS's own lists put it.
        inputIconPosition: "end",
        pageBackLinkText: text("Back", "返回"),
        // Its `change` event, which Framework7's types leave out.
        on: { change: (_select: unknown, next: unknown) => { haptic("selection"); pick.current(String(next)); } },
      } as SmartSelect.Parameters}
    >
      <select name={title} value={value} onChange={() => {}}>
        {all.some((o) => o.value === value) ? null : <option value={value} disabled>{current || value || "—"}</option>}
        {groups.map((group, index) => group.label
          ? <optgroup key={group.label + index} label={group.label}>{group.options.map(option)}</optgroup>
          : group.options.map(option))}
      </select>
    </ListItem>
  );
}

// Framework7's List puts its rows in its <ul> by their component's name.
PickRow.displayName = "f7-list-item-pick";

/** An on/off row: Framework7's toggle. */
function ToggleRow({ title, on, flip }: { title: string; on: boolean; flip: () => void }) {
  return (
    <ListItem title={title}>
      <Toggle slot="after" checked={on} onToggleChange={() => { haptic("selection"); flip(); }} />
    </ListItem>
  );
}

/** The model's choices: a harness's own models, then OpenProgram's by
 *  provider (useModelPicker, the model menu's list). */
function useModelChoices(model: ReturnType<typeof useChatModel>) {
  const { text } = useTranslation();
  const harness = model.harness.harness ? {
    id: model.harness.harness,
    provider: harnessProvider(model.harness.harness) ?? model.provider ?? "",
    entry: model.harness.entry,
  } : undefined;
  const picker = useModelPicker({ currentProvider: model.provider, currentModel: model.model, onClose: () => {}, harness });
  const groups: Group[] = [];
  let value = "";
  if (harness) {
    groups.push({
      label: harness.entry?.label ?? harness.id,
      options: (harness.entry?.models ?? []).map((m) => {
        const key = JSON.stringify(["h", harness.provider, m.id]);
        if (model.provider === harness.provider && listedModel(harness.entry, model.model)?.id === m.id) value = key;
        return { value: key, label: m.label };
      }),
    });
  }
  if (picker.gateway) {
    for (const group of picker.byProvider) {
      groups.push({
        label: harness ? `${picker.providerName(group.provider)} · ${text("through OpenProgram", "通过 OpenProgram")}` : picker.providerName(group.provider),
        options: group.models.map((m) => {
          const key = JSON.stringify(["m", m.provider, m.id]);
          if (picker.isActive(m)) value = key;
          return { value: key, label: m.name || m.id };
        }),
      });
    }
  }
  // A model the lists leave out (an id the harness passed through) is
  // still named, as the composer's button names it.
  const current = value ? undefined : model.model ?? undefined;
  const pick = (key: string) => {
    const [kind, provider, id] = JSON.parse(key) as [string, string, string];
    if (kind === "h") void picker.pickModel(provider, id);
    else void picker.pick(provider, id);
  };
  return { groups, value, current, pick };
}

ToggleRow.displayName = "f7-list-item-toggle";

const SETTINGS_ROUTES = [{ path: "/", component: SettingsPage }];

function SettingsPage() {
  const rows = useContext(SettingsContext);
  return rows ? <PhoneSettingsRows {...rows} /> : null;
}

function PhoneSettingsRows({ model, permission, permissionLabel, showEffort, heading, close, ...props }: Rows) {
  const { text } = useTranslation();
  // The popup is open: read the harnesses its Harness row lists.
  const harness = useHarness(true);
  const agent = useChatAgent(() => {});
  const pickHarness = useHarnessPick(harness, () => {});
  const harnessAgents = useHarnessAgents(harness, () => {});
  const models = useModelChoices(model);
  const efforts = props.thinkingOptions.map((option) => ({ value: option.value, label: formatEffortLabel(option.value) }));

  return (
    <Page>
      <Navbar title={heading ?? text("This conversation", "这个对话")}>
        <Link slot="right" popupClose>{text("Done", "完成")}</Link>
      </Navbar>
      <List strong inset dividersIos>
        <PickRow
          title="Harness"
          value={harness.harness ?? OPENPROGRAM_HARNESS}
          groups={[{ options: [
            { value: OPENPROGRAM_HARNESS, label: "OpenProgram" },
            ...(harness.harnesses ?? []).map((h) => ({
              value: h.id,
              label: [h.label, harnessNote(h, text)].filter(Boolean).join(" · "),
              disabled: !!unavailable(h),
            })),
          ] }]}
          onPick={(id) => void pickHarness(id, harness.harnesses?.find((h) => h.id === id))}
        />
        <PickRow title={text("Model", "模型")} value={models.value} current={models.current} groups={models.groups} onPick={models.pick} />
        {/* More efforts than a segmented control holds: a row under the model. */}
        {showEffort && efforts.length > 5 ? (
          <PickRow title={text("Thinking", "思考力度")} value={props.thinking} groups={[{ options: efforts }]}
            onPick={(value) => props.setThinking(value as typeof props.thinking)} />
        ) : null}
      </List>
      {/* A segmented control while its choices fit (the system keeps one to
          five segments on a phone); more, and they are a row above. */}
      {showEffort && efforts.length <= 5 ? (
        <>
          <BlockTitle>{text("Thinking", "思考力度")}</BlockTitle>
          <Block>
            <Segmented strong>
              {efforts.map((option) => (
                <Button key={option.value} active={option.value === props.thinking}
                  onClick={() => { haptic("selection"); props.setThinking(option.value as typeof props.thinking); }}>
                  {option.label}
                </Button>
              ))}
            </Segmented>
          </Block>
        </>
      ) : null}
      <List strong inset dividersIos>
        <PickRow
          title={text("Permissions", "权限")}
          value={permission.mode}
          groups={[{ options: permission.options.map((o) => ({ value: o.value, label: o.label })) }]}
          onPick={(mode) => { if (!permission.pending) permission.set(mode as typeof permission.mode); }}
        />
        {harness.harness ? (
          // The outside agent's own agents (Codex profiles, Claude Code's
          // agents); until the harness is read it has no choices to list.
          <PickRow
            title={text("Agent", "智能体")}
            value={harnessAgents.currentId}
            disabled={!harness.entry}
            groups={harnessAgents.shown.map((group) => ({
              label: group.title ?? undefined,
              options: harnessAgents.choices.filter((a) => a.kind === group.kind).map((a) => ({ value: a.id, label: harnessAgents.label(a) })),
            }))}
            onPick={(id) => void harnessAgents.pick(id)}
          />
        ) : null}
        {/* Rows, not a fragment: the list puts only its rows in its <ul>. */}
        {harness.harness ? null : (
          <PickRow
            title={text("Agent", "智能体")}
            value={agent.currentId ?? ""}
            groups={[
              { label: agent.liveAgents.length ? text("Chat", "对话") : undefined, options: agent.chatAgents.map((a) => ({ value: a.id, label: a.name || a.id })) },
              ...(agent.liveAgents.length ? [{ label: text("Always-on", "常驻"), options: agent.liveAgents.map((a) => ({ value: a.id, label: a.name || a.id })) }] : []),
            ]}
            onPick={(id) => { const found = agent.agents?.find((a) => a.id === id); if (found) void agent.pick(found); }}
          />
        )}
        {harness.harness ? null : (
          <ListItem link="#" title={text("Manage Agents", "管理 Agents")} onClick={() => { close(); navigate("/agents"); }} />
        )}
      </List>
      <List strong inset dividersIos>
        {props.fastSupported ? <ToggleRow title={text("Fast mode", "高速模式")} on={props.fastEnabled} flip={props.toggleFast} /> : null}
        {/* An outside agent searches and runs commands its own way. */}
        {harness.harness ? null : <ToggleRow title={text("Web search", "网页搜索")} on={props.webSearchEnabled} flip={props.toggleWebSearch} />}
        {props.sandboxAvailable && !harness.harness ? <ToggleRow title={text("Sandbox", "沙箱")} on={props.sandboxEnabled} flip={props.toggleSandbox} /> : null}
        <ToggleRow title={text("Unattended", "无人值守")} on={props.unattended} flip={props.toggleUnattended} />
      </List>
    </Page>
  );
}
