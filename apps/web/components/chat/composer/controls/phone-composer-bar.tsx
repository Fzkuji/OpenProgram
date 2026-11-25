"use client";

/**
 * The composer's controls on a touch phone (components/phone/): one row in
 * the input box, `+` to attach, one button naming the model, effort and
 * permission, and the mic and send that the box already has. The button
 * opens every setting of the turn in one sheet, a row each; a row with a
 * › opens its choices as a page of the same sheet with a Back row (the
 * menu engine's sheets, ui/menu/phone-sheet.tsx). Paseo folds its
 * composer's controls the same way on a phone; the desktop keeps its two
 * rows of chips (controls-cluster.tsx).
 */

import { memo, useState } from "react";
import { Plus } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { MenuRow } from "@/components/ui/menu/menu-parts";
import { MenuCheck } from "@/components/ui/menu/menu-check";
import {
  CHECK_SLOT_PAD,
  GROUP_LABEL,
  ITEM_DESC,
  ITEM_TITLE,
  ITEM_TWO_LINE,
  MENU_ITEM_STATES,
  MENU_PANEL,
  itemCls,
} from "@/components/ui/menu/menu-styles";
import { ChatModelMenuBody, useChatModel } from "@/components/chat/top-bar";
import { ModelMark } from "@/components/chat/top-bar/chips/model-mark";
import { ChatAgentMenuRows, useChatAgent } from "@/components/chat/top-bar/menus/agent-config-menu";
import { HarnessAgentRows, harnessAgentLabel, useHarness } from "@/components/chat/top-bar/menus/harness-menu";
import { formatEffortLabel } from "@/lib/effort/effort-color";
import { useTranslation } from "@/lib/i18n";
import { haptic } from "@/lib/mobile/bridge";
import { SolarIcon } from "@/components/solar-icons";
import { cn } from "@/lib/utils";

import { usePermissionMode } from "./toggles/use-permission-mode";
import type { ControlsClusterProps } from "./controls-cluster";

type Props = Pick<ControlsClusterProps,
  | "onPickImages"
  | "webSearchEnabled" | "toggleWebSearch"
  | "fastEnabled" | "fastSupported" | "toggleFast"
  | "unattended" | "toggleUnattended"
  | "sandboxEnabled" | "sandboxAvailable" | "toggleSandbox"
  | "chatAgent" | "chatModel" | "noEnabledModels"
  | "thinking" | "thinkingOptions" | "setThinking">;

const ROW = cn(itemCls(false), MENU_ITEM_STATES);

