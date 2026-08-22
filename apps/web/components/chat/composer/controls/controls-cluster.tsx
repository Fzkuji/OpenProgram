"use client";

/**
 * Composer controls cluster — permission / plus menu / active tool chips on
 * the left; chat+exec model chips, thinking-effort pill and context ring on
 * the right.
 *
 * Always rendered in the detached `.controlsRow` below the wrapper —
 * every composer mode shares the same row, this component only knows
 * the cluster's contents.
 */
import React, { useRef, useState } from "react";
import { Menu } from "@base-ui-components/react/menu";
import { Paperclip, Settings } from "lucide-react";

import {
  type AnimatedNavIconHandle,
  BicepsFlexedIcon,
} from "@/components/animated-icons";
import { HoverTip } from "@/components/ui/tooltip";
import { useTranslation } from "@/lib/i18n";
import { effortLevelColor } from "@/lib/effort-color";
import { GROUP_LABEL } from "../../top-bar/menu-styles";
import { AgentBadge, PermissionBadge } from "../../top-bar";
import { ContextBadge } from "../../context-badge";
import {
  FastIcon,
  OptionsIcon,
  ToolsIcon,
  SandboxIcon,
  UnattendedIcon,
  WebSearchIcon,
} from "../icons";
import { PlusMenuItem, ToolChip } from "./menu-pieces";
import { ThinkingEffortPill } from "./thinking-effort-pill";
import type { ThinkingOption } from "./use-thinking-effort";
import styles from "../composer.module.css";

const noop = () => {};

// `.plusMenu` was written for the old hand-rolled portal — it carries
// `position:absolute; bottom:100%; left:0; margin-bottom:4px` to sit
// above the trigger. base-ui's Menu positions the *Positioner* wrapper
// and we apply `.plusMenu` to the inner Popup panel, so those absolute
// props would fight the Positioner's transform. Neutralize them here
// (visuals — bg/border/radius/shadow/padding — stay untouched) so the
// menu reads identically while base-ui's Positioner owns placement,
// flip, and alignment (including the submenus' side="top").
const POPUP_STATIC_RESET: React.CSSProperties = {
  position: "static",
  bottom: "auto",
  left: "auto",
  marginBottom: 0,
};

export interface ControlsClusterProps {
  /** Split-pane composers pass their bound session id so the agent badges
   *  get unique DOM ids; unbound keeps the legacy singleton ids. */
  bound: string | null;
  plusMenuOpen: boolean;
  setPlusMenuOpen(open: boolean): void;
  onPickImages(): void;
  pendingImagesCount: number;
  pendingDocsCount: number;
  toolsEnabled: boolean;
  toggleTools(): void;
  webSearchEnabled: boolean;
  toggleWebSearch(): void;
  fastEnabled: boolean;
  fastSupported: boolean;
  toggleFast(): void;
  unattended: boolean;
  toggleUnattended(): void;
  sandboxEnabled: boolean;
  sandboxAvailable: boolean;
  sandboxReason: string | null;
  toggleSandbox(): void;
  toolProfiles: Record<string, string[]>;
  activeProfile: string;
  switchProfile(name: string): void;
  chatAgent: { locked?: boolean; provider?: string; model?: string };
  execAgent: { provider?: string; model?: string };
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
  pendingImagesCount,
  pendingDocsCount,
  toolsEnabled,
  toggleTools,
  webSearchEnabled,
  toggleWebSearch,
  fastEnabled,
  fastSupported,
  toggleFast,
  unattended,
  toggleUnattended,
  sandboxEnabled,
  sandboxAvailable,
  sandboxReason,
  toggleSandbox,
  toolProfiles,
  activeProfile,
  switchProfile,
  chatAgent,
  execAgent,
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
  const [profileMenuOpen, setProfileMenuOpen] = useState(false);
  const anyToolActive =
    toolsEnabled || webSearchEnabled || (fastEnabled && fastSupported) || unattended
    || (sandboxEnabled && sandboxAvailable);
  const effortColor = effortLevelColor(thinkingOptions, thinking);

