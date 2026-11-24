"use client";

import {
  type FormEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { SolarIcon } from "@/components/solar-icons";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { itemCls } from "@/components/ui/menu/menu-styles";
import { useTranslation } from "@/lib/i18n";
import { wsRequest } from "@/lib/net/ws/ws-request";
import type { MachineInfo } from "@/lib/machines/store";
import { cn } from "@/lib/utils";

interface FolderReply {
  ok: boolean;
  path?: string;
  parent?: string | null;
  folders?: string[];
  error?: string;
}

const LIST_WAIT_MS = 30_000;

function listFolder(machineId: string, path: string): Promise<FolderReply | null> {
  return wsRequest<FolderReply>(
    "list_machine_folder", { machine_id: machineId, path }, "machine_folder",
    { requestId: true }, LIST_WAIT_MS,
  );
}

interface Pending {
  machine: MachineInfo;
  resolve(path: string | null): void;
}

/** Choose a folder on a machine that keeps its own files (an SSH host).
 *  The folder is browsed through the machine's shell (`list_machine_folder`):
 *  it opens at `start`, else where the machine's remote folder setting says, each subfolder
 *  steps in, and the path can be typed. "Open folder" answers the folder as
 *  the machine names it. */
export function useRemoteFolderPicker(): {
  pickRemoteFolder(machine: MachineInfo, start?: string): Promise<string | null>;
  remoteFolderDialog: ReactNode;
  remoteOpen: boolean;
} {
  const { text } = useTranslation();
  const pending = useRef<Pending | null>(null);
  const [machine, setMachine] = useState<MachineInfo | null>(null);
  const [typed, setTyped] = useState("");
  const [listing, setListing] = useState<FolderReply | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const shown = useRef(0);

  const finish = useCallback((path: string | null) => {
    const request = pending.current;
    pending.current = null;
    setMachine(null);
    setListing(null);
    setProblem(null);
    request?.resolve(path);
  }, []);

  useEffect(() => () => {
    pending.current?.resolve(null);
    pending.current = null;
  }, []);

  const show = useCallback(async (target: MachineInfo, path: string): Promise<FolderReply | null> => {
    const ticket = ++shown.current;
    const reply = await listFolder(target.id, path);
    if (ticket !== shown.current) return null;
    if (reply?.ok) {
      setListing(reply);
      setTyped(reply.path ?? path);
      setProblem(null);
    }
    return reply;
  }, []);

  const pickRemoteFolder = useCallback((target: MachineInfo, start = "") => {
    pending.current?.resolve(null);
    return new Promise<string | null>((resolve) => {
      pending.current = { machine: target, resolve };
      setMachine(target);
      setTyped("");
      setListing(null);
      setProblem(null);
      void show(target, start);
    });
  }, [show]);

  async function go(path: string) {
    if (!machine) return;
    const reply = await show(machine, path);
    if (reply && !reply.ok) setProblem(reply.error ?? null);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!machine) return;
    const candidate = typed.trim();
    if (listing?.ok && candidate === listing.path) {
      finish(candidate);
      return;
    }
    const reply = await show(machine, candidate);
    if (reply?.ok && reply.path) finish(reply.path);
    else if (reply) setProblem(reply.error ?? null);
  }

  const name = machine?.name ?? "";
  const remoteFolderDialog = (
    <Dialog open={machine !== null} onOpenChange={(open) => { if (!open) finish(null); }}>
      <DialogContent className="sm:max-w-[560px]">
        <form className="grid gap-4" onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>{text(`Open folder on ${name}`, `打开 ${name} 上的文件夹`)}</DialogTitle>
            <DialogDescription>
              {text(
                `The project's conversations run on ${name}, in this folder.`,
                `这个项目的会话在 ${name} 上的这个文件夹里运行。`,
              )}
            </DialogDescription>
          </DialogHeader>
          <input
            autoFocus
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            onKeyDown={(event) => {
              // Tab steps into the typed folder, as a shell completes one.
              if (event.key === "Tab" && typed.trim() && typed.trim() !== listing?.path) {
                event.preventDefault();
                void go(typed.trim());
              }
            }}
            placeholder="/home/user/project"
            className="h-(--ui-button-h) w-full rounded-md border border-border bg-background px-3 font-mono text-fs-base text-foreground outline-none focus:border-[color:var(--accent-blue)]"
            aria-invalid={problem ? true : undefined}
            aria-label={text("Folder on the device", "设备上的文件夹")}
          />
          <div className="-mx-1 flex h-[280px] flex-col overflow-y-auto px-1" data-remote-folders="">
            {listing?.ok && listing.parent ? (
              <div className={itemCls(false)} onClick={() => void go(listing.parent ?? "/")}>
                <SolarIcon name="folder" size={16} className="opacity-70" aria-hidden="true" />
                <span className="flex-1 truncate">..</span>
              </div>
            ) : null}
            {(listing?.ok ? listing.folders ?? [] : []).map((folder) => (
              <div
                key={folder}
                className={cn(itemCls(false), folder.startsWith(".") && "text-text-muted")}
                onClick={() => void go(`${(listing?.path ?? "").replace(/\/$/, "")}/${folder}`)}
              >
                <SolarIcon name="folder" size={16} className="opacity-70" aria-hidden="true" />
                <span className="flex-1 truncate">{folder}</span>
              </div>
            ))}
          </div>
          {problem ? (
            <p className="text-fs-base text-destructive" role="alert">{problem}</p>
          ) : null}
          <DialogFooter>
            <Button type="button" variant="elevated" onClick={() => finish(null)}>
              {text("Cancel", "取消")}
            </Button>
            <Button type="submit" disabled={!typed.trim()}>
              {text("Open folder", "打开文件夹")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );

  return { pickRemoteFolder, remoteFolderDialog, remoteOpen: machine !== null };
}
