"use client";

/**
 * Devices — one list of every device: the computers OpenProgram works on
 * (this computer, SSH servers, Docker, another computer's OpenProgram…)
 * and the phones and browsers that open this computer. One Add device
 * button asks what the device is and then one thing (``devices/add-device-dialog``);
 * Open this computer from switches the ways in (``devices/access-section``).
 *
 * This computer's row opens what OpenProgram may use on it, and whether
 * paired devices may operate it (``devices/this-computer-dialog``). Each
 * other row's menu edits, tests,
 * switches off or removes a computer, or renames or revokes a phone.
 * Same registry as ``openprogram machines``; every change is broadcast so
 * open composers refresh their picker.
 */
import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import { ChevronRight, MoreHorizontal, Smartphone } from "lucide-react";

import { Button } from "@/components/ui/button";
import { confirmAction } from "@/components/ui/confirm-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { SolarIcon } from "@/components/solar-icons";
import { MenuRow, type MenuGlyph } from "@/components/ui/menu/menu-parts";
import { MENU_ITEM_STATES, MENU_PANEL, MENU_SEPARATOR, itemCls } from "@/components/ui/menu/menu-styles";
import { cn } from "@/lib/utils";
import { MachineIcon } from "@/components/machines/machine-icon";
import { formatRelativeTime } from "@/lib/format-utils/format";
import { useTranslation } from "@/lib/i18n";
import { jsonFetch } from "@/lib/net/fetch-client";
import { wsRequest } from "@/lib/net/ws/ws-request";
import { systemAccessQuery } from "@/lib/settings/system-page-queries";
import {
  LOCAL_ID,
  connectionSummary,
  ensureMachines,
  hasUnknownConnection,
  isRemoteOpenProgram,
  useMachines,
  type MachineInfo,
} from "@/lib/machines/store";
import { SettingsHeading, SettingsPage } from "./settings-ui";
import styles from "./settings-page.module.css";
import { AccessSection, useWays } from "./devices/access-section";
import { AddDeviceDialog } from "./devices/add-device-dialog";
import { machineRequest, readPairedDevices, TEST_WAIT_MS, type PairedDevice, type Text } from "./devices/device-requests";
import { ManualDeviceDialog } from "./devices/manual-device-dialog";
import { permissionsReady, switchable, ThisComputerDialog } from "./devices/this-computer-dialog";

function summary(machine: MachineInfo, kinds: ReturnType<typeof useMachines.getState>["kinds"], locale: string, text: Text): string {
  if (machine.id === LOCAL_ID) return text("Runs your conversations", "运行你的对话");
  if (isRemoteOpenProgram(machine)) {
    return text("Another OpenProgram · its projects are in your sidebar", "另一台 OpenProgram · 它的项目在侧边栏里");
  }
  return machine.connection ? connectionSummary(machine.connection, kinds, locale) : "";
}

function stateText(machine: MachineInfo, text: Text): string {
  if (machine.enabled === false) return text("Off", "已关闭");
  const state = machine.status?.state;
  if (state === "connected") return text("Online", "在线");
  if (state === "error") return text("Can't reach it", "连不上");
  return "";
}