export const PhoneComposerBar = memo(function PhoneComposerBar(props: Props) {
  const { text } = useTranslation();
  const [open, setOpen] = useState(false);
  const model = useChatModel(props.chatAgent.provider, props.chatAgent.model, open);
  const permission = usePermissionMode();
  const permissionLabel = permission.options.find((o) => o.value === permission.mode)?.label ?? permission.mode;
  const showEffort = !!props.chatModel && !props.noEnabledModels && props.thinkingOptions.length > 1;
  const modelText = [
    model.label || text("Choose a model", "选择模型"),
    showEffort && props.thinking ? formatEffortLabel(props.thinking) : null,
  ].filter(Boolean).join(" · ");
  const summary = `${modelText} · ${permissionLabel}`;

  return (
    <div className="phone-composer-bar">
      <button
        type="button"
        className="phone-composer-plus"
        aria-label={text("Add photos or files", "添加照片或文件")}
        onClick={props.onPickImages}
      >
        <Plus size={20} strokeWidth={1.8} aria-hidden="true" />
      </button>
      <DropdownMenu open={open} onOpenChange={setOpen}>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className="phone-composer-settings"
            disabled={!!props.chatAgent.locked}
            aria-label={text(`Settings: ${summary}`, `设置：${summary}`)}
          >
            {/* Two chips, one sheet: the model and its effort, then the
                permission, so neither truncates the other away (Happy's
                composer row). */}
            <span className="phone-composer-chip phone-composer-chip-model">
              <ModelMark provider={model.markProvider} />
              <span className="phone-composer-settings-text">{modelText}</span>
            </span>
            {/* The permission as its shield alone: its name would take the
                model's room; the sheet names it. Full access shows in the
                accent, as a warning. */}
            <span className="phone-composer-chip phone-composer-chip-permission" data-mode={permission.mode}>
              <SolarIcon name="shield-check" size={17} aria-hidden="true" />
            </span>
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent className={MENU_PANEL} onCloseAutoFocus={(e) => e.preventDefault()}>
          <PhoneSettingsRows {...props} model={model} permission={permission} permissionLabel={permissionLabel}
            showEffort={showEffort} close={() => setOpen(false)} />
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
});

function PhoneSettingsRows({ model, permission, permissionLabel, showEffort, close, ...props }: Props & {
  model: ReturnType<typeof useChatModel>;
  permission: ReturnType<typeof usePermissionMode>;
  permissionLabel: string;
  showEffort: boolean;
  close: () => void;
}) {
  const { text } = useTranslation();
  const harness = useHarness();
  const agent = useChatAgent(close);
  const toggle = (on: boolean, flip: () => void, label: string) => (
    <DropdownMenuItem
      className={ROW}
      onSelect={(event) => { event.preventDefault(); haptic("selection"); flip(); }}
      role="menuitemcheckbox"
      aria-checked={on}
    >
      <span className="min-w-0 flex-1 truncate text-left">{label}</span>
      <span className="phone-switch" data-on={on ? "" : undefined} aria-hidden="true" />
    </DropdownMenuItem>
  );

  return (
    <>
      <div className="phone-sheet-head">
        <span>{text("This conversation", "这个对话")}</span>
        <button type="button" onClick={close}>{text("Done", "完成")}</button>
      </div>
      <div className="phone-sheet-group">
        <DropdownMenuSub>
          <DropdownMenuSubTrigger className={ROW}>
            <MenuRow label={text("Model", "模型")} trail={<ModelTrail label={model.label} id={model.model} none={text("None", "未选择")} />} submenu />
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent className={MENU_PANEL}>
            <ChatModelMenuBody chat={model} onClose={close} />
          </DropdownMenuSubContent>
        </DropdownMenuSub>
      </div>
      {showEffort ? (
        <div className="phone-effort-row">
          <span className="phone-sheet-label">{text("Thinking", "思考力度")}</span>
          <div className="phone-segmented" role="radiogroup" aria-label={text("Thinking effort", "思考力度")}>
            {props.thinkingOptions.map((option) => (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={option.value === props.thinking}
                data-on={option.value === props.thinking ? "" : undefined}
                onClick={() => { haptic("selection"); props.setThinking(option.value); }}
              >
                {formatEffortLabel(option.value)}
              </button>
            ))}
          </div>
        </div>
      ) : null}
      <div className="phone-sheet-group">
      <DropdownMenuSub>
        <DropdownMenuSubTrigger className={ROW}>
          <MenuRow label={text("Permissions", "权限")} trail={permissionLabel} submenu />
        </DropdownMenuSubTrigger>
        <DropdownMenuSubContent className={MENU_PANEL}>
          <div className={GROUP_LABEL}>{text("Mode", "模式")}</div>
          {permission.options.map((o) => (
            <DropdownMenuItem
              key={o.value}
              className={cn(ROW, o.description ? ITEM_TWO_LINE : "")}
              onSelect={() => { if (!permission.pending) permission.set(o.value); }}
            >
              <span className="min-w-0 flex-1">
                <span className={o.description ? ITEM_TITLE : "truncate"}>{o.label}</span>
                {o.description ? <span className={ITEM_DESC}>{o.description}</span> : null}
              </span>
              {o.value === permission.mode ? <MenuCheck /> : <span className={CHECK_SLOT_PAD} />}
            </DropdownMenuItem>
          ))}
        </DropdownMenuSubContent>
      </DropdownMenuSub>
      {harness.harness ? (
        // The outside agent's own agents (Codex profiles, Claude Code's agents).
        <DropdownMenuSub>
          <DropdownMenuSubTrigger className={ROW}>
            <MenuRow label={text("Agent", "智能体")} trail={harnessAgentLabel(harness, text)} submenu />
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent className={MENU_PANEL}>
            <HarnessAgentRows state={harness} onPicked={close} />
          </DropdownMenuSubContent>
        </DropdownMenuSub>
      ) : (
        <DropdownMenuSub>
          <DropdownMenuSubTrigger className={ROW}>
            <MenuRow label={text("Agent", "智能体")} trail={agent.label} submenu />
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent className={MENU_PANEL}>
            <ChatAgentMenuRows chat={agent} />
          </DropdownMenuSubContent>
        </DropdownMenuSub>
      )}
      </div>
      <div className="phone-sheet-group">
        {props.fastSupported ? toggle(props.fastEnabled, props.toggleFast, text("Fast mode", "高速模式")) : null}
        {toggle(props.webSearchEnabled, props.toggleWebSearch, text("Web search", "网页搜索"))}
        {props.sandboxAvailable ? toggle(props.sandboxEnabled, props.toggleSandbox, text("Sandbox", "沙箱")) : null}
        {toggle(props.unattended, props.toggleUnattended, text("Unattended", "无人值守"))}
      </div>
    </>
  );
}

/** The model's name, and its id under it when the name is not the id. */
function ModelTrail({ label, id, none }: { label: string; id?: string; none: string }) {
  const bare = (id ?? "").replace(/^[^:/]+[:/]/, "");
  // The id only adds something when it is not the name spelled in lower case.
  const same = (v: string) => v.toLowerCase().replace(/[\s_.-]+/g, "");
  return (
    <span className="phone-model-trail">
      <span>{label || none}</span>
      {bare && same(bare) !== same(label) ? <small>{bare}</small> : null}
    </span>
  );
}
