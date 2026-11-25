"use client";

/**
 * This computer: what OpenProgram may use on it. Each row is on or off;
 * Turn on asks the worker to guide the grant (``/api/system/access/guide``):
 * macOS's own prompt where macOS has one, otherwise the right System
 * Settings page with a small window under it showing the app to drag into
 * the list, as Codex Computer Use does. The rows update by themselves when
 * a permission flips.
 *
 * Only permissions with an on/off switch are listed. Controlling another
 * app (Automation) and opening files are asked by macOS the first time
 * they are needed, so they have nothing to set here.
 *
 * Below them, one switch says whether a computer paired with this one may
 * send its computer_use calls here (``computer_use.paired_devices``, off by
 * default); they then run on this computer under these permissions and its
 * own rules.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { SolarIcon } from "@/components/solar-icons";
import { Switch } from "@/components/ui/switch";
import { useTranslation } from "@/lib/i18n";
import { jsonFetch } from "@/lib/net/fetch-client";
import { settingRowsQuery, systemAccessQuery, type Capability } from "@/lib/settings/system-page-queries";
import styles from "../settings-page.module.css";
import type { Text } from "./device-requests";

const WHAT_IT_DOES: Record<string, [string, string]> = {
  accessibility: ["Lets OpenProgram use app interfaces", "让 OpenProgram 操作应用界面"],
  screen_recording: ["Lets OpenProgram see what apps show", "让 OpenProgram 看到应用里的画面"],
  input_monitoring: ["Lets voice input listen for its shortcut key", "让语音输入响应快捷键"],
  microphone: ["Voice input", "语音输入"],
  camera: ["Photos and video you ask for", "按你的要求拍照或录像"],
  calendar: ["Reads and adds events you ask for", "按你的要求读取和添加日程"],
  reminders: ["Reads and adds reminders you ask for", "按你的要求读取和添加提醒"],
  contacts: ["Looks up people you name", "查找你提到的联系人"],
};

/** The permissions that have an on/off switch: the ones OpenProgram can ask for. */
export function switchable(rows: Capability[]): Capability[] {
  return rows.filter((row) => row.request_mode === "native" && row.status !== "unsupported");
}

function name(row: Capability, text: Text): string {
  return text(row.label || row.id, row.label_zh || row.label || row.id);
}

/** The setting that lets paired devices operate this computer. */
const PAIRED_DEVICES = "computer_use.paired_devices";

export function permissionsReady(rows: Capability[] | undefined): boolean | null {
  if (!rows) return null;
  const listed = switchable(rows);
  return listed.length > 0 && listed.every((row) => row.status === "granted");
}

export function ThisComputerDialog({ onClose }: { onClose: () => void }) {
  const { text } = useTranslation();
  const [asked, setAsked] = useState<string[]>([]);
  // While a grant is under way the rows are read every two seconds, so a flipped switch shows at once.
  const access = useQuery({ ...systemAccessQuery, refetchInterval: asked.length ? 2000 : false });
  const rows = switchable(access.data?.capabilities ?? []);
  const client = useQueryClient();
  const settings = useQuery(settingRowsQuery);
  const pairedOn = settings.data?.find((row) => row.key === PAIRED_DEVICES)?.value === true;

  async function setPaired(on: boolean) {
    client.setQueryData(settingRowsQuery.queryKey, (all) =>
      all?.map((row) => (row.key === PAIRED_DEVICES ? { ...row, value: on } : row)));
    await jsonFetch("/api/settings", { method: "POST", body: JSON.stringify({ key: PAIRED_DEVICES, value: on }) })
      .catch(() => {});
    await client.invalidateQueries({ queryKey: settingRowsQuery.queryKey });
  }

  async function turnOn(row: Capability) {
    setAsked((list) => [...list, row.id]);
    await jsonFetch("/api/system/access/guide", {
      method: "POST",
      body: JSON.stringify({ capabilities: [row.id] }),
    }).catch(() => {});
  }

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>{text("This computer", "这台电脑")}</DialogTitle>
          <DialogDescription>{text("What OpenProgram may use on this computer.", "OpenProgram 可以在这台电脑上使用的权限。")}</DialogDescription>
        </DialogHeader>
        <div className={styles.card}>
          {rows.map((row) => {
            const on = row.status === "granted";
            const what = WHAT_IT_DOES[row.id];
            return (
              <div key={row.id} className={styles.row} data-capability={row.id}>
                <div className={`${styles.label} min-w-0 flex-1`}>
                  <div>{name(row, text)}</div>
                  {what ? <div className={styles.rowHelp}>{text(...what)}</div> : null}
                </div>
                {on ? (
                  <span className="ml-auto inline-flex items-center gap-1 text-fs-sm text-accent-green">
                    <SolarIcon name="check-circle" size={16} aria-hidden="true" />
                    {text("On", "已打开")}
                  </span>
                ) : (
                  <Button variant="elevated" className="ml-auto" onClick={() => void turnOn(row)}>
                    {text("Turn on", "打开")}
                  </Button>
                )}
              </div>
            );
          })}
        </div>
        <div className={styles.card}>
          <div className={styles.row} data-setting={PAIRED_DEVICES}>
            <div className={`${styles.label} min-w-0 flex-1`}>
              <div>{text("Let paired devices operate this computer", "允许配对的设备操作这台电脑")}</div>
              <div className={styles.rowHelp}>
                {text(
                  "Their computer_use calls run here, with these permissions and this computer's rules.",
                  "它们的电脑操作在这里执行，使用这里的权限和规则。",
                )}
              </div>
            </div>
            <Switch
              className="ml-auto"
              aria-label={text("Let paired devices operate this computer", "允许配对的设备操作这台电脑")}
              checked={pairedOn}
              disabled={!settings.data}
              onCheckedChange={(on) => void setPaired(on)}
            />
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
