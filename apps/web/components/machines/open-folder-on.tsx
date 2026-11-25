"use client";

import { type ReactNode, useCallback, useEffect } from "react";

import { MachineIcon, useMachineName } from "@/components/machines/machine-icon";
import { useRemoteFolderPicker } from "@/components/machines/remote-folder-picker";
import { useFolderPicker } from "@/components/ui/folder-picker";
import { MENU_SEPARATOR, itemCls } from "@/components/ui/menu/menu-styles";
import { useTranslation } from "@/lib/i18n";
import {
  LOCAL_ID,
  ensureMachines,
  environmentMachines,
  findMachine,
  machineKeepsFiles,
  machineOnline,
  useMachines,
  type MachineInfo,
} from "@/lib/machines/store";
import { cn } from "@/lib/utils";

/** A folder chosen for a new project: where it is, and the machine it is on. */
export interface FolderOnMachine {
  path: string;
  machineId: string;
}

/** Choosing a project's folder once its machine is picked. This computer,
 *  and a machine that reaches this computer's folders (Docker mounts them),
 *  open the existing folder picker; a machine that keeps its own files (an
 *  SSH host) opens the folder browser that reads it through its shell. */
export function useOpenFolderOn(): {
  openOn(machine: MachineInfo): Promise<FolderOnMachine | null>;
  pickFolder(start?: string): Promise<string | null>;
  dialogs: ReactNode;
  dialogOpen: boolean;
} {
  const { pickFolder, folderPickerDialog, manualOpen } = useFolderPicker();
  const { pickRemoteFolder, remoteFolderDialog, remoteOpen } = useRemoteFolderPicker();
  const openOn = useCallback(async (machine: MachineInfo) => {
    const path = machineKeepsFiles(machine) ? await pickRemoteFolder(machine) : await pickFolder();
    return path ? { path, machineId: machine.id } : null;
  }, [pickFolder, pickRemoteFolder]);
  return {
    openOn,
    pickFolder,
    dialogs: <>{folderPickerDialog}{remoteFolderDialog}</>,
    dialogOpen: manualOpen || remoteOpen,
  };
}

/** Choosing another folder for a project that is already on a machine (a
 *  source folder, a conversation's working folder): on an SSH host it is
 *  a folder there, browsed through its shell; anywhere else this
 *  computer's folder picker. */
export function useFolderPickerOn(machineId: string | null | undefined): {
  pickFolder(start?: string): Promise<string | null>;
  dialogs: ReactNode;
  dialogOpen: boolean;
} {
  const items = useMachines((s) => s.items);
  const { pickFolder: pickHere, folderPickerDialog, manualOpen } = useFolderPicker();
  const { pickRemoteFolder, remoteFolderDialog, remoteOpen } = useRemoteFolderPicker();
  const remote = !!machineId && machineId !== LOCAL_ID;
  useEffect(() => {
    if (remote) void ensureMachines();
  }, [remote]);
  const machine = remote ? findMachine(items, machineId) : null;
  const keepsFiles = machineKeepsFiles(machine);
  const pickFolder = useCallback(async (start?: string) => {
    if (keepsFiles && machine) return pickRemoteFolder(machine, start ?? "");
    // A machine no longer configured has no folders to choose from.
    if (remote && !machine) return null;
    return pickHere(start);
  }, [keepsFiles, machine, pickHere, pickRemoteFolder, remote]);
  return {
    pickFolder,
    dialogs: <>{folderPickerDialog}{remoteFolderDialog}</>,
    dialogOpen: manualOpen || remoteOpen,
  };
}

/** The machines a project can be on, this computer first, each with its
 *  icon and connection dot; one that is not connected is greyed and not
 *  chosen. "Add device…" goes to Settings > Devices. */
export function MachineChoiceRows({
  onPick,
  onAddMachine,
}: {
  onPick(machine: MachineInfo): void;
  onAddMachine(): void;
}) {
  const { text } = useTranslation();
  const items = useMachines((s) => s.items);
  const machineName = useMachineName();
  return (
    <>
      {environmentMachines(items).map((machine) => {
        const offline = machine.id !== LOCAL_ID && !machineOnline(machine);
        return (
          <div
            key={machine.id}
            className={cn(itemCls(false), offline && "pointer-events-none opacity-50")}
            aria-disabled={offline || undefined}
            data-machine-choice={machine.id}
            onClick={() => { if (!offline) onPick(machine); }}
          >
            {machine.id === LOCAL_ID ? null : <MachineIcon machine={machine} />}
            <span className="flex-1 truncate">{machineName(machine)}</span>
          </div>
        );
      })}
      <div className={MENU_SEPARATOR} />
      <a
        href="/settings/machines"
        onClick={onAddMachine}
        className={cn(itemCls(false), "no-underline text-text-muted")}
      >
        <span className="flex-1 truncate">{text("Add device…", "添加设备…")}</span>
      </a>
    </>
  );
}
