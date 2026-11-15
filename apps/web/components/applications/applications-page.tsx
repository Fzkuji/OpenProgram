"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import * as Menu from "@radix-ui/react-dropdown-menu";
import { MoreHorizontal } from "lucide-react";
import { applicationRequest, type ApplicationDefinition } from "@/lib/net/applications";
import { refreshApplications, useApplicationList } from "@/lib/net/application-list";
import { useTranslation } from "@/lib/i18n";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { MenuRow } from "@/components/ui/menu/menu-parts";
import {
  ITEM_DESC, ITEM_TITLE, ITEM_TWO_LINE, MENU_ITEM_STATES, MENU_PANEL, MENU_SEPARATOR, itemCls,
} from "@/components/ui/menu/menu-styles";
import { BoxesIcon, FolderOpenIcon, RefreshCwIcon } from "@/components/animated-icons";
import { ManageEmptyState, ManagePage, ManagePageHeader, ManageSummary, managePageStyles as shared, ManageLoadingState } from "@/components/ui/manage-page";
import { useFolderPicker } from "@/components/ui/folder-picker";
import { requestProgramSelection } from "@/components/programs/programs-selection";
import { ApplicationIcon } from "./application-icon";
import { useApplicationLauncher } from "./use-application-launcher";
import { applicationRows, type ApplicationFilter, type ApplicationRow } from "./application-rows";
import styles from "./applications-page.module.css";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";

const menuItem = itemCls(false) + " " + MENU_ITEM_STATES;
const dangerItem = itemCls(false, true) + " " + MENU_ITEM_STATES;

