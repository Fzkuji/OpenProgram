"use client";

/**
 * A project's … button on the phone, at the right of its bar, as Notes and
 * Files put one on a folder. It opens iOS's own menu in the phone app and
 * Framework7's action sheet in a browser. The menu names where the project
 * is (its folder and computer) and offers what the desktop sidebar's
 * project menu offers that fits a phone: Pin, Rename and Archive chats
 * (project-menu.tsx); the work goes through the same requests.
 */

import { useRef, useState } from "react";
import { MoreHorizontal } from "lucide-react";
import { Actions, ActionsButton, ActionsGroup, ActionsLabel, Link, f7 } from "framework7-react";

import { useTranslation } from "@/lib/i18n";
import { LOCAL_ID, useMachines } from "@/lib/machines/store";
import { mobileBridge } from "@/lib/mobile/bridge";
import { nativeMenus, openNativeMenu } from "@/lib/mobile/native-menu";
import { wsRequest } from "@/lib/net/ws/ws-request";
import { pinnedProjectIds, setProjectPinned, useRecentsView } from "@/lib/prefs/recents-view";
import type { ProjectGroupSource } from "@/lib/projects/project-groups";

import { PhonePortal } from "./phone-portal";

export function PhoneProjectMenu({ project, slot }: { project: ProjectGroupSource; slot?: string }) {
  const { text } = useTranslation();
  const view = useRecentsView();
  const machines = useMachines((s) => s.items);
  const button = useRef<HTMLSpanElement>(null);
  const [sheet, setSheet] = useState(false);

  const pinned = pinnedProjectIds(view, project.is_default ? project.id : null).includes(project.id);
  const machineId = project.is_default ? LOCAL_ID : project.machine_id || LOCAL_ID;
  const computer = machineId === LOCAL_ID
    ? mobileBridge()?.computer?.name || text("This computer", "这台电脑")
    : machines.find((machine) => machine.id === machineId)?.name || machineId;

  const togglePin = () => setProjectPinned(project.id, !pinned, project.is_default);
  const failed = (error: unknown) => f7.dialog.alert(String(error instanceof Error ? error.message : error), text("Project action failed", "项目操作失败"));
  const rename = () => f7.dialog.prompt(
    text("A new name for this project", "给这个项目起个新名字"),
    text("Rename project", "重命名项目"),
    async (value) => {
      const name = String(value ?? "").trim();
      if (!name || name === project.name) return;
      try {
        const result = await wsRequest<{ ok: boolean; error?: string }>("update_project", { project_id: project.id, patch: { name } }, "project_updated");
        if (!result?.ok) throw new Error(result?.error || text("Could not save project", "无法保存项目"));
        window.dispatchEvent(new Event("project-changed"));
      } catch (error) { failed(error); }
    },
    undefined,
    project.name,
  );
  const archive = () => f7.dialog.create({
    title: text("Archive chats", "归档聊天"),
    text: text(
      `Archive every chat in "${project.name}"? They stay under the Archived filter, where you can unarchive them.`,
      `归档「${project.name}」里的所有对话？它们仍在“已归档”里，可以取消归档。`,
    ),
    buttons: [
      { text: text("Cancel", "取消") },
      { text: text("Archive", "归档"), strong: true, onClick: async () => {
        try {
          const result = await wsRequest<{ ok: boolean; project_id: string; error?: string }>(
            "archive_project_chats", { project_id: project.id }, "archive_project_chats_result",
            (data) => data.project_id === project.id, 130000,
          );
          if (!result?.ok) throw new Error(result?.error || text("No confirmation received.", "未收到确认。"));
          window.dispatchEvent(new Event("project-changed"));
        } catch (error) { failed(error); }
      } },
    ],
  }).open();

  function open() {
    // The span has no box of its own: the menu hangs from the button.
    const anchor = button.current?.querySelector(".link");
    if (nativeMenus() && anchor) {
      openNativeMenu(anchor, [
        { title: "", inline: true, children: [
          { title: computer, subtitle: project.path, icon: "folder", disabled: true },
        ] },
        { title: "", inline: true, children: [
          { title: pinned ? text("Unpin", "取消置顶") : text("Pin", "置顶"), icon: pinned ? "pin.slash" : "pin", run: togglePin },
          { title: text("Rename", "重命名"), icon: "pencil", run: rename },
        ] },
        { title: text("Archive chats", "归档聊天"), icon: "archivebox", run: archive },
      ]);
      return;
    }
    setSheet(true);
  }

  return (
    <span slot={slot} ref={button} style={{ display: "contents" }}>
      <Link iconOnly aria-label={text(`Options for ${project.name}`, `${project.name} 的选项`)} onClick={open}>
        <MoreHorizontal size={22} aria-hidden="true" />
      </Link>
      <PhonePortal>
        <Actions opened={sheet} onActionsClosed={() => setSheet(false)}>
          <ActionsGroup>
            <ActionsLabel>{`${computer} · ${project.path}`}</ActionsLabel>
            <ActionsButton onClick={togglePin}>{pinned ? text("Unpin", "取消置顶") : text("Pin", "置顶")}</ActionsButton>
            <ActionsButton onClick={rename}>{text("Rename", "重命名")}</ActionsButton>
            <ActionsButton onClick={archive}>{text("Archive chats", "归档聊天")}</ActionsButton>
          </ActionsGroup>
          <ActionsGroup>
            <ActionsButton strong>{text("Cancel", "取消")}</ActionsButton>
          </ActionsGroup>
        </Actions>
      </PhonePortal>
    </span>
  );
}
