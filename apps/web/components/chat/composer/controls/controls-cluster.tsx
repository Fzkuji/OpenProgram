"use client";

/**
 * Composer controls cluster — permission / plus menu / active tool chips on
 * the left; the model chip, thinking-effort pill, the Agent chip and
 * context ring on the right.
 *
 * Always rendered in the detached `.controlsRow` below the wrapper —
 * every composer mode shares the same row, this component only knows
 * the cluster's contents.
 */
import React, { useRef } from "react";

import { type AnimatedNavIconHandle } from "@/components/animated-icons";
import { SolarIcon } from "@/components/solar-icons";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import { useTranslation } from "@/lib/i18n";
import { effortLevelColor, formatEffortLabel } from "@/lib/effort/effort-color";
import { MENU_PANEL, MENU_SEPARATOR } from "@/components/ui/menu/menu-styles";
import { AgentBadge, AgentConfigBadge, PermissionBadge } from "../../top-bar";
import { ContextBadge } from "../../badges/context-badge";
import {
  AttachIcon,
  OptionsIcon,
  RecordIcon,
  SandboxIcon,
  UnattendedIcon,
  WebSearchIcon,
} from "../icons";
import { PlusMenuRow, ToolChip } from "./menu-pieces";
import { ThinkingEffortPill } from "./effort/thinking-effort-pill";
import type { ThinkingOption } from "./effort/use-thinking-effort";
import styles from "../composer.module.css";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface ControlsClusterProps {
  /** Split-pane composers pass their bound session id so the agent badges
   *  get unique DOM ids; unbound keeps the legacy singleton ids. */
  bound: string | null;
  plusMenuOpen: boolean;
  setPlusMenuOpen(open: boolean): void;
  onPickImages(): void;
  /** Present where a demonstration can be recorded (desktop App on macOS). */
  onRecord?(): void;
  recordingActive?: boolean;
  pendingImagesCount: number;
  pendingDocsCount: number;
  webSearchEnabled: boolean;
  /** Whether web search follows the Agent or was set on this branch (and
   *  the Agent's default); unknown leaves the tooltip without that line. */
  webSearchOrigin?: { follows: boolean; inherited: unknown } | null;
  toggleWebSearch(): void;
  fastEnabled: boolean;
  fastSupported: boolean;
  fastHint: string;
  toggleFast(): void;
  unattended: boolean;
  toggleUnattended(): void;
  sandboxEnabled: boolean;
  sandboxAvailable: boolean;
  sandboxReason: string | null;
  toggleSandbox(): void;
  chatAgent: { locked?: boolean; provider?: string; model?: string };
  chatModel: string | undefined;
  noEnabledModels: boolean;
  thinking: string;
  thinkingOptions: ThinkingOption[];
  setThinking(value: string): void;
  thinkingMenuOpen: boolean;
  setThinkingMenuOpen(next: boolean | ((v: boolean) => boolean)): void;
  thinkingTriggerRef: React.RefObject<HTMLDivElement>;
}