export function ApplicationsPage() {
  const { text } = useTranslation();
  const router = useRouter();
  const launcher = useApplicationLauncher(() => router.push("/chat"));
  const { pickFolder, folderPickerDialog } = useFolderPicker();
  const { applications: listed, builtin, loaded } = useApplicationList();
  const [filter, setFilter] = useState<ApplicationFilter>("all");
  const [installing, setInstalling] = useState(false);
  const [directory, setDirectory] = useState("");
  const [replace, setReplace] = useState(false);
  const [trust, setTrust] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [uninstalling, setUninstalling] = useState<ApplicationRow | null>(null);
  const refresh = useCallback(() => refreshApplications({ fresh: true }), []);
  useEffect(() => { void refreshApplications().catch(reason => setError(String(reason.message ?? reason))); }, []);

  const rows = useMemo(() => applicationRows(listed, builtin), [listed, builtin]);
  const shown = rows.filter(row => filter === "all" || (filter === "builtin") === row.builtin);
  const enabledCount = rows.filter(row => row.enabled).length;

  async function change(action: () => Promise<unknown>, message: string) {
    if (busy) return false;
    setBusy(true); setError(""); setNotice("");
    try { await action(); await refresh(); setNotice(message); return true; }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); return false; }
    finally { setBusy(false); }
  }
  async function chooseDirectory() {
    try { const selected = await pickFolder(); if (selected) { setDirectory(selected); setReplace(false); setTrust(false); } }
    catch (reason) { setError(String(reason)); }
  }
  function openInstall(source = "") {
    setDirectory(source); setReplace(Boolean(source)); setTrust(false); setInstalling(true);
  }
  const update = (row: ApplicationRow, patch: Partial<Pick<ApplicationDefinition, "enabled" | "hidden">>) => void change(
    () => applicationRequest(`/api/applications/${encodeURIComponent(row.id)}`, "PATCH", patch),
    text("Application settings saved", "应用设置已保存"),
  );
  function open(row: ApplicationRow) {
    if (row.definition) void launcher.launch(row.definition);
    else if (row.builtinTab) { useCenterTabs.getState().openBuiltinTab(row.builtinTab); router.push("/chat"); }
  }
  function showInAbilities(row: ApplicationRow) {
    requestProgramSelection(row.builtin ? { name: row.id } : { applicationId: row.id });
    router.push("/programs");
  }

  return <ManagePage>
    <ManagePageHeader
      title={text("Applications", "应用")}
      toolbar={<>
        {loaded && rows.length > 0 && <ManageSummary>{text(`${enabledCount} of ${rows.length} enabled`, `${enabledCount}/${rows.length} 个已启用`)}</ManageSummary>}
        <ToggleGroup
          type="single" variant="elevated" spacing={1}
          value={filter}
          onValueChange={value => { if (value) setFilter(value as ApplicationFilter); }}
          aria-label={text("Show applications", "显示的应用")}
        >
          <ToggleGroupItem value="all">{text("All", "全部")}</ToggleGroupItem>
          <ToggleGroupItem value="builtin">{text("Built in", "内置")}</ToggleGroupItem>
          <ToggleGroupItem value="installed">{text("Installed", "已安装")}</ToggleGroupItem>
        </ToggleGroup>
      </>}
      actions={[
        { label: text("Refresh", "刷新"), onClick: () => void change(refresh, ""), icon: RefreshCwIcon, iconOnly: true, disabled: busy },
        { label: text("Install…", "安装…"), onClick: () => openInstall(), primary: true, disabled: busy },
      ]}
    />
    {(error || launcher.error) && <div role="alert" className={shared.errorBar}>{error || launcher.error}</div>}
    {notice && <div role="status" className={styles.notice}>{notice}</div>}
    <div className={shared.body}>
      <div className="flex flex-col gap-[24px] w-full">
        {launcher.dialog}{folderPickerDialog}
        {!loaded && !error ? (
          <ManageLoadingState>{text("Loading applications…", "正在加载应用…")}</ManageLoadingState>
        ) : !loaded ? (
          <ManageEmptyState
            compact
            icon={<BoxesIcon size={20} />}
            title={text("Applications are unavailable", "暂时无法加载应用")}
            description={text("Check the error above, then try again.", "请查看上方错误后重试。")}
            action={<Button variant="elevated" disabled={busy} onClick={() => void change(refresh, "")}>{text("Retry", "重试")}</Button>}
          />
        ) : shown.length === 0 ? (
          <ManageEmptyState
            compact
            icon={<BoxesIcon size={20} />}
            title={filter === "installed" ? text("No applications installed", "尚未安装应用") : text("No applications", "没有应用")}
            description={text("Install one from a local folder with Install…. Tools are managed in Abilities.", "可通过“安装…”从本地文件夹安装。工具在能力页管理。")}
          />
        ) : (
          <section className="flex flex-col" aria-label={text("Applications", "应用")}>
            {shown.map(row => <div className={styles.application} data-disabled={row.enabled ? undefined : ""} key={row.key} role="group" aria-label={row.title}>
              <span className={styles.appIcon}><ApplicationIcon icon={row.icon} id={row.id} size={44} /></span>
              <div className={styles.appMain}>
                <div className={`${styles.appTitle} flex items-baseline gap-[8px] min-w-0`}>
                  <h3>{row.title}</h3>
                  <span className={styles.appMeta}>{[
                    row.builtin ? text("built in", "内置") : row.version,
                    row.scope === "project" ? text("Per project", "按项目") : text("Global", "全局"),
                  ].filter(Boolean).join(" · ")}</span>
                </div>
                {row.description && <p className={styles.description}>{row.description}</p>}
              </div>
              <div className={`${styles.appActions} flex items-center gap-[10px] flex-none`}>
                {(row.definition || row.builtinTab) && <Button variant="elevated" disabled={busy || !row.enabled} onClick={() => open(row)}>{text("Open", "打开")}</Button>}
                <Switch checked={row.enabled} disabled={busy} aria-label={text(`Enable ${row.title}`, `启用${row.title}`)} onCheckedChange={checked => update(row, { enabled: checked })} />
                <Menu.Root>
                  <Menu.Trigger asChild>
                    <Button variant="ghost" size="icon-xs" aria-label={text(`More for ${row.title}`, `${row.title} 的更多操作`)}><MoreHorizontal aria-hidden /></Button>
                  </Menu.Trigger>
                  <Menu.Portal><Menu.Content side="bottom" align="end" sideOffset={4} className={MENU_PANEL + " min-w-[200px]"}>
                    <Menu.Item className={menuItem} onSelect={() => showInAbilities(row)}><MenuRow label={text("Show in Abilities", "在能力中显示")} /></Menu.Item>
                    {row.definition && <Menu.Item className={menuItem} disabled={busy} onSelect={() => update(row, { hidden: !row.definition?.hidden })}>
                      <MenuRow label={row.definition.hidden ? text("Show in new tab", "在新标签页显示") : text("Hide from new tab", "从新标签页隐藏")} />
                    </Menu.Item>}
                    {!row.builtin && row.source && <Menu.Item className={menuItem + " " + ITEM_TWO_LINE} disabled={busy} onSelect={() => { openInstall(row.source); setNotice(text("Review the source, then install to update.", "请审查来源，然后安装以完成更新。")); }}>
                      <span className="min-w-0 flex-1 text-left">
                        <span className={ITEM_TITLE}>{text("Update from source", "从来源更新")}</span>
                        <span className={ITEM_DESC + " break-all"}>{row.source}</span>
                      </span>
                    </Menu.Item>}
                    {!row.builtin && <>
                      <Menu.Separator className={MENU_SEPARATOR} />
                      <Menu.Item className={dangerItem} disabled={busy} onSelect={() => setUninstalling(row)}><MenuRow danger label={text("Uninstall", "卸载")} /></Menu.Item>
                    </>}
                  </Menu.Content></Menu.Portal>
                </Menu.Root>
              </div>
            </div>)}
          </section>
        )}
      </div>
    </div>

    <Dialog open={installing} onOpenChange={next => { if (!busy) setInstalling(next); }}>
      <DialogContent className="sm:max-w-[520px]">
        <form className="grid gap-[10px]" onSubmit={event => { event.preventDefault(); void change(
          () => applicationRequest("/api/applications/install", "POST", { path: directory.trim(), replace, trust }),
          text("Application installed", "应用已安装"),
        ).then(done => { if (done) setInstalling(false); }); }}>
          <DialogHeader>
            <DialogTitle>{text("Install from a folder", "从文件夹安装")}</DialogTitle>
            <DialogDescription>{text("Applications have their own interface and saved data. Enabled applications appear on the new tab page.", "应用拥有自己的界面和数据。已启用的应用会显示在新标签页。")}</DialogDescription>
          </DialogHeader>
          <label className={styles.fieldLabel} htmlFor="application-directory">{text("Application directory", "应用目录")}</label>
          <div className={`${styles.directory} flex gap-[8px]`}>
            <Input id="application-directory" className="flex-1 max-[760px]:flex-none" value={directory} onChange={event => { setDirectory(event.target.value); setTrust(false); }} placeholder={text("Local directory containing application.json", "包含 application.json 的本地目录")} required disabled={busy} />
            <Button type="button" variant="elevated" disabled={busy} onClick={() => void chooseDirectory()}>
              <FolderOpenIcon size={16} aria-hidden />{text("Browse", "浏览")}
            </Button>
          </div>
          <div className={`${styles.checks} grid gap-[6px] mt-[2px]`}>
            <label><Checkbox checked={replace} onCheckedChange={value => setReplace(value === true)} disabled={busy} />{text("Replace the installed version from this source", "替换此来源的已安装版本")}</label>
            <label><Checkbox checked={trust} onCheckedChange={value => setTrust(value === true)} disabled={busy} />{text("I reviewed and trust this application's Python code to run locally", "我已审查并信任此应用的 Python 代码在本机运行")}</label>
          </div>
          {error && installing && <p role="alert" className={styles.dialogError}>{error}</p>}
          <DialogFooter>
            <Button type="button" variant="elevated" disabled={busy} onClick={() => setInstalling(false)}>{text("Cancel", "取消")}</Button>
            <Button type="submit" disabled={busy || !directory.trim()}>{busy ? text("Working…", "处理中…") : text("Install application", "安装应用")}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>

    {uninstalling && <ConfirmDialog
      destructive
      title={text(`Uninstall ${uninstalling.title}?`, `卸载${uninstalling.title}？`)}
      description={text("Its menu entry and running operations go; its saved data is kept, and installing it again restores it.", "菜单入口和运行中的操作会移除；已保存的数据保留，重新安装即可恢复。")}
      confirmLabel={text("Uninstall", "卸载")}
      onCancel={() => setUninstalling(null)}
      onConfirm={() => {
        const row = uninstalling;
        setUninstalling(null);
        void change(() => applicationRequest(`/api/applications/${encodeURIComponent(row.id)}`, "DELETE"), text("Application uninstalled; saved data retained", "应用已卸载，已保存的数据保留"));
      }}
    />}
  </ManagePage>;
}