function RowMenu({ children, label }: { children: React.ReactNode; label: string }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label={label}>
          <MoreHorizontal className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" sideOffset={4} className={cn(MENU_PANEL, "min-w-[170px] text-text-primary")}>
        {children}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function Item({ glyph, label, danger = false, onSelect }: { glyph: MenuGlyph; label: string; danger?: boolean; onSelect: () => void }) {
  return (
    <DropdownMenuItem className={cn(itemCls(false, danger), MENU_ITEM_STATES)} onSelect={onSelect}>
      <MenuRow glyph={glyph} label={label} danger={danger} />
    </DropdownMenuItem>
  );
}

export function MachinesSettings() {
  const { text, locale } = useTranslation();
  const items = useMachines((s) => s.items);
  const kinds = useMachines((s) => s.kinds);
  const loaded = useMachines((s) => s.loaded);
  const access = useQuery(systemAccessQuery);
  const [ways, changeWays] = useWays();
  const [phones, setPhones] = useState<{ devices: PairedDevice[]; current: string | null } | null>(null);
  const [status, setStatus] = useState<Record<string, { ok: boolean; msg: string }>>({});
  const [adding, setAdding] = useState(false);
  const [manual, setManual] = useState<MachineInfo | null | undefined>(undefined);
  const [thisComputer, setThisComputer] = useState(false);
  const [renaming, setRenaming] = useState<{ id: string; label: string } | null>(null);
  const lang = locale === "zh" ? "zh" : "en";

  const readPhones = useCallback(() => readPairedDevices().then(setPhones).catch(() => {}), []);

  // The kept list shows at once; this reads it again behind (the store
  // retries a read that gets no answer).
  useEffect(() => {
    void ensureMachines(true);
    void readPhones();
  }, [readPhones]);

  function note(id: string, ok: boolean, msg: string) {
    setStatus((s) => ({ ...s, [id]: { ok, msg } }));
  }

  /** Each conversation gets its own instance there, which switching it off or removing it deletes. */
  function perConversation(machine: MachineInfo): boolean {
    return machine.shell?.instance_scope === "conversation";
  }

  async function setEnabled(machine: MachineInfo, enabled: boolean) {
    const res = await machineRequest("set_machine_enabled", { id: machine.id, enabled }, text);
    if (!res.ok) note(machine.id, false, res.error || "");
  }

  async function remove(machine: MachineInfo) {
    if (!(await confirmAction({
      title: text(`Remove "${machine.name}"?`, `删除“${machine.name}”？`),
      description: perConversation(machine)
        ? text(
          "Conversations and agents can no longer use it. Each conversation's container on it is removed, with its files.",
          "会话和智能体将不能再使用它。每个会话在它上面的容器会被删除，连同其中的文件。",
        )
        : text(
          "Conversations and agents can no longer use it. Nothing on that device is deleted.",
          "会话和智能体将不能再使用它。那台设备上的内容不会被删除。",
        ),
      confirmLabel: text("Remove", "删除"),
      destructive: true,
    }))) return;
    const res = await machineRequest("delete_machine", { id: machine.id }, text);
    if (!res.ok) note(machine.id, false, res.error || "");
  }

  async function test(machine: MachineInfo) {
    note(machine.id, true, text("Testing…", "测试中…"));
    const res = await wsRequest<{ id: string; ok: boolean; message: string }>(
      "test_machine", { id: machine.id }, "machine_test", (d) => d?.id === machine.id, TEST_WAIT_MS, { requestId: true },
    ).catch(() => null);
    if (!res) note(machine.id, false, text("The worker did not answer.", "后台没有响应。"));
    else note(machine.id, res.ok, res.message);
  }

  async function revoke(device: PairedDevice) {
    const ok = await confirmAction({
      title: text(`Revoke "${device.label}"?`, `吊销“${device.label}”？`),
      description: text("It is signed out at once and must be added again to come back.", "它会立即退出，之后要重新添加才能再连接。"),
      confirmLabel: text("Revoke", "吊销"),
      destructive: true,
    });
    if (!ok) return;
    await jsonFetch(`/api/auth/devices/${encodeURIComponent(device.id)}`, { method: "DELETE" }).catch(() => {});
    await readPhones();
  }

  async function rename() {
    if (renaming?.label.trim()) {
      await jsonFetch(`/api/auth/devices/${encodeURIComponent(renaming.id)}`, {
        method: "PATCH",
        body: JSON.stringify({ label: renaming.label.trim() }),
      }).catch(() => {});
      await readPhones();
    }
    setRenaming(null);
  }

  const ready = permissionsReady(access.data?.capabilities);
  const missing = switchable(access.data?.capabilities ?? []).filter((row) => row.status !== "granted").length;

  return (
    <SettingsPage
      title={text("Devices", "设备")}
      description={text(
        "Computers OpenProgram works on, and phones and computers that open this one.",
        "OpenProgram 操作的电脑，以及能打开这台电脑的手机和电脑。",
      )}
    >
      <section className="flex flex-col gap-4">
        <SettingsHeading
          title={text("Devices", "设备列表")}
          action={<Button onClick={() => setAdding(true)}>{text("Add device", "添加设备")}</Button>}
        />
        <div className={styles.card}>
          {!loaded ? <div className={styles.row} aria-hidden="true" /> : null}
          {loaded ? items.map((machine) => {
            const local = machine.id === LOCAL_ID;
            const off = machine.enabled === false;
            const found = machine.discovered === true;
            const st = status[machine.id];
            const state = stateText(machine, text);
            return (
              <div
                key={machine.id}
                className={`${styles.row} ${local ? "cursor-pointer" : ""}`}
                data-machine={machine.id}
                onClick={local ? () => setThisComputer(true) : undefined}
              >
                <MachineIcon machine={machine} size={20} />
                <div className={`${styles.label} min-w-0 flex-1`}>
                  <div>{local ? text("This computer", "这台电脑") : machine.name}</div>
                  <div className={`${styles.rowHelp} truncate`}>{summary(machine, kinds, locale, text)}</div>
                  {st ? <div className={`${st.ok ? styles.rowStatusOk : styles.rowStatusError} [overflow-wrap:anywhere]`}>{st.msg}</div> : null}
                </div>
                <div className="ml-auto flex shrink-0 items-center gap-2">
                  {local ? (
                    <>
                      {ready === null ? null : ready ? (
                        <span className="inline-flex items-center gap-1 text-fs-sm text-accent-green">
                          <SolarIcon name="check-circle" size={14} aria-hidden="true" />
                          {text("Permissions ready", "权限已就绪")}
                        </span>
                      ) : (
                        <span className="text-fs-sm text-text-muted">
                          {text(`${missing} permission${missing === 1 ? "" : "s"} to turn on`, `${missing} 项权限未打开`)}
                        </span>
                      )}
                      <ChevronRight className="size-4 text-text-muted" aria-hidden="true" />
                    </>
                  ) : (
                    <>
                      {state ? <span className="text-fs-sm text-text-muted">{state}</span> : null}
                      {found ? null : (
                        <RowMenu label={text("More", "更多")}>
                          {!isRemoteOpenProgram(machine) && !hasUnknownConnection(machine) ? (
                            <Item glyph="pen" label={text("Edit", "编辑")} onSelect={() => setManual(machine)} />
                          ) : null}
                          <Item glyph="refresh" label={text("Test", "测试")} onSelect={() => void test(machine)} />
                          <Item
                            glyph={off ? "play" : "close-circle"}
                            label={off ? text("Switch on", "打开") : text("Switch off", "关闭")}
                            onSelect={() => void setEnabled(machine, off)}
                          />
                          <DropdownMenuSeparator className={MENU_SEPARATOR} />
                          <Item glyph="trash-bin-trash" danger label={text("Remove", "删除")} onSelect={() => void remove(machine)} />
                        </RowMenu>
                      )}
                    </>
                  )}
                </div>
              </div>
            );
          }) : null}
          {phones?.devices.map((device) => (
            <div key={device.id} className={styles.row} data-paired-device={device.id}>
              <Smartphone className="size-5 shrink-0 text-text-muted" aria-hidden="true" />
              <div className={`${styles.label} min-w-0 flex-1`}>
                {renaming?.id === device.id ? (
                  <Input
                    autoFocus
                    value={renaming.label}
                    maxLength={64}
                    aria-label={text("Name", "名称")}
                    onChange={(event) => setRenaming({ id: device.id, label: event.target.value })}
                    onBlur={() => void rename()}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") void rename();
                      if (event.key === "Escape") setRenaming(null);
                    }}
                  />
                ) : (
                  <div>
                    {device.label}
                    {device.id === phones.current ? (
                      <span className={styles.rowHelp}>{"  ·  "}{text("This device", "当前设备")}</span>
                    ) : null}
                  </div>
                )}
                <div className={styles.rowHelp}>
                  {text(
                    `Opens this computer · used ${formatRelativeTime(device.last_used_at, lang)}`,
                    `打开这台电脑 · ${formatRelativeTime(device.last_used_at, lang)}使用过`,
                  )}
                </div>
              </div>
              <div className="ml-auto flex shrink-0 items-center gap-2">
                <RowMenu label={text("More", "更多")}>
                  <Item glyph="pen" label={text("Rename", "改名")} onSelect={() => setRenaming({ id: device.id, label: device.label })} />
                  <DropdownMenuSeparator className={MENU_SEPARATOR} />
                  <Item glyph="trash-bin-trash" danger label={text("Revoke", "吊销")} onSelect={() => void revoke(device)} />
                </RowMenu>
              </div>
            </div>
          ))}
        </div>
      </section>

      <AccessSection ways={ways} change={changeWays} />

      {adding ? (
        <AddDeviceDialog
          ways={ways}
          change={changeWays}
          onManual={() => { setAdding(false); setManual(null); }}
          onClose={() => { setAdding(false); void readPhones(); }}
        />
      ) : null}
      {manual !== undefined ? <ManualDeviceDialog machine={manual} onClose={() => setManual(undefined)} /> : null}
      {thisComputer ? <ThisComputerDialog onClose={() => setThisComputer(false)} /> : null}
    </SettingsPage>
  );
}