export function ControlsCluster({
  bound,
  plusMenuOpen,
  setPlusMenuOpen,
  onPickImages,
  onRecord,
  recordingActive = false,
  pendingImagesCount,
  pendingDocsCount,
  webSearchEnabled,
  webSearchOrigin = null,
  toggleWebSearch,
  fastEnabled,
  fastSupported,
  fastHint,
  toggleFast,
  unattended,
  toggleUnattended,
  sandboxEnabled,
  sandboxAvailable,
  sandboxReason,
  toggleSandbox,
  chatAgent,
  chatModel,
  noEnabledModels,
  thinking,
  thinkingOptions,
  setThinking,
  thinkingMenuOpen,
  setThinkingMenuOpen,
  thinkingTriggerRef,
}: ControlsClusterProps) {
  const { text } = useTranslation();
  const plusIconRef = useRef<AnimatedNavIconHandle>(null);
  const effortIconRef = useRef<AnimatedNavIconHandle>(null);
  const anyToolActive =
    webSearchEnabled || unattended || (sandboxEnabled && sandboxAvailable);
  const effortColor = effortLevelColor(thinkingOptions, thinking);

  return (
    <>
          <div className={styles.inputOptions}>
            {/* Permission control leads the left cluster, restyled by
                the wrapper CSS into Claude's borderless "Accept edits ⌄"
                text form (no border / bg; popover + id untouched). */}
            <PermissionBadge />
            <DropdownMenu
              open={plusMenuOpen}
              onOpenChange={(o) => {
                setPlusMenuOpen(o);
                // Opening the plus menu collapses the effort pill (they
                // shared the bottom row and shouldn't be open at once).
                if (o) setThinkingMenuOpen(false);
              }}
            >
              <HoverTip
                label={
                  <TipBody
                    title={text("Options", "选项")}
                    detail={text("Attach files, and turn web search or sandbox on and off", "添加文件，开关网页搜索或沙箱")}
                  />
                }
              >
                <DropdownMenuTrigger asChild>
                  <button
                    className={`${cn(buttonVariants({ variant: "elevated", size: "icon-sm" }))} ${styles.plusBtn} ${anyToolActive ? styles.hasActive : ""}`}
                    onMouseEnter={() => plusIconRef.current?.startAnimation?.()}
                    onMouseLeave={() => plusIconRef.current?.stopAnimation?.()}
                    aria-label={text("More options", "更多选项")}
                    type="button"
                  >
                    <OptionsIcon ref={plusIconRef} />
                  </button>
                </DropdownMenuTrigger>
              </HoverTip>

              {/* The same radix wrapper and MENU_PANEL frame as every other
                  dropdown; radix owns placement, flip, roving focus and
                  dismissal. 9 = 10px band gap − 1px 输入框外扩 ring（底部
                  弹层统一）。z-[200] keeps the stacking the menu always had
                  (above the composer's own layers). */}
              <DropdownMenuContent
                side="top"
                align="start"
                sideOffset={9}
                className={cn(MENU_PANEL, "z-[200] max-w-[320px]")}
              >
                {/* Attach file — a plain action; selecting it closes the
                    menu. No shortcut hint — the app registers none. */}
                <PlusMenuRow
                  active={pendingImagesCount > 0 || pendingDocsCount > 0}
                  icon={<AttachIcon size={16} />}
                  label={text("Add files or photos", "添加文件或照片")}
                  onSelect={() => onPickImages()}
                />
                {onRecord ? (
                  <PlusMenuRow
                    active={recordingActive}
                    icon={<RecordIcon size={16} />}
                    label={text("Record a workflow", "录制 Workflow")}
                    onSelect={() => onRecord()}
                  />
                ) : null}

                <DropdownMenuSeparator className={MENU_SEPARATOR} />

                {/* Toggles keep the menu open (keepOpen). */}
                <PlusMenuRow
                  active={webSearchEnabled}
                  keepOpen
                  icon={<WebSearchIcon size={16} />}
                  label={text("Web Search", "网页搜索")}
                  onSelect={() => toggleWebSearch()}
                />

                <PlusMenuRow
                  active={sandboxEnabled && sandboxAvailable}
                  keepOpen
                  disabled={!sandboxAvailable}
                  title={sandboxReason || undefined}
                  icon={<SandboxIcon size={16} />}
                  label={
                    sandboxAvailable
                      ? text("Sandbox", "沙箱")
                      : text("Sandbox · Unavailable", "Sandbox · Unavailable")
                  }
                  onSelect={() => toggleSandbox()}
                />

                <DropdownMenuSeparator className={MENU_SEPARATOR} />

                <PlusMenuRow
                  active={unattended}
                  keepOpen
                  icon={<UnattendedIcon size={16} on={unattended} />}
                  label={text("Unattended", "无人值守")}
                  onSelect={() => toggleUnattended()}
                />
              </DropdownMenuContent>
            </DropdownMenu>

            <div className={styles.activeToolChips}>
              {/* Only ENABLED tools show as a chip here. The off ones are
                  not rendered at all — they live in the + menu and are
                  turned on from there. An active chip shows its × on hover
                  to switch it back off. (The container is :empty →
                  display:none, so all-off shows nothing.) HoverTip is a
                  real top-layer tooltip; a CSS ::after would be cropped by
                  the chip's overflow:hidden. */}
              {webSearchEnabled && (
                <HoverTip label={<TipBody title={text("Web search on", "网页搜索已开启")} detail={text(
                  "With a provider that has its own web search (ChatGPT, OpenAI, Claude, Gemini), the model searches through the provider; otherwise it uses the local web_search tool and the search backend set in Settings. Click to turn off.",
                  "模型的提供商自带网页搜索时（ChatGPT、OpenAI、Claude、Gemini）由提供商搜索；否则使用本地 web_search 工具和设置里配置的搜索后端。点击关闭。",
                ) + (webSearchOrigin ? " " + (webSearchOrigin.follows
                  ? text("Follows the Agent.", "跟随 Agent。")
                  : text(`Set on this branch · Agent default: ${webSearchOrigin.inherited ? "on" : "off"}.`,
                         `本分支已设置 · Agent 默认：${webSearchOrigin.inherited ? "开" : "关"}。`)) : "")} />}>
                  <ToolChip
                    icon={<WebSearchIcon size={16} />}
                    label={text("Web Search", "网页搜索")}
                    on
                    onToggle={toggleWebSearch}
                  />
                </HoverTip>
              )}
              {sandboxEnabled && sandboxAvailable && (
                <HoverTip label={<TipBody title={text("Sandbox on", "沙箱已开启")} detail={text("Commands run in an isolated sandbox. Click to turn off.", "命令在隔离的沙箱里运行。点击关闭。")} />}>
                  <ToolChip
                    icon={<SandboxIcon size={16} />}
                    label={text("Sandbox", "沙箱")}
                    on
                    onToggle={toggleSandbox}
                  />
                </HoverTip>
              )}
              {unattended && (
                <HoverTip label={<TipBody title={text("Unattended on", "无人值守已开启")} detail={text("The agent won't stop to ask you questions. Click to turn off.", "agent 不会停下来向你提问。点击关闭。")} />}>
                  <ToolChip
                    icon={<UnattendedIcon size={16} on />}
                    label={text("Unattended", "无人值守")}
                    on
                    onToggle={toggleUnattended}
                  />
                </HoverTip>
              )}
            </div>

          </div>
          <div className={styles.inputBottomRight}>
            {/* Claude-style right cluster before the send affordance:
                the chat model as quiet borderless text ("Opus 4.8"
                form, restyled via .agentChips overrides — component
                and popover untouched), effort pill, the conversation's
                Agent, context ring last. */}
            <div className={styles.agentChips}>
              <AgentBadge
                id={bound ? `chatAgentBadge-${bound}` : "chatAgentBadge"}
                locked={!!chatAgent.locked}
                provider={chatAgent.provider}
                model={chatAgent.model}
              />
            </div>
            {/* Effort picker only when a chat model is selected. No
                persistent "no model" indicator here by design — a
                blocked send/run fires a transient top toast instead
                (see ``promptNeedModel``). The `thinking` value still
                flows to submit (uses the model default) when hidden. */}
            {chatModel && !noEnabledModels ? (
              <HoverTip
                label={
                  <TipBody
                    title={thinking
                      ? text(`Thinking effort: ${formatEffortLabel(thinking)}`, `思考力度：${formatEffortLabel(thinking)}`)
                      : text("Thinking effort", "思考力度")}
                    detail={text("How long the model thinks before answering. Click to adjust.", "模型回答前思考多久。点击调整。")}
                  />
                }
              >
                {/* Wrapper is the outside-click boundary AND the anchor
                    for the pill's floating slider (detached row). The
                    text trigger only shows in the detached row (CSS);
                    the morphed internal band keeps the icon pill.
                    aria-expanded 挂在这个 div（HoverTip 的真正 trigger
                    元素）上，卡开着时 tooltip.tsx 的拦截才生效——之前标
                    到里层按钮，HoverTip 看的是这个 div，所以拦不住。 */}
                <div
                  ref={thinkingTriggerRef}
                  className={styles.effortControl}
                  aria-expanded={thinkingMenuOpen}
                >
                  {(
                    <button
                      type="button"
                      className={`${cn(buttonVariants({ variant: "elevated", size: "sm" }))} ${styles.effortText}`}
                      aria-expanded={thinkingMenuOpen}
                      onMouseEnter={() => effortIconRef.current?.startAnimation?.()}
                      onMouseLeave={() => effortIconRef.current?.stopAnimation?.()}
                      onClick={() => {
                        setPlusMenuOpen(false);
                        setThinkingMenuOpen((v) => !v);
                      }}
                      // 常规宽度保持原有文字配色；最高档仍用紫色标识。
                      style={thinking === "max" ? { color: "#8E6BD9" } : undefined}
                    >
                      <SolarIcon
                        name="fire"
                        ref={effortIconRef}
                        size={16}
                        fit="fill"
                        className={styles.effortIcon}
                        // Fast 开着时火苗变红；否则逐级颜色只在窄态（文字隐藏时）
                        // 显示，常规宽度跟随文字颜色。
                        style={{
                          "--effort-color": thinking === "max" ? "#8E6BD9" : effortColor,
                          ...(fastEnabled && fastSupported ? { color: "var(--accent-red)" } : {}),
                        } as React.CSSProperties}
                        aria-hidden="true"
                      />
                      <span className={styles.effortValue}>
                        {thinking ? formatEffortLabel(thinking) : text("Model settings", "模型设置")}
                      </span>
                    </button>
                  )}
                  <ThinkingEffortPill
                    expanded={thinkingMenuOpen}
                    onToggle={() => {
                      setThinkingMenuOpen((v) => !v);
                      setPlusMenuOpen(false);
                    }}
                    options={thinkingOptions}
                    value={thinking}
                    onChange={setThinking}
                    fastEnabled={fastEnabled && fastSupported}
                    fastSupported={fastSupported}
                    fastHint={fastHint}
                    toggleFast={toggleFast}
                  />
                </div>
              </HoverTip>
            ) : null}
            <AgentConfigBadge id={bound ? `agentConfigBadge-${bound}` : "agentConfigBadge"} />
            <ContextBadge sessionId={bound ?? undefined} />
          </div>

    </>
  );
}
