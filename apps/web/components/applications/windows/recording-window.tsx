"use client";

/**
 * The Workflow Recording window: the same setup the composer's
 * "Record a workflow" dialog shows, on a page of its own. A recording
 * started here opens a new chat first, so the finished recording waits
 * in that chat's composer for you to send.
 */
import { useEffect } from "react";
import { Video } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ManageEmptyState, ManagePage, ManagePageHeader, managePageStyles as shared } from "@/components/ui/manage-page";
import { RecordSetupFields, useRecordSetup } from "@/components/chat/recording/record-dialog";
import { useTranslation } from "@/lib/i18n";
import { useApplicationList } from "@/lib/net/application-list";
import { recordingEnabled } from "@/lib/recording/recording-application";
import { listenForRecordingResults, recordingAvailable, useRecordingStore } from "@/lib/recording/recording-store";
import { newSession } from "@/lib/runtime-bridge/session/conversations";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import styles from "./windows.module.css";

export function RecordingWindow() {
  const { text } = useTranslation();
  const { builtin } = useApplicationList();
  const active = useRecordingStore(state => state.active);
  const setup = useRecordSetup("focused", () => {
    // The result goes to the focused composer when the recording ends.
    const key = useCenterTabs.getState().openDraftSessionTab();
    newSession(key);
  });
  useEffect(() => { listenForRecordingResults(); }, []);

  const available = recordingAvailable();
  const enabled = recordingEnabled(builtin);

  return <ManagePage>
    <ManagePageHeader title={text("Workflow Recording", "录制 Workflow")} />
    <div className={shared.body}>
      <div className="mx-auto flex w-full max-w-[760px] flex-col gap-6">
        {!available ? (
          <ManageEmptyState compact icon={<Video size={20} />} title={text("Recording needs the desktop app", "录制需要桌面 App")} description={text("Workflow recording runs in the OpenProgram desktop app on macOS.", "录制 Workflow 只能在 macOS 上的 OpenProgram 桌面 App 里使用。")} />
        ) : !enabled ? (
          <ManageEmptyState compact icon={<Video size={20} />} title={text("Workflow Recording is turned off", "录制 Workflow 已关闭")} description={text("Turn it on on the Applications page.", "请在应用页打开它。")} />
        ) : active ? (
          <ManageEmptyState compact icon={<Video size={20} />} title={text("Recording…", "正在录制…")} description={text("Stop it from the recording bar at the top of the screen. The recording then waits in the chat that opened for it.", "在屏幕顶部的录制条上停止。录制随后会出现在为它新开的对话里。")} />
        ) : (
          <div className={styles.recordCard}>
            <p className={styles.lead}>{text(
              "Do the task once, the way you always do it. A new chat opens, and when you stop, the recording waits there for you to send; the agent turns it into a workflow and asks about anything unclear.",
              "按平时的做法把这件事做一遍。开始后会新开一个对话，停止录制时录制内容会出现在那里等你发送；agent 会把它做成 workflow，不清楚的地方会先问你。",
            )}</p>
            <RecordSetupFields setup={setup} />
            <div className="flex justify-end">
              <Button type="button" disabled={setup.busy} onClick={setup.start}>
                {setup.busy ? text("Starting…", "正在开始…") : text("Start recording", "开始录制")}
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  </ManagePage>;
}