  return (
    <>
          <div className={styles.inputOptions}>
            {/* Permission control leads the left cluster, restyled by
                the wrapper CSS into Claude's borderless "Accept edits ⌄"
                text form (no border / bg; popover + id untouched). */}
            <PermissionBadge />
            <Menu.Root
              open={plusMenuOpen}
              onOpenChange={(o) => {
                setPlusMenuOpen(o);
                if (!o) setProfileMenuOpen(false);
                // Opening the plus menu collapses the effort pill (they
                // shared the bottom row and shouldn't be open at once).
                if (o) setThinkingMenuOpen(false);
              }}
            >
              <Menu.Trigger
                render={
                  <button
                    className={`${styles.plusBtn} ${anyToolActive ? styles.hasActive : ""}`}
                    onMouseEnter={() => plusIconRef.current?.startAnimation?.()}
                    onMouseLeave={() => plusIconRef.current?.stopAnimation?.()}
                    title={text("Add tools, files, and more", "添加工具、文件等")}
                    aria-label={text("More options", "更多选项")}
                    type="button"
                  >
                    <OptionsIcon ref={plusIconRef} />
                  </button>
                }
              />

              <Menu.Portal>
                {/* Positioner owns placement (side/align/offset + flip);
                    Popup is the actual panel that wears `.plusMenu`. The
                    static reset stops the old absolute props from fighting
                    the Positioner. */}
                {/* 9 = 10px band gap − 1px 输入框外扩 ring（底部弹层统一）。 */}
                <Menu.Positioner side="top" align="start" sideOffset={9} style={{ zIndex: 200 }}>
                  <Menu.Popup
                    className={styles.plusMenu}
                    style={POPUP_STATIC_RESET}
                  >
                    {/* Attach file — a plain action; clicking it closes the
                        menu (default Menu.Item closeOnClick behaviour).
                        Grammar B row: 16px line icon + label. No shortcut
                        hint — the app registers none for attach. */}
                    <Menu.Item className={styles.plusMenuRow} onClick={() => onPickImages()}>
                      <PlusMenuItem
                        active={pendingImagesCount > 0 || pendingDocsCount > 0}
                        onClick={noop}
                        icon={<Paperclip size={16} />}
                        label={text("Add files or photos", "添加文件或照片")}
                      />
                    </Menu.Item>

                    <Menu.Separator className={styles.plusMenuDivider} />

                    {/* Tools — row click toggles tools; the gear opens a
                        click-controlled submenu that stays open when the
                        pointer leaves and closes on outside interaction. */}
                    <div
                      className={styles.plusMenuSplitRow}
                      role="none"
                      data-tools-active={toolsEnabled || undefined}
                    >
                      <Menu.Item
                        className={`${styles.plusMenuRow} ${styles.plusMenuPrimary}`}
                        closeOnClick={false}
                        onClick={() => {
                          toggleTools();
                          setProfileMenuOpen(false);
                        }}
                      >
                        <PlusMenuItem
                          active={toolsEnabled}
                          onClick={noop}
                          icon={<ToolsIcon size={16} />}
                          label={text("Tools", "工具")}
                        />
                      </Menu.Item>
                      <Menu.SubmenuRoot
                        open={profileMenuOpen}
                        onOpenChange={(open, { reason }) => {
                          // Base UI closes submenus when a sibling parent-menu
                          // item receives mousemove. Pointer movement is not a
                          // dismissal action for this click-only submenu.
                          if (open || reason !== "sibling-open") {
                            setProfileMenuOpen(open);
                          }
                        }}
                      >
                        <Menu.SubmenuTrigger
                          className={`${styles.plusMenuRow} ${styles.plusMenuGear}`}
                          openOnHover={false}
                          label={text("Tool profile", "工具配置")}
                          aria-label={text("Tool profile", "工具配置")}
                        >
                          <Settings size={14} />
                        </Menu.SubmenuTrigger>
                        <Menu.Portal>
                          <Menu.Positioner
                            side="right"
                            align="end"
                            sideOffset={6}
                            style={{ zIndex: 201 }}
                          >
                            <Menu.Popup
                              className={styles.plusMenu}
                              style={POPUP_STATIC_RESET}
                            >
                              <div className={GROUP_LABEL}>
                                {text("Access preset", "Access preset")}
                              </div>
                              <Menu.Item
                                className={styles.plusMenuRow}
                                onClick={() => switchProfile("__agent__")}
                              >
                                <PlusMenuItem
                                  active={activeProfile === "__agent__"}
                                  onClick={noop}
                                  icon={null}
                                  label={text("Use Agent configuration", "使用 Agent 配置")}
                                />
                              </Menu.Item>
                              {Object.keys(toolProfiles).sort().map((pName) => (
                                <Menu.Item
                                  key={pName}
                                  className={styles.plusMenuRow}
                                  onClick={() => switchProfile(pName)}
                                >
                                  <PlusMenuItem
                                    active={activeProfile === pName}
                                    onClick={noop}
                                    icon={null}
                                    label={pName === "full"
                                      ? text("All Tools", "全部工具")
                                      : pName}
                                  />
                                </Menu.Item>
                              ))}
                            </Menu.Popup>
                          </Menu.Positioner>
                        </Menu.Portal>
                      </Menu.SubmenuRoot>
                    </div>

                    {/* Web Search / Fast — toggles that must NOT close the
                        menu, so closeOnClick={false}. */}
                    <Menu.Item
                      className={styles.plusMenuRow}
                      closeOnClick={false}
                      onClick={() => toggleWebSearch()}
                    >
                      <PlusMenuItem
                        active={webSearchEnabled}
                        onClick={noop}
                        icon={<WebSearchIcon size={16} />}
                        label={text("Web Search", "网页搜索")}
                      />
                    </Menu.Item>
                    {fastSupported ? (
                      <Menu.Item
                        className={styles.plusMenuRow}
                        closeOnClick={false}
                        onClick={() => toggleFast()}
                      >
                        <PlusMenuItem
                          active={fastEnabled}
                          onClick={noop}
                          icon={<FastIcon size={16} />}
                          label={text("Fast", "高速")}
                        />
                      </Menu.Item>
                    ) : null}

                    <Menu.Item
                      className={styles.plusMenuRow}
                      closeOnClick={false}
                      disabled={!sandboxAvailable}
                      title={sandboxReason || undefined}
                      onClick={() => toggleSandbox()}
                    >
                      <PlusMenuItem
                        active={sandboxEnabled && sandboxAvailable}
                        onClick={noop}
                        icon={<SandboxIcon size={16} />}
                        label={
                          sandboxAvailable
                            ? text("Sandbox", "沙箱")
                            : text("Sandbox · Unavailable", "Sandbox · Unavailable")
                        }
                      />
                    </Menu.Item>

                    <Menu.Separator className={styles.plusMenuDivider} />

                    {/* Unattended — a toggle; keep the menu open. */}
                    <Menu.Item
                      className={styles.plusMenuRow}
                      closeOnClick={false}
                      onClick={() => toggleUnattended()}
                    >
                      <PlusMenuItem
                        active={unattended}
                        onClick={noop}
                        icon={<UnattendedIcon size={16} />}
                        label={text("Unattended", "无人值守")}
                      />
                    </Menu.Item>
                  </Menu.Popup>
                </Menu.Positioner>
              </Menu.Portal>
            </Menu.Root>

            <div className={styles.activeToolChips}>
              {/* Only ENABLED tools show as a chip here. The off ones are
                  not rendered at all — they live in the + menu and are
                  turned on from there. An active chip shows its × on hover
                  to switch it back off. (The container is :empty →
                  display:none, so all-off shows nothing.) HoverTip is a
                  real top-layer tooltip; a CSS ::after would be cropped by
                  the chip's overflow:hidden. */}
              {toolsEnabled && (
                <HoverTip label={text("Tools", "工具")}>
                  <ToolChip
                    icon={<ToolsIcon size={16} />}
                    label={text("Tools", "工具")}
                    on
                    onToggle={toggleTools}
                  />
                </HoverTip>
              )}
              {webSearchEnabled && (
                <HoverTip label={text("Web Search", "网页搜索")}>
                  <ToolChip
                    icon={<WebSearchIcon size={16} />}
                    label={text("Web Search", "网页搜索")}
                    on
                    onToggle={toggleWebSearch}
                  />
                </HoverTip>
              )}
              {fastEnabled && fastSupported && (
                <HoverTip label={text("Fast", "高速")}>
                  <ToolChip
                    icon={<FastIcon size={16} />}
                    label={text("Fast", "高速")}
                    on
                    onToggle={toggleFast}
                  />
                </HoverTip>
              )}
              {sandboxEnabled && sandboxAvailable && (
                <HoverTip label={text("Sandbox", "沙箱")}>
                  <ToolChip
                    icon={<SandboxIcon size={16} />}
                    label={text("Sandbox", "沙箱")}
                    on
                    onToggle={toggleSandbox}
                  />
                </HoverTip>
              )}
              {unattended && (
                <HoverTip label={text("Unattended", "无人值守")}>
                  <ToolChip
                    icon={<UnattendedIcon size={16} />}
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
                chat + exec models as quiet borderless text ("Opus 4.8"
                form, restyled via .agentChips overrides — components
                and their popovers untouched), effort pill, context
                ring last. */}
            <div className={styles.agentChips}>
              <AgentBadge
                id={bound ? `chatAgentBadge-${bound}` : "chatAgentBadge"}
                kind="chat"
                locked={!!chatAgent.locked}
                provider={chatAgent.provider}
                model={chatAgent.model}
              />
              <AgentBadge
                id={bound ? `execAgentBadge-${bound}` : "execAgentBadge"}
                kind="exec"
                locked={false}
                provider={execAgent.provider}
                model={execAgent.model}
              />
            </div>
            {/* Effort picker only when a chat model is selected. No
                persistent "no model" indicator here by design — a
                blocked send/run fires a transient top toast instead
                (see ``promptNeedModel``). The `thinking` value still
                flows to submit (uses the model default) when hidden. */}
            {chatModel && !noEnabledModels ? (
              <HoverTip label={text("Thinking effort", "思考力度")}>
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
                  {thinkingOptions.length > 1 && (
                    <button
                      type="button"
                      className={styles.effortText}
                      onMouseEnter={() => effortIconRef.current?.startAnimation?.()}
                      onMouseLeave={() => effortIconRef.current?.stopAnimation?.()}
                      // ponytail: the pill ignores its expanded/onToggle
                      // props (internal useState) — a programmatic click
                      // on its own (hidden) collapsed chip is the only
                      // public "open". Lift the state into the pill if a
                      // second caller ever needs it.
                      onClick={() => {
                        setPlusMenuOpen(false);
                        thinkingTriggerRef.current
                          ?.querySelector<HTMLElement>(".effort-pill-collapsed")
                          ?.click();
                      }}
                      // 常规宽度保持原有文字配色；最高档仍用紫色标识。
                      style={thinking === "max" ? { color: "#8E6BD9" } : undefined}
                    >
                      <BicepsFlexedIcon
                        ref={effortIconRef}
                        size={14}
                        className={styles.compactEffortIcon}
                        // 图标只在窄态显示，因此逐级颜色不会改变常规宽度文字。
                        style={{ color: thinking === "max" ? "#8E6BD9" : effortColor }}
                        aria-hidden="true"
                      />
                      <span className={styles.effortValue}>
                        {thinking ? thinking[0].toUpperCase() + thinking.slice(1) : ""}
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
                  />
                </div>
              </HoverTip>
            ) : null}
            <ContextBadge sessionId={bound ?? undefined} />
          </div>

    </>
  );
}
